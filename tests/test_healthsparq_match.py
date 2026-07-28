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
