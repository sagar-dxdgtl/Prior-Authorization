"""Parser for CMS CY2026 PBP Benefits files (tab-delimited, header row).

Joins Section A (plan attributes: type, name, SNP) with Section D (plan-level MOOP) on the natural
key (contract, plan id, segment). Column names and the tab layout mirror the real file exactly, so
this fixture is a faithful miniature of pbp_section_a.txt / pbp_section_d.txt.
"""

from network_probe.domain.line_of_business import plan_type_from_pbp_code
from network_probe.domain.pbp_ingest import PlanBenefitRecord, iter_plan_benefits

_SECTION_A = (
    "pbp_a_hnumber\tpbp_a_plan_identifier\tsegment_id\tpbp_a_plan_type\tpbp_a_plan_name\t"
    "pbp_a_org_marketing_name\tpbp_a_special_need_plan_type\tpbp_a_network_flag\n"
    "H1000\t001\t0\t01\tTest HMO Plan\tTestOrg\t\t1\n"
    "H2000\t001\t0\t04\tTest PPO Plan\tTestOrg\t\t1\n"
    "H3000\t002\t0\t02\tTest Dual Complete (HMO-POS D-SNP)\tTestOrg\t3\t2\n"
)
_SECTION_D = (
    "pbp_a_hnumber\tpbp_a_plan_identifier\tsegment_id\tpbp_d_out_pocket_amt_yn\tpbp_d_out_pocket_amt\t"
    "pbp_d_comb_max_enr_amt_yn\tpbp_d_comb_max_enr_amt\tpbp_d_oon_max_enr_oopc_yn\tpbp_d_oon_max_enr_oopc_amt\n"
    "H1000\t001\t0\t1\t5500.00\t\t\t\t\n"
    "H2000\t001\t0\t1\t6700.00\t1\t10000.00\t2\t\n"
    "H3000\t002\t0\t1\t9250.00\t2\t\t2\t\n"
)


def _write(tmp_path):
    a = tmp_path / "pbp_section_a.txt"
    d = tmp_path / "pbp_section_d.txt"
    a.write_text(_SECTION_A)
    d.write_text(_SECTION_D)
    return a, d


def _by_key(recs):
    return {(r.contract_number, r.pbp_id): r for r in recs}


def test_plan_type_from_pbp_code():
    assert plan_type_from_pbp_code("01") == "hmo"
    assert plan_type_from_pbp_code("02") == "hmopos"
    assert plan_type_from_pbp_code("04") == "ppo"
    assert plan_type_from_pbp_code("31") == "ppo"  # regional PPO folds to ppo
    assert plan_type_from_pbp_code("09") == "pffs"
    assert plan_type_from_pbp_code("29") == "unknown"  # PDP — no medical network claim
    assert plan_type_from_pbp_code("") == "unknown"


def test_parses_three_plans_joined(tmp_path):
    a, d = _write(tmp_path)
    recs = list(iter_plan_benefits(a, d, year=2026))
    assert len(recs) == 3
    assert all(isinstance(r, PlanBenefitRecord) and r.year == 2026 for r in recs)


def test_hmo_record_fields(tmp_path):
    a, d = _write(tmp_path)
    r = _by_key(iter_plan_benefits(a, d, year=2026))[("H1000", "001")]
    assert r.plan_type == "hmo" and r.plan_type_code == "01"
    assert r.dsnp is False
    assert r.inn_moop == "5500.00"
    assert r.comb_moop is None and r.oon_moop is None
    assert r.plan_name == "Test HMO Plan"


def test_ppo_record_has_combined_moop(tmp_path):
    a, d = _write(tmp_path)
    r = _by_key(iter_plan_benefits(a, d, year=2026))[("H2000", "001")]
    assert r.plan_type == "ppo"
    assert r.comb_moop == "10000.00" and r.comb_moop_yn == "1"
    assert r.inn_moop == "6700.00"


def test_hmopos_dsnp_record(tmp_path):
    a, d = _write(tmp_path)
    r = _by_key(iter_plan_benefits(a, d, year=2026))[("H3000", "002")]
    assert r.plan_type == "hmopos"
    assert r.dsnp is True and r.snp_type_code == "3"
    # explicit "no combined OON MOOP" (the real Birenbaum-plan signal)
    assert r.comb_moop_yn == "2" and r.comb_moop is None


def test_plan_present_in_a_but_absent_in_d_still_emitted(tmp_path):
    # Section A is the plan universe; a plan with no Section D row still yields a record (MOOPs None).
    a = tmp_path / "a.txt"
    d = tmp_path / "d.txt"
    a.write_text(_SECTION_A)
    d.write_text(
        "pbp_a_hnumber\tpbp_a_plan_identifier\tsegment_id\tpbp_d_out_pocket_amt_yn\tpbp_d_out_pocket_amt\t"
        "pbp_d_comb_max_enr_amt_yn\tpbp_d_comb_max_enr_amt\tpbp_d_oon_max_enr_oopc_yn\tpbp_d_oon_max_enr_oopc_amt\n"
    )  # empty Section D
    recs = _by_key(iter_plan_benefits(a, d, year=2026))
    assert len(recs) == 3
    assert recs[("H1000", "001")].inn_moop is None
