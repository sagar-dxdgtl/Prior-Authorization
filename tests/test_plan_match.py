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
