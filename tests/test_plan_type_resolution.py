"""resolve_plan_type: fuse the plan string and the PBP store into the OON-tier signal the
determination consumes. Precedence: authoritative PBP match → explicit string token → unknown.
Only Medicare/Dual lines are in scope (CMS PBP is Medicare-only); commercial → N/A.
"""

from network_probe.domain.pbp_ingest import PlanBenefitRecord
from network_probe.domain.plan_benefits import PlanTypeResolution, resolve_plan_type


class _FakeStore:
    def __init__(self, rec):
        self._rec = rec

    def resolve(self, plan_hint, contract=None, year=None):
        return self._rec


def _rec(**kw):
    base = dict(
        contract_number="H1000", pbp_id="001", segment_id="0", year=2026,
        plan_type_code="04", plan_type="ppo", plan_name="Test Plan", org_marketing_name="",
        snp_type_code="", dsnp=False, network_flag="1",
        inn_moop=None, comb_moop_yn="", comb_moop=None, oon_moop_yn="", oon_moop=None,
    )
    base.update(kw)
    return PlanBenefitRecord(**base)


def test_resolves_via_pbp_when_store_matches():
    rec = _rec(plan_type="ppo", plan_name="AARP MA FL (PPO)", comb_moop_yn="1", comb_moop="10000.00")
    r = resolve_plan_type("AARP Medicare Advantage", benefit_type="MA", store=_FakeStore(rec))
    assert isinstance(r, PlanTypeResolution)
    assert r.source == "pbp" and r.plan_type == "ppo" and r.capability is True and r.record is rec


def test_falls_back_to_string_token_when_no_pbp_match():
    r = resolve_plan_type("Humana Gold Plus (HMO)", benefit_type="Medicare", store=_FakeStore(None))
    assert r.source == "plan-string" and r.plan_type == "hmo" and r.capability is False


def test_unknown_when_no_token_and_no_pbp():
    r = resolve_plan_type("UHC Medicare Dual Complete AZMCARE", benefit_type="Dual", store=_FakeStore(None))
    assert r.plan_type == "unknown" and r.capability is None and r.source == "none"


def test_commercial_line_is_na():
    # CMS PBP is Medicare-only — never consulted for a commercial line
    r = resolve_plan_type("Ambetter ACA PPO", benefit_type="ACA Commercial", store=_FakeStore(_rec()))
    assert r.source == "n/a" and r.capability is None


def test_parses_h_contract_from_plan_string():
    r = resolve_plan_type("Humana Gold Plus HMOPOS H0028 D-SNP", benefit_type="Dual", store=_FakeStore(None))
    assert r.contract == "H0028"


def test_no_store_skips_pbp_and_uses_string():
    # store=None (e.g. test env / live disabled) → no DB read, string token only
    r = resolve_plan_type("Wellcare (PPO)", benefit_type="Medicare Advantage", store=None)
    assert r.source == "plan-string" and r.plan_type == "ppo" and r.capability is True
