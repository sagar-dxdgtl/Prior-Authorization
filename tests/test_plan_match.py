"""Plan-matching tests. The first one is a regression for a real live mis-pin.

On 2026-07-28 a live UHC Find Care capture was handed the loose plan string "AARP Medicare Advantage"
and pinned "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)" on shared words alone, then
reported OUT_OF_NETWORK against it. Every UHC MA plan in that market shares those words. These tests
pin the rule that fixes it: names do not identify a plan, identifiers do.
"""

from __future__ import annotations

from network_probe.portal.plan_match import (
    distinctive_tokens,
    identifiers,
    match_plan,
    needs_disambiguation,
)

# The real options UHC's guest plan list offers for this market.
UHC_FL_OPTIONS = [
    "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)",
    "AARP Medicare Advantage Patriot from UHC FL-0026 (PPO)",
    "AARP Medicare Advantage Choice from UHC FL-12 (PPO)",
    "UnitedHealthcare Dual Complete FL-S001 (HMO-POS D-SNP)",
]


def test_loose_plan_name_refuses_to_pin():
    """The CareFlex regression: a vague name must NOT pin a plan.

    "AARP Medicare Advantage" is common to three of these options. Anything but None here means the
    driver would go on to report a network verdict about a plan the member may not be in.
    """
    assert match_plan("AARP Medicare Advantage", UHC_FL_OPTIONS) is None
    # And it should be escalated rather than silently dropped.
    assert needs_disambiguation("AARP Medicare Advantage", UHC_FL_OPTIONS) is True


def test_271_contract_id_pins_decisively():
    """A real 271 carries the contract — that identifies the plan unambiguously."""
    wanted = "AARP Medicare Advantage from UHC FL-0026 (PPO) / H2406018000"
    m = match_plan(wanted, UHC_FL_OPTIONS)
    assert m is not None
    assert m.index == 1
    assert m.confidence == "high"
    assert m.confirms_network is True  # only an identifier match licenses an OON
    assert "FL-0026" in m.basis


def test_market_code_alone_is_enough():
    m = match_plan("UHC FL-35", UHC_FL_OPTIONS)
    assert m is not None and m.index == 0 and m.confidence == "high"


def test_contract_only_matches_when_portal_prints_the_contract():
    """A 271 giving contract+PBP must still match a portal label printing only the contract."""
    options = ["Some Plan (H2406)", "Other Plan (H9999)"]
    m = match_plan("Member plan H2406018000", options)
    assert m is not None and m.index == 0 and m.confidence == "high"
    assert "H2406" in m.identifiers


def test_most_specific_identifier_wins():
    options = ["Plan A H2406", "Plan B H2406018000"]
    m = match_plan("H2406018000", options)
    assert m is not None and m.index == 1


def test_tie_on_tokens_refuses_rather_than_guessing():
    options = ["Vitality Gold Plan", "Vitality Gold Plan (Region 2)"]
    assert match_plan("Vitality Gold", options) is None


def test_distinctive_token_match_pins_but_does_not_confirm_network():
    """Two distinctive words is enough to drive the search, but not to assert out-of-network."""
    options = ["Meridian Complete Illinois", "Wellcare Dual Access"]
    m = match_plan("Meridian Complete", options)
    assert m is not None and m.index == 0
    assert m.confidence == "medium"
    assert m.confirms_network is False  # the whole point: weak match cannot license an OON


def test_plan_type_and_line_words_are_not_distinctive():
    """HMO/PPO/Medicare/Advantage must never carry a match on their own."""
    assert distinctive_tokens("Medicare Advantage HMO POS Plan") == set()
    assert match_plan("Medicare Advantage HMO", UHC_FL_OPTIONS) is None


def test_identifier_extraction_granularities():
    assert identifiers("H2406018000") == {"H2406", "H2406018", "H2406018000"}
    assert "FL-0026" in identifiers("AARP ... FL-0026 (PPO)")
    assert identifiers("no ids here at all") == set()


def test_empty_and_missing_inputs_are_safe():
    assert match_plan(None, UHC_FL_OPTIONS) is None
    assert match_plan("H2406", []) is None
    assert needs_disambiguation(None, UHC_FL_OPTIONS) is False
    assert needs_disambiguation("x", ["only one option"]) is False


# --- ACA / HIOS ------------------------------------------------------------------------------------
# Added 2026-07-28 after measuring that 0 of Oscar's 90 Florida plan labels carried any identifier the
# Medicare-shaped patterns recognise. `confirms_network` was therefore unreachable for every ACA row,
# which made both IN and OON impossible for that whole line regardless of driver quality.

OSCAR_FL_OPTIONS = [
    "Bronze Simple 99999FL0020002",
    "Silver Simple CSR 150 12345FL0010001",
    "Gold Classic 12345FL0030003",
]


def test_hios_component_id_pins_an_aca_plan():
    m = match_plan("Oscar Silver Simple 12345FL0010001-01", OSCAR_FL_OPTIONS)
    assert m is not None
    assert m.index == 1
    assert m.confirms_network is True, "a HIOS id must license an OON like a contract number does"


def test_hios_variant_matches_a_portal_printing_only_the_base():
    """A 271 naming the -01 variant must still match a label carrying only the component id."""
    assert "12345FL0010001" in identifiers("12345FL0010001-01")
    m = match_plan("12345FL0010001-01", ["Silver Simple 12345FL0010001"])
    assert m is not None and m.confirms_network


def test_hios_does_not_match_a_different_issuer_or_product():
    assert match_plan("12345FL0010001", ["Bronze 99999FL0020002"]) is None


def test_long_digit_runs_are_not_mistaken_for_hios_ids():
    """Phone numbers, NPIs and member ids must never read as plan identifiers."""
    for noise in ("5551234567", "1234567890", "member 999888777666", "NPI 1902811656"):
        assert identifiers(noise) == set(), f"{noise!r} must yield no plan identifier"


def test_medicaid_has_no_identifier_and_that_is_documented_behaviour():
    """Managed Medicaid networks carry no CMS identifier at all, so confirms_network is unreachable
    for that line through this function. Asserted so the gap stays visible rather than surprising a
    future Medicaid driver author."""
    assert identifiers("TX - Texas STAR") == set()
    assert identifiers("Molina Healthcare Texas STAR / Managed Medicaid") == set()
