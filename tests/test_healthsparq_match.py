"""Regression: HealthSparq provider matching must not manufacture a false IN.

Found by the adversarial verification pass, 2026-07-28. `_match(candidates, term)` derived the expected
name from the SEARCH TERM, and the driver's first term is the bare surname — so `given` was empty, the
first-name guard never ran, and any provider sharing the surname matched. The portal indexes no NPI at
all, so name matching is the only identity check there is, and a false match here reports the wrong
doctor's network status as our provider's.

This is not hypothetical: Test 2's Aetna check surfaced "Fallon Desir PSYD" as a different person while
searching for Hedson Desir. Had HealthSparq matched that row, the demo would have asserted IN-network
for a provider who is out of it.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.healthsparq import HealthSparqDriver, _is_chrome
from network_probe.portal.models import PortalQuery

DESIR = PortalQuery(
    payer_key="bcbs-empire-anthem-elevance-az",
    npi="1346866332",
    provider_first_name="Hedson",
    provider_last_name="Desir",
    zip_code="85382",
)


@pytest.fixture
def d() -> HealthSparqDriver:
    return HealthSparqDriver()


def test_same_surname_different_person_is_not_a_match(d):
    """The exact false IN the reviewer proved. These all share the surname and are other people."""
    for other in [
        "Fallon Desir, PSYD - Phoenix, AZ",
        "Marie Desir, NP - Glendale, AZ",
        "Desir, Jean-Paul, MD, Cardiology - Scottsdale, AZ",
    ]:
        assert d._match([other], DESIR) is None, f"{other!r} must not match Hedson Desir"


def test_our_provider_matches_across_name_shapes(d):
    for ours in [
        "Hedson R. Desir, MD, Family Medicine - Peoria, AZ",
        "Desir, Hedson, MD - United Vein & Vascular Centers, Peoria AZ",
        "H Desir MD - Peoria, AZ",  # initial: prefix agreement both ways
    ]:
        assert d._match([ours], DESIR) is not None, f"{ours!r} should match"


def test_surname_must_be_a_whole_token(d):
    """A substring test would let Desir match Desrosiers and invent a network status."""
    assert d._match(["Anne Desrosiers, MD - Mesa, AZ"], DESIR) is None
    assert d._match(["Paul Desired, DO"], DESIR) is None


@pytest.mark.parametrize("chrome", [
    "See all results for Desir",
    "Showing 20 results for Desir",
    "139 results",
    "3 more providers",
    "No results for Desir",
])
def test_ui_chrome_is_never_a_provider_match(d, chrome):
    """These contain the surname but name nobody. Containment alone accepted them as the matched name."""
    assert _is_chrome(chrome) is True
    assert d._match([chrome], DESIR) is None


def test_expectation_comes_from_the_query_not_the_search_term(d):
    """The structural fix: passing only a surname as the *term* must not widen the match, because the
    expectation is built from the query's first+last name regardless of what was typed."""
    candidates = ["Fallon Desir, PSYD - Phoenix, AZ"]
    # Whatever term produced these candidates, the identity check is against DESIR.
    assert d._match(candidates, DESIR) is None
    # And a query for that other person legitimately does match it.
    fallon = PortalQuery(payer_key="x", npi="0", provider_first_name="Fallon", provider_last_name="Desir")
    assert d._match(candidates, fallon) is not None


def test_missing_names_are_safe(d):
    empty = PortalQuery(payer_key="x", npi="0")
    assert d._match(["Hedson R. Desir, MD"], empty) is None
    assert d._match([], DESIR) is None
    assert d._match([""], DESIR) is None


# --- D4: label drift between the live gate and the baked-in map created a self-tie ----------------

def test_punctuation_only_label_drift_collapses_to_one_network():
    """AZ Blue's gate prints "Statewide / National PPO + Prosano"; the baked-in map has
    "Statewide/National PPO + Prosano" — the same network, spaced differently.

    `_network_map` merged them as two dict keys, so the map offered one network twice. `_best_network`
    correctly refuses a tie, so BOTH spellings then resolved to None and the member's plan could not be
    pinned at all. Measured live 2026-07-29.
    """
    from network_probe.portal.drivers.healthsparq import SITES, HealthSparqDriver

    site = SITES["azblue-healthsparq"]
    live = [("Statewide / National PPO + Prosano", "URL_LIVE_PROSANO")]
    merged = HealthSparqDriver()._merge_networks(live, site)

    prosano = [l for l, _ in merged if "Prosano" in l and "Statewide" in l]
    assert len(prosano) == 1, f"one network listed twice: {prosano}"


def test_the_live_label_wins_when_spellings_collide():
    """The payer's own gate is the authority on this plan year's wording."""
    from network_probe.portal.drivers.healthsparq import SITES, HealthSparqDriver

    site = SITES["azblue-healthsparq"]
    merged = dict(HealthSparqDriver()._merge_networks(
        [("Statewide / National PPO + Prosano", "URL_LIVE")], site))
    assert merged.get("Statewide / National PPO + Prosano") == "URL_LIVE"


def test_collapsed_label_can_then_be_pinned():
    """The point of the collapse: the member's plan string resolves instead of dying on a self-tie."""
    from network_probe.portal.drivers.healthsparq import SITES, HealthSparqDriver

    d = HealthSparqDriver()
    merged = d._merge_networks([("Statewide / National PPO + Prosano", "URL_LIVE")],
                               SITES["azblue-healthsparq"])
    got = d._best_network(merged, "Statewide/National PPO + Prosano")
    assert got is not None, "a real AZ Blue network must be pinnable"
    assert "Prosano" in got[0]


def test_genuinely_different_networks_are_not_collapsed():
    """Normalisation must not merge distinct networks — Alliance PPO/EPO and Alliance HMO are different."""
    from network_probe.portal.drivers.healthsparq import SITES, HealthSparqDriver

    merged = HealthSparqDriver()._merge_networks([], SITES["azblue-healthsparq"])
    labels = [lbl for lbl, _ in merged]
    assert "Alliance HMO" in labels and "Alliance PPO / EPO" in labels


def test_gate_label_containing_escaped_quotes_is_captured_whole():
    """AZ Blue publishes: Blue Preferred Care Tiers (\\"Triple Choice Plan\\" for State of AZ employees)

    `_GATE_LINK_RE`'s label group was `[^"]*`, which stops at the first escaped quote and captured
    'Blue Preferred Care Tiers (\\'. That is not merely ugly in the audit note — it discards
    "Triple Choice Plan" and "State of AZ employees", the only tokens that distinguish this network,
    so a member on the State of AZ employee plan could never be matched on them. Found live 2026-07-29.
    """
    from network_probe.portal.drivers.healthsparq import _GATE_LINK_RE, _gate_label

    snippet = (
        '"href":"https://azblue.healthsparq.com/healthsparq/public/#/one/city=&state=AZ'
        '&insurerCode=BCBSAZ_I&productCode=SOA&brandCode=BCBSAZMAYO","text":'
        '"Blue Preferred Care Tiers (\\"Triple Choice Plan\\" for State of AZ employees)",'
        '"anchor":""'
    )
    m = _GATE_LINK_RE.search(snippet)
    assert m is not None, "the link regex must still match a normal gate entry"
    assert _gate_label(m.group("label")) == (
        'Blue Preferred Care Tiers ("Triple Choice Plan" for State of AZ employees)'
    )


def test_gate_label_without_escapes_is_unchanged():
    from network_probe.portal.drivers.healthsparq import _GATE_LINK_RE, _gate_label

    snippet = (
        '"href":"https://azblue.healthsparq.com/healthsparq/public/#/one/city=\\u0026state=AZ'
        '\\u0026productCode=ALH","text":"Alliance HMO","anchor":""'
    )
    m = _GATE_LINK_RE.search(snippet)
    assert m is not None
    assert _gate_label(m.group("label")) == "Alliance HMO"
