"""The evidence a check gathered must actually reach the determination.

Found live on 2026-08-03 (Randall C Orem, NPI 1497741409, UnitedHealthcare FL South Florida): the
Network Finding tab correctly reported the provider in the payer's directory under 11 networks, yet
the Determination tile read "Not yet established" — the blank UNKNOWN that `_best_available` and
`test_best_available.py` exist to prevent.

Root cause: `check_eligibility` built the evidence dict inside one `try:` whose tail called an
unbound name (`_catalogue_row`, defined in domain/service.py and never imported here). The NameError
was caught by the block's own `except Exception: ev = {}`, discarding the directory evidence that had
already been collected two lines earlier. It fired on EVERY check, so no determination ever saw
evidence at all.

Two independent regressions, because the fix has two halves:
  * the payer-label lookup must resolve, and
  * evidence already gathered must survive a failure in a later, optional enrichment step.
"""

from network_probe.domain import eligibility as elig
from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery


class FakeCat:
    """Minimal catalogue: resolve() for the payer id, row() for the display label."""

    def resolve(self, key):
        class P:
            stedi_payer_id = "UHC"
            benefit_type = None

        return P()


class FakeStedi:
    def __init__(self, result):
        self.result = result

    def check(self, q):
        return self.result


def _res(status):
    return EligibilityResult(
        coverage_active=True,
        plan_name="LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO",
        group="82135",
        coverage_dates={},
        network_status=status,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=None,
        corroboration=[],
        source_audit={},
    )


def _orem_verdict():
    """The real shape: in the directory, 11 networks, none matching the plan hint -> UNKNOWN."""
    return NetworkVerdict(
        status=NetworkStatus.UNKNOWN,
        matched_provider={
            "npi": "1497741409",
            "name": "Randall C Orem",
            "networks": [
                "Choice Plus", "NexusACO RP", "Select HMO", "Charter HMO", "Doctors Plan",
                "Pacific Nephrology Medical Grp", "Charter Plus", "Select",
                "NexusACO OA HMO", "Core Essential", "Navigate",
            ],
        },
        plan_or_network_checked="unitedhealthcare-fl-south-florida",
        source_url="https://example.invalid/fhir",
        confidence="medium",
        notes="in the directory but none of their 11 networks confidently matched",
        corroboration=[],
    )


def _run(monkeypatch, verdict):
    monkeypatch.setattr(elig, "check_network", lambda q, **k: verdict)
    return elig.check_eligibility(
        ProviderQuery(payer="unitedhealthcare-fl-south-florida", npi="1497741409", plan_hint=""),
        catalogue=FakeCat(),
        stedi=FakeStedi(_res(NetworkStatus.UNKNOWN)),
    )


def test_directory_evidence_survives_into_the_determination(monkeypatch):
    """The Orem case: 11 directory networks must produce a reading, not a blank UNKNOWN."""
    r = _run(monkeypatch, _orem_verdict())
    assert r.determination["code"] == "UNKNOWN"  # the verdict itself is unchanged
    assert r.determination["provisional"] == "LIKELY_IN_NETWORK"
    assert "11 network" in r.determination["basis"]
    assert r.determination["label"] != "Not yet established"


def test_a_failing_enrichment_step_does_not_discard_evidence(monkeypatch):
    """The bug's actual shape: an exception in the optional payer-label lookup wiped `ev` wholesale.

    Evidence already gathered is not invalidated by a later, unrelated failure — so a broken
    catalogue must cost the determination its payer *label*, never its directory *finding*.
    """

    class ExplodingCat(FakeCat):
        def row(self, key):
            raise RuntimeError("catalogue unavailable")

    monkeypatch.setattr(elig, "check_network", lambda q, **k: _orem_verdict())
    r = elig.check_eligibility(
        ProviderQuery(payer="unitedhealthcare-fl-south-florida", npi="1497741409", plan_hint=""),
        catalogue=ExplodingCat(),
        stedi=FakeStedi(_res(NetworkStatus.UNKNOWN)),
    )
    assert r.determination["provisional"] == "LIKELY_IN_NETWORK"


def test_plan_oon_capability_is_exposed_to_the_caller(monkeypatch):
    """The portal capture reconciles with `plan_oon_capability`, so the response has to carry it.

    Without it the UI cannot forward the signal, and a member whose 271 is silent on the OON tier
    reconciles to plain "Out-of-Network" where the plan type says "Out-of-Network (with benefits)".
    """
    r = _run(monkeypatch, _orem_verdict())
    assert "plan_oon_capability" in r.to_dict()
