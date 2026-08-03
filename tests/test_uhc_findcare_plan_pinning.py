"""UHC Find Care must pin the member's plan by IDENTIFIER before it may read absence as OON.

Caught live 2026-08-03 — Randall C Orem (NPI 1497741409), member on
"LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO". The walk trail reads:

    ... → location committed (Select) → plan pinned: AARP Medicare Advantage CareFlex
                                                     from UHC FL-35 (HMO-POS)

and the capture then returned a confident **OUT_OF_NETWORK** "for this plan". FL-35 (HMO-POS) is a
different product from FL-0026 (PPO): the driver answered about a network the member is not in.
That is the single worst failure this layer can have, and it is the exact case `portal/plan_match.py`
was written to prevent — its module docstring opens on this very CareFlex mis-pin.

The driver had simply never adopted it. `_best_option` scored ≥4-char token overlap, so the four
words every UHC Medicare product shares — AARP, MEDICARE, ADVANTAGE, FROM — were enough to "confirm"
a plan, and `plan_confirmed=True` then licensed the OON. Three defects, one root:

  1. non-distinctive words counted as evidence of plan identity;
  2. only the first 12 options were ever scored, so the member's plan could be invisible;
  3. any match at all licensed an absence reading — there was no identifier-grade gate.

Humana's driver already does this correctly (`_Pin.confirms` + `_absence_blockers`). These tests
hold UHC to the same bar.
"""

import pytest

from network_probe.portal.drivers.uhc_findcare import UhcFindCareDriver
from network_probe.portal.models import PortalStatus

# UHC's real FL Medicare plan list, in portal order. CareFlex sits FIRST — which is why a
# ties-go-to-the-earliest word scorer picked it.
FL_OPTIONS = [
    "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)",
    "AARP Medicare Advantage Patriot from UHC FL-0016 (HMO-POS)",
    "AARP Medicare Advantage from UHC FL-0002 (HMO)",
    "AARP Medicare Advantage Choice from UHC FL-0026 (PPO)",
    "UnitedHealthcare Dual Complete FL-S001 (HMO-POS D-SNP)",
]
OREM_PLAN = "LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO"


def test_the_identifier_wins_over_shared_marketing_words():
    """FL-0026 is in the plan string and in exactly one label. That is the answer."""
    m = UhcFindCareDriver().choose_plan(FL_OPTIONS, OREM_PLAN)
    assert m is not None, "an identifier present in both strings must always match"
    assert m.label == "AARP Medicare Advantage Choice from UHC FL-0026 (PPO)"
    assert m.index == 3
    assert m.confidence == "high"
    assert m.confirms_network


def test_the_careflex_mispin_does_not_recur():
    m = UhcFindCareDriver().choose_plan(FL_OPTIONS, OREM_PLAN)
    assert "CareFlex" not in m.label
    assert "FL-35" not in m.label


def test_an_identifier_past_the_twelfth_option_is_still_found():
    """The old scorer read `min(opts.count(), 12)`, so a long market list hid the member's plan."""
    padded = [f"AARP Medicare Advantage Filler {i} from UHC FL-90{i:02d} (HMO)" for i in range(14)]
    padded.append("AARP Medicare Advantage Choice from UHC FL-0026 (PPO)")
    m = UhcFindCareDriver().choose_plan(padded, OREM_PLAN)
    assert m is not None and m.label.endswith("FL-0026 (PPO)")
    assert m.index == 14


def test_shared_words_alone_never_confirm_a_network():
    """No identifier on either side: a pick may still happen, but it must not be identifier-grade."""
    m = UhcFindCareDriver().choose_plan(
        ["AARP Medicare Advantage CareFlex from UHC (HMO-POS)",
         "AARP Medicare Advantage Patriot from UHC (HMO-POS)"],
        "AARP MEDICARE ADVANTAGE FROM UHC",
    )
    assert m is None or not m.confirms_network, (
        "AARP/MEDICARE/ADVANTAGE/FROM are shared by every UHC MA product and identify nothing"
    )


# --- the gate that turns a pin into a licence to call OON ----------------------------------------


@pytest.mark.parametrize(
    "confirms,expected",
    [(True, True), (False, False)],
)
def test_only_an_identifier_grade_pin_licenses_an_absence_reading(confirms, expected):
    class _Pin:
        name = "AARP Medicare Advantage Choice from UHC FL-0026 (PPO)"
        why = "test"

    pin = _Pin()
    pin.confirms = confirms
    assert (UhcFindCareDriver()._absence_blockers(pin) == []) is expected


def test_no_pin_at_all_blocks_an_absence_reading():
    class _Pin:
        name = None
        why = "no plan matched"
        confirms = False

    blockers = UhcFindCareDriver()._absence_blockers(_Pin())
    assert blockers, "with no network pinned, absence is absence from an unknown scope"
    assert "scope" in " ".join(blockers).lower()


def test_a_names_only_pin_says_so_in_plain_words():
    """§8: the note must name which guard fired, so a reader can see why it is not an OON."""

    class _Pin:
        name = "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)"
        why = "matched on 2 distinctive term(s)"
        confirms = False

    blockers = " ".join(UhcFindCareDriver()._absence_blockers(_Pin()))
    assert "identifier" in blockers.lower() or "names" in blockers.lower()


def test_presence_still_needs_only_a_pinned_plan_not_an_identifier():
    """Asymmetry, deliberately: finding the provider IN the searched list is positive evidence.

    Absence is the direction that needs the strong gate, because absence from the WRONG network
    looks identical to absence from the right one.
    """
    d = UhcFindCareDriver()
    status, _ = d.presence_verdict(
        plan_confirmed=True, kind="NPI", term="1902811656", matched="Conrad Chang Manayan",
        npi="1902811656", zip_code="30144", plan="UHC Medicare Advantage GA-2 (PPO)",
    )
    assert status == PortalStatus.IN_NETWORK
