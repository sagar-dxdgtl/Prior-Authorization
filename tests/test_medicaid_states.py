"""Per-state Medicaid enrollment sources.

`medicaid_enrollment` backs a **decisive negative**: a successful lookup that finds no match makes
`enrollment_negative` assert OUT_OF_NETWORK, because you cannot be in a Medicaid MCO's network for
a programme you are not enrolled in. That is the strongest claim this codebase makes from a public
source, so a state only earns a place here when its source can carry it. Two properties are
required, and both directions of failure are live-verified below:

  * **Identifier-grade.** The response must contain the NPI so the match can be confirmed
    client-side. A server-side NPI filter whose output we cannot check is not evidence — it is
    trust. This is what disqualifies Kansas (rows come back with `NPI: null`) and Texas (the TMHP
    lookup returns HTML with the NPI encoded), even though both endpoints answer fine.
  * **Authoritative for the STATE.** It must be the state's own enrolled-provider file, not an
    MCO's network directory. Absence from one MCO's network says nothing about enrollment, and
    reading it as a negative would be the same absence-means-out error the directory adapters
    already had. This is what disqualifies the Centene/Molina sources (WI, IA).

Anything else stays deliberately unwired and returns *undetermined* — never a guess, and never a
false OON. Unwired states now say WHY, so the next person does not re-derive it.
"""

import pytest

from network_probe.domain.enrollment import medicaid_enrollment

# --- Illinois: HFS, the state Medicaid agency's own individual-provider directory ---------------
# Live 2026-07-28: NPI 1588744650 (Petermann, the Meridian IL row) -> count 1, name PETERMANN.
_IL_HIT = {
    "result": [
        {
            "ProviderId": 26081,
            "NPI": "1588744650",
            "LastName": "PETERMANN KEVIN L",
            "TheName": "PETERMANN, KEVIN L",
        }
    ],
    "count": 1,
}
_IL_MISS = {"result": [], "count": 0}

# --- Maine: the MaineCare provider directory, a plain FHIR R4 server (28,945 practitioners) -----
_ME_HIT = {
    "resourceType": "Bundle",
    "type": "searchset",
    "entry": [
        {
            "resource": {
                "resourceType": "Practitioner",
                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1588744650"}],
                "name": [{"family": "Petermann", "given": ["Kevin"]}],
            }
        }
    ],
}
_ME_MISS = {"resourceType": "Bundle", "type": "searchset"}  # note: no `entry` key at all


class _Client:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get_json(self, url, headers=None):
        self.calls.append(("GET", url))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def post_json(self, url, content, headers=None):
        self.calls.append(("POST", url, content))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


# ---- Illinois ------------------------------------------------------------------------------------

def test_illinois_enrolled_provider():
    r = medicaid_enrollment("1588744650", "IL", client=_Client(_IL_HIT))
    assert r.enrolled is True
    assert r.program == "medicaid-IL"


def test_illinois_absent_provider_is_a_decisive_negative():
    r = medicaid_enrollment("1000000004", "IL", client=_Client(_IL_MISS))
    assert r.enrolled is False


def test_illinois_sends_the_npi_in_the_search_body():
    c = _Client(_IL_HIT)
    medicaid_enrollment("1588744650", "IL", client=c)
    assert "1588744650" in c.calls[0][2]


def test_illinois_ignores_rows_for_a_different_npi():
    """HFS matches with `contains`, so a short or shared digit run can drag in other people."""
    other = {"result": [{"ProviderId": 1, "NPI": "9999999999", "TheName": "SOMEONE, ELSE"}], "count": 1}
    r = medicaid_enrollment("1588744650", "IL", client=_Client(other))
    assert r.enrolled is False


# ---- Maine ---------------------------------------------------------------------------------------

def test_maine_enrolled_provider():
    r = medicaid_enrollment("1588744650", "ME", client=_Client(_ME_HIT))
    assert r.enrolled is True
    assert r.program == "medicaid-ME"


def test_maine_absent_provider_is_a_decisive_negative():
    """An empty FHIR searchset omits `entry` entirely rather than sending an empty list."""
    r = medicaid_enrollment("1000000004", "ME", client=_Client(_ME_MISS))
    assert r.enrolled is False


def test_maine_ignores_a_bundle_entry_whose_npi_does_not_match():
    other = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Practitioner",
                    "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "9999999999"}],
                }
            }
        ],
    }
    r = medicaid_enrollment("1588744650", "ME", client=_Client(other))
    assert r.enrolled is False


def test_maine_ignores_non_npi_identifiers():
    """Practitioners also carry state licence numbers; only the us-npi system is the NPI."""
    licence_only = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Practitioner",
                    "identifier": [{"system": "http://maine.gov/license", "value": "1588744650"}],
                }
            }
        ],
    }
    r = medicaid_enrollment("1588744650", "ME", client=_Client(licence_only))
    assert r.enrolled is False


def test_maine_treats_an_operation_outcome_as_undetermined_not_absent():
    """A FHIR server reports errors as a 200 OperationOutcome. Reading that as 'no match' would
    turn every server-side error into a confident OON."""
    oo = {"resourceType": "OperationOutcome", "issue": [{"severity": "error"}]}
    r = medicaid_enrollment("1588744650", "ME", client=_Client(oo))
    assert r.enrolled is None


# ---- the shared safety rules --------------------------------------------------------------------

@pytest.mark.parametrize("state", ["IL", "ME"])
def test_a_transport_failure_is_undetermined_never_a_false_oon(state):
    r = medicaid_enrollment("1588744650", state, client=_Client(RuntimeError("network down")))
    assert r.enrolled is None


@pytest.mark.parametrize("state", ["il", "Il", "iL"])
def test_state_codes_are_case_insensitive(state):
    assert medicaid_enrollment("1588744650", state, client=_Client(_IL_HIT)).enrolled is True


def test_an_unwired_state_is_undetermined_and_says_why_it_is_unwired():
    """Kansas answers, but its rows carry `NPI: null` — a match we cannot confirm is not evidence."""
    r = medicaid_enrollment("1588744650", "KS", client=_Client(_IL_HIT))
    assert r.enrolled is None
    assert "npi" in r.detail.lower()


def test_a_state_with_no_source_at_all_is_still_undetermined():
    r = medicaid_enrollment("1588744650", "ZZ", client=_Client(_IL_HIT))
    assert r.enrolled is None


def test_an_unwired_state_makes_no_http_call():
    c = _Client(_IL_HIT)
    medicaid_enrollment("1588744650", "KS", client=c)
    assert c.calls == []


@pytest.mark.parametrize("npi", ["", None, "12345", "abcdefghij"])
def test_a_malformed_npi_is_undetermined(npi):
    assert medicaid_enrollment(npi, "IL", client=_Client(_IL_HIT)).enrolled is None
