"""The TiC-eligibility gate: only COMMERCIAL/ACA lines are subject to the federal Transparency-in-
Coverage MRF mandate. Medicare (Advantage + FFS), Medicaid, Dual, TRICARE and VA are exempt, so
TiC must never be consulted for them (it would be a guaranteed blank, live)."""

from network_probe.domain.line_of_business import is_commercial, line_of_business


def test_benefit_type_commercial_is_commercial():
    assert line_of_business(None, "Commercial") == "commercial"
    assert is_commercial(None, "Commercial") is True


def test_benefit_type_aca_is_commercial():
    assert line_of_business(None, "ACA") == "commercial"
    assert is_commercial(None, "ACA") is True


def test_medicare_advantage_is_not_commercial():
    assert line_of_business(None, "Medicare Advantage") == "medicare"
    assert is_commercial(None, "Medicare Advantage") is False


def test_traditional_medicare_is_not_commercial():
    assert line_of_business(None, "Traditional Medicare") == "medicare"
    assert is_commercial(None, "Traditional Medicare") is False


def test_dual_eligible_is_not_commercial():
    assert line_of_business(None, "Dual Eligible (FIDE SNP)") == "dual"
    assert is_commercial(None, "Dual Eligible (FIDE SNP)") is False


def test_managed_and_traditional_medicaid_are_not_commercial():
    assert line_of_business(None, "Managed Medicaid") == "medicaid"
    assert line_of_business(None, "Traditional Medicaid") == "medicaid"
    assert is_commercial(None, "Managed Medicaid") is False


def test_tricare_and_va_are_federal_not_commercial():
    assert line_of_business(None, "TRICARE Secondary") == "federal"
    assert line_of_business(None, "VA") == "federal"
    assert is_commercial(None, "TRICARE Secondary") is False


def test_plan_text_dual_complete_overrides_missing_benefit_type():
    # the real 271 plan name is the member's actual plan — it wins over a blank benefit_type
    plan = "AZ UNITEDHEALTHCARE DUAL COMPLETE HMOPOS FULL H032"
    assert line_of_business(plan, None) == "dual"
    assert is_commercial(plan, None) is False


def test_plan_text_medicare_marker_beats_commercial_benefit_type():
    # if the plan name clearly says Medicare Advantage, don't let a stale "Commercial" tag fire TiC
    assert is_commercial("AARP Medicare Advantage PPO", "Commercial") is False


def test_h_contract_number_is_medicare_advantage():
    # CMS H-contract numbers (H\d{4}) are Medicare Advantage plans
    assert is_commercial("SOME PLAN H0032", None) is False


def test_commercial_plan_text_with_commercial_benefit_type():
    assert line_of_business("Cigna Open Access Plus PPO", "Commercial") == "commercial"
    assert is_commercial("Cigna Open Access Plus PPO", "Commercial") is True


def test_unknown_both_is_not_commercial():
    # conservative: with no signal either way, do NOT claim commercial (would wrongly invite TiC)
    assert line_of_business(None, None) == "unknown"
    assert is_commercial(None, None) is False


# ---- P3: coverage that has NO provider network at all ----------------------------------
#
# Original Medicare (FFS) and Medicare Supplement / Medigap have no network. Asking a
# network directory about such a member can only return UNKNOWN or a false OON, because a
# Medicare-participating provider is in-network by definition.
# See HANDOFF-2026-07-28.md §1 P3 (Roulhac, NPI 1801837109, Humana CO — staff INN).

from network_probe.domain.line_of_business import has_provider_network  # noqa: E402


def test_medicare_supplement_has_no_provider_network():
    assert has_provider_network("Humana Medicare Supplement Plan G", None) is False


def test_medigap_has_no_provider_network():
    assert has_provider_network("Medigap Plan F", None) is False


def test_original_medicare_has_no_provider_network():
    assert has_provider_network("Original Medicare", None) is False
    assert has_provider_network(None, "Medicare FFS") is False


def test_medicare_advantage_does_have_a_network():
    assert has_provider_network("AARP Medicare Advantage PPO", None) is True
    assert has_provider_network("Humana Honor PPO H1036", None) is True


def test_commercial_has_a_network():
    assert has_provider_network("Cigna Open Access Plus", "Commercial") is True


def test_advantage_wins_over_a_bare_supplemental_word():
    """'Supplemental benefits' is standard Medicare Advantage marketing — not Medigap."""
    assert has_provider_network("UHC Medicare Advantage with supplemental dental", None) is True
