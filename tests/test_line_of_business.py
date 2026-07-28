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


# ---- the MEMBER'S plan outranks the payer row's coarse tag --------------------------------------
#
# Every test above passes benefit_type=None, and that is exactly why this defect survived: the LIVE
# path never passes None. `check_network` resolves the catalogue row and hands over its benefit_type,
# and the `humana-co-denver` row is tagged **"Medicare Advantage"** — a payer-level label, not this
# member's coverage. The Advantage marker was matched against plan and benefit_type combined, so the
# tag turned every Humana CO member into someone with a network and the whole no-network branch was
# skipped for the one row (Roulhac) it was built for. It only ever fired in tests.
#
# A benefit_type is one coarse label for a payer row that may sell several products. A plan string
# naming Medigap or Original Medicare is the member's actual coverage. The plan wins.

def test_a_medigap_plan_beats_a_payer_row_tagged_medicare_advantage():
    """The live Roulhac call. This is the regression that made P3 inert."""
    assert has_provider_network("Humana Medicare Supplement Plan G", "Medicare Advantage") is False


def test_original_medicare_beats_a_payer_row_tagged_medicare_advantage():
    assert has_provider_network("Original Medicare", "Medicare Advantage") is False


def test_an_advantage_plan_still_has_a_network_under_the_same_tag():
    """Guard the other direction: the tag agreeing with the plan must not change anything."""
    assert has_provider_network("Humana Gold Plus H1036-123", "Medicare Advantage") is True


def test_the_benefit_type_still_decides_when_the_plan_says_nothing():
    """With no signal in the plan, the payer row is all we have — keep using it."""
    assert has_provider_network("Humana Medicare CO", "Medicare FFS") is False
    assert has_provider_network("Humana Medicare CO", "Medicare Advantage") is True
    assert has_provider_network(None, "Medicare Advantage") is True


def test_advantage_still_wins_within_the_plan_string_itself():
    """The MA-first rule was right about its real target: marketing copy inside ONE plan name."""
    assert has_provider_network("Humana Medicare Advantage supplement plan", "Medicare") is True
