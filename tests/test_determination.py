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


# --- physician-OON vs payer-OON (group TIN contracted or not) ---

def test_oon_group_contracted_is_physician_oon():
    # the clinic's TIN IS contracted with the payer, but this physician isn't in-network -> Physician OON
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=False, group_contracted=True)
    assert d.code == "PHYSICIAN_OUT_OF_NETWORK"
    assert "physician" in d.label.lower()


def test_physician_oon_takes_precedence_over_benefits():
    # group contracted -> the client labels it Physician OON regardless of the plan's OON tier
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=True, group_contracted=True)
    assert d.code == "PHYSICIAN_OUT_OF_NETWORK"


def test_oon_group_not_contracted_is_payer_oon():
    # no group contract at all -> payer-level OON; reason names the payer-level gap
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=False, group_contracted=False)
    assert d.code == "OUT_OF_NETWORK"
    assert "payer" in d.reason.lower()


def test_oon_group_not_contracted_but_plan_pays_is_oon_w_benefits():
    # payer OON but the plan still pays out-of-network -> OON w/ Benefits (not physician OON)
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=True, group_contracted=False)
    assert d.code == "OUT_OF_NETWORK_WITH_BENEFITS"


def test_group_contracted_none_keeps_prior_behavior():
    # unknown group status -> unchanged from before (plain OON)
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, out_of_network_benefits=False, group_contracted=None)
    assert d.code == "OUT_OF_NETWORK"


def test_in_network_ignores_group_contracted():
    d = final_determination(NetworkStatus.IN_NETWORK, out_of_network_benefits=None, group_contracted=True)
    assert d.code == "IN_NETWORK"


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


# --- plan-type OON capability (fills a SILENT 271, never overrides a definite one) ---

def test_silent_271_filled_by_ppo_capability():
    # 271 gave no OON tier (None); the plan is a PPO -> plan type fills the gap -> OON w/ Benefits
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, None, plan_oon_capability=True)
    assert d.code == "OUT_OF_NETWORK_WITH_BENEFITS"


def test_silent_271_filled_by_hmo_capability():
    # 271 silent; the plan is a pure HMO (no routine OON) -> plain OON
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, None, plan_oon_capability=False)
    assert d.code == "OUT_OF_NETWORK"


def test_definite_271_true_not_overridden_by_hmo_capability():
    # the live 271 definitively shows OON benefits; capability(False) must NOT override it (demo-safe)
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, True, plan_oon_capability=False)
    assert d.code == "OUT_OF_NETWORK_WITH_BENEFITS"  # 271 wins


def test_definite_271_false_not_overridden_by_ppo_capability():
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, False, plan_oon_capability=True)
    assert d.code == "OUT_OF_NETWORK"  # 271 wins


def test_capability_none_is_exactly_prior_behavior():
    assert final_determination(NetworkStatus.OUT_OF_NETWORK, True, plan_oon_capability=None).code \
        == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert final_determination(NetworkStatus.OUT_OF_NETWORK, None, plan_oon_capability=None).code \
        == "OUT_OF_NETWORK"


def test_physician_oon_precedence_unaffected_by_capability():
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, None, group_contracted=True, plan_oon_capability=True)
    assert d.code == "PHYSICIAN_OUT_OF_NETWORK"


def test_reason_flags_when_plan_type_filled_the_tier():
    # when the tier came from plan type (271 silent), the reason must say so — auditability
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, None, plan_oon_capability=True)
    assert "plan type" in d.reason.lower()
    # when the 271 spoke, no such inference note
    d2 = final_determination(NetworkStatus.OUT_OF_NETWORK, True, plan_oon_capability=True)
    assert "plan type" not in d2.reason.lower()
