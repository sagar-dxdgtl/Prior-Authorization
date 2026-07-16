from network_probe.domain.plan_candidates import derive_plan_candidates


def _infos(*plan_strings):
    return [{"planCoverage": s} for s in plan_strings]


def test_dual_eligible_ranks_product_over_segment():
    # Devoted TX dual-eligible 271 (real values from .cache/stedi_271/1720209885.json)
    cands, selected = derive_plan_candidates(
        _infos("03 - SLMB ONLY (PARTIAL DUAL)", "DEVOTED GIVEBACK 006 TX (HMO)")
    )
    assert selected == "DEVOTED GIVEBACK 006 TX (HMO)"
    assert [c["plan"] for c in cands] == ["DEVOTED GIVEBACK 006 TX (HMO)", "03 - SLMB ONLY (PARTIAL DUAL)"]
    assert cands[0]["is_product"] is True and cands[0]["rank"] == 0


def test_oscar_first_of_two():
    cands, selected = derive_plan_candidates(_infos("BASE SILVER CSR 150", "SILVERSIMPLEPCPSAVER"))
    assert selected == "BASE SILVER CSR 150"
    assert len(cands) == 2


def test_generic_network_is_dropped():
    # Cigna returns only the useless string "Network" -> no usable candidate
    cands, selected = derive_plan_candidates(_infos("Network"))
    assert cands == [] and selected is None


def test_dedup_and_blank_skipped():
    cands, selected = derive_plan_candidates(_infos("UHC BRONZE ESSENTIAL", "", "UHC BRONZE ESSENTIAL"))
    assert [c["plan"] for c in cands] == ["UHC BRONZE ESSENTIAL"]
    assert selected == "UHC BRONZE ESSENTIAL"


def test_empty_input():
    assert derive_plan_candidates([]) == ([], None)
    assert derive_plan_candidates(None) == ([], None)
