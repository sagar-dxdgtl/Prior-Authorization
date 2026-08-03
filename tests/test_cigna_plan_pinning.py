"""Cigna's plan picker must not depend on the order the portal happens to list plans in.

`_best_plan`'s first tier scored distinctive-token overlap with `score > best_score`, so a TIE was
broken by whichever plan the portal rendered first. Measured against Cigna's real commercial plan
buttons: for a member on "Cigna LocalPlus", the labels "Cigna LocalPlus" and "Cigna LocalPlus IN"
both score 1, so the answer flipped purely on list order — and LocalPlus IN is a *narrower* network,
so pinning it and then reading absence as OUT_OF_NETWORK answers about a network the member is not
in. Same defect class as the UHC CareFlex mis-pin; see test_uhc_findcare_plan_pinning.py.

It also guessed where there was nothing to choose between: "Cigna Open Access" against both
"Open Access Plus…" and "Open Access Plus IN…" picked one on shared generic words.

The fix routes tier 1 through `portal/plan_match.py`, which ranks identifiers first, excludes
non-distinctive words, and refuses ties outright. Cigna's own second tier — the product-family
preference, which already requires exactly one hit — is unchanged and still catches the plan strings
that name a line rather than a network ("Cigna Commercial" scores zero tokens by design).
"""

import pytest

from network_probe.portal.drivers.cigna_hcp import CignaHcpDriver

LOCALPLUS = ["Cigna LocalPlus", "Cigna LocalPlus IN"]


@pytest.mark.parametrize("labels", [LOCALPLUS, list(reversed(LOCALPLUS))])
def test_the_pick_does_not_depend_on_portal_ordering(labels):
    """The load-bearing property: same member, same plan list, same answer whatever the order."""
    m = CignaHcpDriver().choose_plan(labels, "Cigna LocalPlus")
    assert m is not None
    assert m.label == "Cigna LocalPlus", (
        "'Cigna LocalPlus IN' is a narrower network — pinning it would answer about the wrong one"
    )


def test_nothing_to_choose_between_declines_rather_than_guesses():
    m = CignaHcpDriver().choose_plan(
        ["Open Access Plus, OA Plus, Choice Fund OA Plus", "Open Access Plus IN, OA Plus IN"],
        "Cigna Open Access",
    )
    assert m is None, "two networks fit equally well — refusing is the correct answer"


def test_a_names_only_pin_is_never_identifier_grade():
    """Cigna labels networks by product (OAP / PPO / LocalPlus / SureFit) and carry no plan
    identifier, so `confirms_network` can never be satisfied from them. Recorded so a future change
    that starts licensing an OON off these labels has to argue with a test."""
    m = CignaHcpDriver().choose_plan(
        ["Open Access Plus, OA Plus, Choice Fund OA Plus", "Cigna SureFit"],
        "Cigna Open Access Plus",
    )
    assert m is not None and not m.confirms_network


def test_an_identifier_in_the_plan_string_still_wins_when_the_portal_prints_one():
    """Not reachable from today's Cigna labels, but the tier must exist: if Cigna ever prints a HIOS
    id or contract number, it must beat word overlap rather than tie with it."""
    m = CignaHcpDriver().choose_plan(
        ["Cigna Connect 12345AZ0010001", "Cigna Connect Flex 67890AZ0020002"],
        "Cigna Connect Flex 67890AZ0020002",
    )
    assert m is not None and m.confirms_network
    assert m.label == "Cigna Connect Flex 67890AZ0020002"


def test_empty_inputs_decline():
    d = CignaHcpDriver()
    assert d.choose_plan([], "Cigna LocalPlus") is None
    assert d.choose_plan(LOCALPLUS, None) is None
