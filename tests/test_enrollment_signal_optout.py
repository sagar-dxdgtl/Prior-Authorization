"""The enrolment display signal must not contradict the verdict it sits next to.

`enrollment_negative` now returns OUT_OF_NETWORK for a provider with a current Medicare opt-out
affidavit. The signal panel reads the *same* NPI from PECOS and would happily print a green
"ENROLLED" beside that OON — an opted-out provider is still enrolled in Medicare, so both
statements are individually true and together they are unreadable. On a screen where staff are
deciding whether to see a patient, a green tick next to a red verdict is worse than no signal.

So the signal consults the opt-out file too, and an affidavit in force takes over the row.
"""

from types import SimpleNamespace

from network_probe.domain.enrollment import ACCEPTS, OPTED_OUT, AssignmentResult, EnrollmentResult
from network_probe.domain.evidence import _enrollment_source
from network_probe.domain.models import ProviderQuery

_Q = ProviderQuery(
    payer="aetna-az-phoenix", plan_hint="Aetna Medicare Prime HMO", npi="1801837109",
    tin="843447602", state="AZ",
)
_RESULT = SimpleNamespace(plan_name="Aetna Medicare Prime HMO")


def _enrolled(npi):
    return EnrollmentResult(True, "medicare-pecos", f"NPI {npi}: Medicare-enrolled (PECOS).")


def _assign(status):
    return lambda npi: AssignmentResult(status, f"NPI {npi}: stub {status}.")


def test_an_opt_out_takes_over_the_row_instead_of_printing_enrolled():
    sig = _enrollment_source(
        _Q, _RESULT, "Medicare Advantage", True,
        pecos_fn=_enrolled, assignment_fn=_assign(OPTED_OUT),
    )
    assert sig["status"] == "OPTED_OUT"
    assert sig["tone"] == "danger"
    assert "opt" in sig["detail"].lower()


def test_a_provider_who_has_not_opted_out_still_reads_as_enrolled():
    sig = _enrollment_source(
        _Q, _RESULT, "Medicare Advantage", True,
        pecos_fn=_enrolled, assignment_fn=_assign(ACCEPTS),
    )
    assert sig["status"] == "ENROLLED"
    assert sig["tone"] == "success"


def test_an_unreachable_opt_out_file_leaves_the_enrolled_row_alone():
    def boom(npi):
        raise RuntimeError("CMS down")

    sig = _enrollment_source(_Q, _RESULT, "Medicare Advantage", True, pecos_fn=_enrolled, assignment_fn=boom)
    assert sig["status"] == "ENROLLED"


def test_a_confirmed_not_enrolled_is_not_overwritten_by_an_opt_out_check():
    """Both are decisive negatives; the enrolment one was already correct and stays."""
    not_enrolled = lambda npi: EnrollmentResult(False, "medicare-pecos", "not in PECOS")  # noqa: E731
    sig = _enrollment_source(
        _Q, _RESULT, "Medicare Advantage", True,
        pecos_fn=not_enrolled, assignment_fn=_assign(OPTED_OUT),
    )
    assert sig["status"] == "NOT_ENROLLED"


def test_a_medicaid_line_does_not_consult_the_opt_out_file():
    calls = []
    q = ProviderQuery(payer="meridian-health-il", plan_hint="Meridian Medicaid", npi="1588744650", state="IL")
    _enrollment_source(
        q, SimpleNamespace(plan_name="Meridian Medicaid"), "Managed Medicaid", True,
        medicaid_fn=lambda npi, st: EnrollmentResult(True, f"medicaid-{st}", "enrolled"),
        assignment_fn=lambda npi: calls.append(npi) or _assign(OPTED_OUT)(npi),
    )
    assert calls == []
