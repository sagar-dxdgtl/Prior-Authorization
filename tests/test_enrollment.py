"""Enrollment source (PECOS + state Medicaid) and the LOB-gated negative filter.

Key safety property: only a *confirmed* not-enrolled (successful lookup, no match) is decisive OON;
a failed/unreachable lookup or an unsupported state is None (undetermined), never a false OON.
`enrolled is True` only clears the gate — it never asserts INN on its own.
"""

from network_probe.domain.enrollment import EnrollmentResult, medicaid_enrollment, pecos_enrollment
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.provider_network import enrollment_negative

_HEADERS = ["NPI", "LAST_NAME", "FIRST_NAME", "PARTB", "DME", "HHA", "PMD", "HOSPICE"]


class _MockClient:
    """Returns the metadata payload for the jsonapi call, the data payload otherwise."""

    def __init__(self, meta, data):
        self.meta, self.data = meta, data

    def get_json(self, url, headers=None):
        return self.meta if "jsonapi" in url else self.data


def _pecos_client(rows):
    meta = {"data": [{"id": "DS-LATEST"}]}
    data = {"meta": {"headers": _HEADERS, "data_file_name": "OrderReferring_20260716.csv"}, "data": rows}
    return _MockClient(meta, data)


# ---- PECOS parsing ----

def test_pecos_enrolled_when_flags_yes():
    c = _pecos_client([["1588744650", "PETERMANN", "KEVIN", "Y", "Y", "Y", "Y", "Y"]])
    r = pecos_enrollment("1588744650", client=c)
    assert r.enrolled is True
    assert r.flags.get("partb") == "Y"
    assert r.source_date == "OrderReferring_20260716.csv"


def test_pecos_not_enrolled_when_no_matching_row():
    c = _pecos_client([])  # keyword search returned nothing for this NPI
    r = pecos_enrollment("1000000004", client=c)
    assert r.enrolled is False
    assert "not Medicare-enrolled" in r.detail


def test_pecos_ignores_non_matching_rows():
    # keyword search may return near-matches; only the exact NPI row counts
    c = _pecos_client([["9999999999", "OTHER", "PERSON", "Y", "N", "N", "N", "N"]])
    r = pecos_enrollment("1588744650", client=c)
    assert r.enrolled is False


def test_pecos_lookup_failure_is_undetermined_not_oon():
    class _Boom:
        def get_json(self, url, headers=None):
            raise RuntimeError("network down")

    r = pecos_enrollment("1588744650", client=_Boom())
    assert r.enrolled is None  # never False on a failed lookup


def test_pecos_bad_npi_is_undetermined():
    assert pecos_enrollment("123", client=_pecos_client([])).enrolled is None


# ---- Medicaid (NY reference) + unsupported state ----

def test_medicaid_ny_enrolled():
    c = _MockClient({}, [{"npi": "1588744650", "medicaid_type": "FFS"}])
    r = medicaid_enrollment("1588744650", "NY", client=c)
    assert r.enrolled is True


def test_medicaid_ny_not_enrolled():
    c = _MockClient({}, [])
    r = medicaid_enrollment("1000000004", "NY", client=c)
    assert r.enrolled is False


def test_medicaid_unsupported_state_is_undetermined():
    r = medicaid_enrollment("1588744650", "ZZ")
    assert r.enrolled is None  # no source wired -> can't assert OON


# ---- the LOB-gated negative filter ----

def _q():
    return ProviderQuery(payer="humana-co-denver", plan_hint="Humana Medicare", npi="1801837109", tin="9", state="CO")


def test_medicare_not_enrolled_is_decisive_oon():
    not_enrolled = lambda npi: EnrollmentResult(False, "medicare-pecos", "not enrolled")  # noqa: E731
    v = enrollment_negative(_q(), "medicare", pecos_fn=not_enrolled)
    assert v is not None and v.status == NetworkStatus.OUT_OF_NETWORK
    assert v.source_url == "enrollment"


def test_medicare_enrolled_defers_gate_cleared():
    v = enrollment_negative(_q(), "medicare", pecos_fn=lambda npi: EnrollmentResult(True, "medicare-pecos", "enrolled"))
    assert v is None  # enrolled != INN; fall through to the plan-network sources


def test_medicare_undetermined_defers():
    v = enrollment_negative(_q(), "medicare", pecos_fn=lambda npi: EnrollmentResult(None, "medicare-pecos", "failed"))
    assert v is None


def test_medicaid_not_enrolled_is_oon():
    q = ProviderQuery(payer="meridian-health-il", plan_hint="Meridian", npi="1", tin="2", state="IL")
    not_enrolled = lambda npi, st: EnrollmentResult(False, f"medicaid-{st}", "not enrolled")  # noqa: E731
    v = enrollment_negative(q, "medicaid", medicaid_fn=not_enrolled)
    assert v is not None and v.status == NetworkStatus.OUT_OF_NETWORK


def test_commercial_line_skips_enrollment():
    v = enrollment_negative(_q(), "commercial", pecos_fn=lambda npi: EnrollmentResult(False, "x", "not enrolled"))
    assert v is None  # enrollment filter never applies to commercial


def test_no_npi_skips():
    q = ProviderQuery(payer="p", plan_hint="x", npi=None, tin="2", state="CO")
    assert enrollment_negative(q, "medicare", pecos_fn=lambda npi: EnrollmentResult(False, "x", "n")) is None
