from network_probe.domain.determination import Determination, final_determination
from network_probe.domain.models import NetworkStatus


def test_in_network_is_inn():
    d = final_determination(NetworkStatus.IN_NETWORK, out_of_network_benefits=None)
    assert isinstance(d, Determination) and d.code == "IN_NETWORK"


def test_oon_with_plan_benefits_is_oon_w_benefits():
    # provider OON but the plan pays out-of-network (PPO) -> the client's "OON w/ Benefits"
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=True)
    assert d.code == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert "benefit" in d.label.lower()


def test_oon_without_plan_benefits_is_plain_oon():
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=False)
    assert d.code == "OUT_OF_NETWORK"


def test_oon_unknown_benefits_is_plain_oon():
    # provider is OON; the 271 gave no usable OON tier -> report plain OON, don't invent benefits
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=None)
    assert d.code == "OUT_OF_NETWORK"


def test_review_passthrough():
    d = final_determination(NetworkStatus.REVIEW, out_of_network_benefits=True)
    assert d.code == "REVIEW"


def test_unknown_provider_status():
    d = final_determination(NetworkStatus.UNKNOWN, out_of_network_benefits=None)
    assert d.code == "UNKNOWN"


def test_unknown_provider_but_plan_pays_oon_notes_the_benefit():
    # can't confirm the provider, but the 271 shows the plan has OON benefits -> reason surfaces it
    d = final_determination(NetworkStatus.UNKNOWN, out_of_network_benefits=True)
    assert d.code == "UNKNOWN" and "out-of-network" in d.reason.lower()


def test_determination_serializes():
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=True)
    js = d.to_dict()
    assert js["code"] == "OUT_OF_NETWORK_WITH_BENEFITS" and "label" in js and "reason" in js


def test_check_eligibility_produces_oon_with_benefits_end_to_end(monkeypatch):
    # End-to-end (no DB / no live calls): 271 says active + plan pays OON; credentialing says the
    # provider is OON -> the final determination is "OON w/ Benefits" (Desormeaux's real case).
    from network_probe.domain import credentialing, eligibility
    from network_probe.domain.benefits import EligibilityResult
    from network_probe.domain.models import ProviderQuery

    class _FakeStedi:
        def check(self, q):
            return EligibilityResult(
                coverage_active=True, plan_name="AARP MA PPO", group=None, coverage_dates={},
                network_status=NetworkStatus.UNKNOWN, benefits=[], pcp_required=None, prior_auth_required=None,
                referral_required=None, cob=None, network_verdict=None, corroboration=[],
                source_audit={"source": "fake"}, out_of_network_benefits=True,
            )

    class _FakeCat:
        def resolve(self, payer):
            return None

    cred = credentialing.CredentialingMatrix(records=[
        credentialing.CredentialRecord("uhc-fl", "1760457477", "463812940", False, plan="MA", source="test")])
    monkeypatch.setattr(credentialing, "default_credentialing", lambda: cred)

    q = ProviderQuery(payer="uhc-fl", plan_hint="AARP Medicare Advantage", npi="1760457477", tin="463812940")
    res = eligibility.check_eligibility(q, catalogue=_FakeCat(), stedi=_FakeStedi(), tenant_id=None)
    assert res.determination["code"] == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert res.to_dict()["determination"]["code"] == "OUT_OF_NETWORK_WITH_BENEFITS"
