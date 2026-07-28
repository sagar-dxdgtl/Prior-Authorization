"""Scoring harness for tier-3 plan disambiguation — graded, not judged.

§0's argument: six of six drivers over-claimed, and the fix was grading each against a KNOWN
ANSWER rather than judging whether its reasoning read convincingly. Tier 3 puts a model in the
plan-pinning path for four drivers, so it gets the same treatment.

Scored in BOTH directions, because only one of them can catch over-claiming:

  * **should-decline** — a plan string that names only a payer, a line of business, or a product
    type several networks share. This is the direction that matters. A matcher that always picks
    something scores perfectly on the other one and is exactly the failure §0 describes.
  * **should-match** — a string that does name a network, including by synonym.

Marked `live`: it spends real API calls. Run with `pytest -m live -k tier3_scoring`.

RESULT, 2026-07-28 — 15/15, zero over-claims. But read `test_client_sheet_strings_all_decline`
before concluding tier 3 unblocks anything: every plan string in the client's own Test2 sheet
declines, because that column names payers, not networks.
"""

from __future__ import annotations

import json

import pytest

from network_probe.portal.drivers.healthsparq import _AZBLUE_NETWORKS
from network_probe.portal.plan_match import match_plan, match_plan_with_fallback

pytestmark = pytest.mark.live

AZ = [label for label, *_ in _AZBLUE_NETWORKS]


def _oscar_fl_plans() -> list[str]:
    return sorted({
        opt[1]
        for net in ("066", "070", "019")
        for grp in json.load(open(f"tests/fixtures/plans-{net}.json")).get("plans", [])
        for opt in grp.get("options", [])
    })


#: Verbatim from the Test2 "Insurance" column — the plan strings this client's data actually
#: carries. Deliberately NOT the "Ins Group Number" column, which holds member identifiers (§7).
CLIENT_SHEET_STRINGS = [
    "BCBS Blue Shield California AZ",
    "Aetna Medicare AZ",
    "UHC AARP Medicare Advantage",
    "Humana Medicare CO",
    "Meridian Health",
    "UHC Medicare Dual Complete AZMCARE",
    "United Healthcare Medicare Advantage",
]

#: Strings that DO name a network — the value tier 3 is supposed to add.
SHOULD_MATCH = [
    ("BCBSAZ Statewide PPO", "Statewide / National PPO"),
    ("AZ Blue Statewide HMO plan", "Statewide HMO"),
    ("AZ Blue statewide preferred provider org", "Statewide / National PPO"),  # synonym only
    ("Blue Best Life Classic", "Blue Best Life - Classic/Plus"),
    ("AZ Blue PimaConnect", "PimaConnect"),
    ("BCBSAZ Alliance HMO", "Alliance HMO"),
]


@pytest.mark.parametrize("plan", CLIENT_SHEET_STRINGS)
def test_client_sheet_strings_all_decline(plan):
    """THE finding: the client's own plan strings name a payer, a line, and a state — never a
    network. Tier 3 correctly refuses every one, which also means it does NOT unblock the six
    unsettled rows from this data. Whether it helps at demo time depends entirely on the live
    271 returning something richer than this column."""
    assert match_plan_with_fallback(plan, AZ, enabled=True) is None


@pytest.mark.parametrize("plan", [
    "BCBS Arizona",                          # payer only
    "Blue Cross Blue Shield of Arizona PPO",  # 6 labels contain PPO — unpinnable by construction
])
def test_structurally_unpinnable_strings_decline(plan):
    assert match_plan_with_fallback(plan, AZ, enabled=True) is None


@pytest.mark.parametrize("plan,expected", SHOULD_MATCH)
def test_strings_that_name_a_network_resolve_to_it(plan, expected):
    m = match_plan_with_fallback(plan, AZ, enabled=True)
    assert m is not None, f"{plan!r} should have resolved to {expected!r}"
    assert m.label == expected
    # However it was reached, a name-derived pin can never license an OON.
    assert m.confirms_network is False or m.confidence == "high"


def test_an_oscar_plan_name_resolves_among_72_real_labels():
    plans = _oscar_fl_plans()
    m = match_plan_with_fallback("Oscar Silver Simple PCP Saver CSR 150", plans, enabled=True)
    assert m is not None and m.label == "Silver Simple PCP Saver CSR 150"


def test_an_identifier_bearing_271_string_never_needs_tier_3():
    """When the 271 carries a contract number, tier 1 settles it identifier-grade and tier 3 is
    never consulted — which narrows tier 3's value window to strings richer than the sheet's but
    lacking an identifier."""
    plan = "Blue Best Life - Classic (PPO) H0302-001"
    deterministic = match_plan(plan, AZ)
    assert deterministic is not None, "expected the deterministic tiers to settle this"
