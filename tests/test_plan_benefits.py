"""Pure logic of the plan-benefit (PBP) layer: the OON capability a record implies, and the
name-match that resolves a member's plan string to a specific PBP plan.

The DB store (PlanBenefitStore) is a thin SQLAlchemy wrapper validated by the live CY2026 ingest;
the demo-critical intelligence lives here in pure functions, so it is tested without a database.

Safety rules encoded:
  * ANY D-SNP → defer (None): dual cost-sharing is member-specific.
  * a filed combined/OON MOOP → True: hard evidence the plan pays OON benefits.
  * name resolution refuses to guess when the top matches span DIFFERENT plan types.
"""

from network_probe.domain.pbp_ingest import PlanBenefitRecord
from network_probe.domain.plan_benefits import best_plan_match, record_oon_capability


def _rec(**kw):
    base = dict(
        contract_number="H1000", pbp_id="001", segment_id="0", year=2026,
        plan_type_code="04", plan_type="ppo", plan_name="Test Plan", org_marketing_name="",
        snp_type_code="", dsnp=False, network_flag="1",
        inn_moop=None, comb_moop_yn="", comb_moop=None, oon_moop_yn="", oon_moop=None,
    )
    base.update(kw)
    return PlanBenefitRecord(**base)


# ---- record_oon_capability ----

def test_ppo_record_has_oon():
    assert record_oon_capability(_rec(plan_type="ppo")) is True


def test_hmo_record_no_oon():
    assert record_oon_capability(_rec(plan_type="hmo")) is False


def test_hmopos_record_defers():
    assert record_oon_capability(_rec(plan_type="hmopos")) is None


def test_combined_moop_proves_oon_even_for_ambiguous_type():
    r = _rec(plan_type="hmopos", comb_moop_yn="1", comb_moop="10000.00")
    assert record_oon_capability(r) is True


def test_any_dsnp_defers_regardless_of_type_or_moop():
    # dual determinations are member-specific — never assert a tier from plan structure/MOOP
    assert record_oon_capability(_rec(plan_type="ppo", dsnp=True)) is None
    assert record_oon_capability(_rec(plan_type="ppo", dsnp=True, comb_moop_yn="1", comb_moop="9000")) is None


# ---- best_plan_match ----

def test_match_by_token_overlap():
    cands = [
        _rec(contract_number="H0321", pbp_id="002", plan_type="hmopos", dsnp=True,
             plan_name="UHC Dual Complete AZ-S001 (HMO-POS D-SNP)"),
        _rec(contract_number="H9999", pbp_id="001", plan_type="ppo",
             plan_name="Cigna Preferred Savings (PPO)"),
    ]
    m = best_plan_match("UHC Dual Complete AZ Member", cands)
    assert m is not None and m.contract_number == "H0321"


def test_no_match_when_no_meaningful_overlap():
    cands = [_rec(plan_name="Humana Gold Plus (HMO)")]
    assert best_plan_match("Cigna True Choice PPO", cands) is None


def test_refuses_to_guess_when_top_matches_span_different_types():
    # "AARP Medicare Advantage FL" overlaps both equally but they are HMO vs PPO → return None
    cands = [
        _rec(contract_number="A", plan_type="hmo", plan_name="AARP Medicare Advantage FL-1 (HMO)"),
        _rec(contract_number="B", plan_type="ppo", plan_name="AARP Medicare Advantage FL-2 (PPO)"),
    ]
    assert best_plan_match("AARP Medicare Advantage FL", cands) is None


def test_refuses_when_same_type_winners_differ_on_dsnp():
    # same plan_type (ppo) but one is a D-SNP → capabilities differ (True vs defer) → must return None,
    # regardless of candidate order (was DB-order-dependent before the capability-aware guard).
    cands = [
        _rec(contract_number="H5", pbp_id="001", plan_type="ppo", dsnp=False,
             plan_name="Aetna Medicare Eagle (PPO)"),
        _rec(contract_number="H5", pbp_id="002", plan_type="ppo", dsnp=True,
             plan_name="Aetna Medicare Eagle Dual (PPO D-SNP)"),
    ]
    assert best_plan_match("Aetna Medicare Eagle", cands) is None
    assert best_plan_match("Aetna Medicare Eagle", list(reversed(cands))) is None


def test_refuses_when_same_type_winners_differ_on_moop_capability():
    # both HMO-POS, but one has a filed combined MOOP (→ True) and one doesn't (→ defer) → defer
    cands = [
        _rec(contract_number="H6", pbp_id="001", plan_type="hmopos", comb_moop_yn="1", comb_moop="9000.00",
             plan_name="Wellcare Choice (HMO-POS)"),
        _rec(contract_number="H6", pbp_id="002", plan_type="hmopos", comb_moop_yn="",
             plan_name="Wellcare Choice (HMO-POS)"),
    ]
    assert best_plan_match("Wellcare Choice", cands) is None


def test_tie_break_is_deterministic():
    # two winners of identical capability → stable pick independent of input order
    a = _rec(contract_number="H7", pbp_id="004", plan_type="hmopos", dsnp=True,
             plan_name="UHC Dual Complete AZ-Y (HMO-POS D-SNP)")
    b = _rec(contract_number="H7", pbp_id="002", plan_type="hmopos", dsnp=True,
             plan_name="UHC Dual Complete AZ-S (HMO-POS D-SNP)")
    m1 = best_plan_match("UHC Dual Complete AZ", [a, b])
    m2 = best_plan_match("UHC Dual Complete AZ", [b, a])
    assert m1 is not None and m1.pbp_id == m2.pbp_id


def test_ties_of_same_type_are_ok():
    # both AZ dual-complete plans are HMO-POS → type is unambiguous → a match is allowed
    cands = [
        _rec(contract_number="H0321", pbp_id="002", plan_type="hmopos", dsnp=True,
             plan_name="UHC Dual Complete AZ-S001 (HMO-POS D-SNP)"),
        _rec(contract_number="H0321", pbp_id="004", plan_type="hmopos", dsnp=True,
             plan_name="UHC Dual Complete AZ-Y001 (HMO-POS D-SNP)"),
    ]
    m = best_plan_match("UHC Dual Complete AZ", cands)
    assert m is not None and m.plan_type == "hmopos"
