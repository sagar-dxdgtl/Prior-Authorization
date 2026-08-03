"""Aetna Find Medicare Providers — the half `aetna_ahpublic` deliberately refuses.

The commercial driver returns UNKNOWN before navigating for any Medicare line, because answering a
Medicare member from Aetna's commercial directory would be the wrong network. That was correct and
it left every Aetna Medicare row with no portal evidence at all. This driver covers it.

Verified live 2026-08-03 (Cook County IL, Tursunaliev NPI 1780175349):

    IN : plan pinned by identifier (H5521016) → profile NPI 1780175349 → badge "In Network"
    OUT: same pin, a surname the plan does not list → absence, licensed by the identifier-grade pin

Unit-tested here: the pure logic that decides WHICH plan is pinned and WHETHER a page is about our
provider. The walk itself is exercised live, not mocked — a mocked SPA proves nothing about a portal
that redesigns.
"""

import pytest

from network_probe.portal.capture import driver_for
from network_probe.portal.drivers.aetna_medicare_direct import (
    _IN_BADGE,
    _NPI_ON_PAGE,
    _OUT_BADGE,
    AetnaMedicareDirectDriver,
    _normalise_wanted,
    _plan_id_tokens,
)
from network_probe.portal.models import PortalQuery
from network_probe.portal.plan_match import match_plan

# The real Cook County list, as the radios expose it (planId, label).
COOK = [
    ("H1206-004-2026", "Aetna Medicare Prime Chronic Care (HMO C-SNP) - H1206-004"),
    ("H1206-009-2026", "Aetna Medicare Prime Chronic Total (HMO C-SNP) - H1206-009"),
    ("H3192-001-2026", "Aetna Medicare Prime (HMO-POS) - H3192-001"),
    ("H1206-003-2026", "Aetna Medicare Signature (HMO-POS) - H1206-003"),
    ("H5521-016-2026", "Aetna Medicare Premier (PPO) - H5521-016"),
    ("H5521-086-2026", "Aetna Medicare Signature Extra (PPO) - H5521-086"),
    ("H7301-013-2026", "Aetna Medicare Signature (PPO) - H7301-013"),
    ("H5521-286-2026", "Aetna Medicare Eagle (PPO) - H5521-286"),
]
HAYSTACK = [f"{_plan_id_tokens(pid)} {label}".strip() for pid, label in COOK]


# --- the identifier, which is the whole reason this portal is worth driving ------------------------


def test_plan_id_becomes_contract_plus_pbp():
    assert _plan_id_tokens("H5521-016-2026") == "H5521016"
    assert _plan_id_tokens("H3192-001-2026") == "H3192001"


def test_the_plan_year_is_dropped():
    """A 271 names the contract and PBP, never the plan year — leaving it on makes it unmatchable."""
    assert "2026" not in _plan_id_tokens("H5521-016-2026")


def test_the_271_string_is_normalised_so_the_pbp_survives():
    """`identifiers()` reads "H5521-016" as just H5521 — the hyphen eats the PBP. One Aetna contract
    covers several plans in a county, so contract-only is a TIE, and the first live run pinned the
    right plan purely because it came first in the list."""
    out = _normalise_wanted("Aetna Medicare Premier (PPO) H5521-016")
    assert "H5521016" in out
    assert "Aetna Medicare Premier" in out  # original kept, so the token tier still works


def test_a_plan_string_with_no_identifier_is_left_alone():
    assert _normalise_wanted("Aetna Medicare Eagle") == "Aetna Medicare Eagle"
    assert _normalise_wanted(None) == ""


def test_contract_plus_pbp_pins_the_exact_plan_not_its_siblings():
    """H5521 alone is Premier (016), Signature Extra (086) AND Eagle (286). Only the PBP separates
    them, so this is the test that stops the CareFlex-class mis-pin on Aetna."""
    m = match_plan(_normalise_wanted("Aetna Medicare Premier (PPO) H5521-016"), HAYSTACK)
    assert m is not None and m.confirms_network
    assert "Premier" in m.label and "H5521-016" in m.label


@pytest.mark.parametrize("pbp,expect", [("016", "Premier"), ("086", "Signature Extra"), ("286", "Eagle")])
def test_each_sibling_of_one_contract_resolves_to_itself(pbp, expect):
    m = match_plan(_normalise_wanted(f"Aetna Medicare H5521-{pbp}"), HAYSTACK)
    assert m is not None and expect in m.label


def test_a_bare_contract_pbp_from_a_271_still_pins():
    m = match_plan(_normalise_wanted("H5521016"), HAYSTACK)
    assert m is not None and "H5521-016" in m.label and m.confirms_network


def test_a_name_only_string_never_licenses_an_out_of_network():
    m = match_plan(_normalise_wanted("Aetna Medicare Eagle"), HAYSTACK)
    assert m is not None and not m.confirms_network


# --- reading the profile ---------------------------------------------------------------------------


def test_out_of_network_is_never_read_as_in_network():
    """"Out of Network" contains "Network"; a loose in-badge test run first would invert the answer.
    The driver checks OUT before IN, and these anchors are what make that safe."""
    body = "Serik Tursunaliev, MD\nNPI ID: 1780175349\nOut of Network\n"
    assert _OUT_BADGE.search(body)
    m = _NPI_ON_PAGE.search(body)
    assert m and m.group(1) == "1780175349"


def test_in_network_badge_and_npi_are_read():
    body = "Serik Tursunaliev, MD\nNPI ID: 1780175349\nIn Network\n"
    assert _IN_BADGE.search(body) and not _OUT_BADGE.search(body)


def test_npi_is_required_to_be_ten_digits():
    assert not _NPI_ON_PAGE.search("NPI ID: 12345")


# --- picking WHICH suggestion to open ---------------------------------------------------------------


def test_the_surname_must_match_as_a_word_not_a_substring():
    """Cigna's typeahead answered "Orem" with "Shoaf, Noremi D" — and "noremi" contains "orem"."""
    d = AetnaMedicareDirectDriver()
    q = PortalQuery(payer_key="aetna-il", npi="1497741409", provider_last_name="Orem")
    assert d._looks_like_our_provider("Randall Orem, MD\n\nPractitioner", q)
    assert not d._looks_like_our_provider("Shoaf, Noremi D\n\nPractitioner", q)


def test_no_surname_matches_nothing():
    d = AetnaMedicareDirectDriver()
    q = PortalQuery(payer_key="aetna-il", npi="1497741409")
    assert not d._looks_like_our_provider("Anyone At All", q)


# --- routing: the same payer key serves two lines of business on two portals -------------------------


@pytest.mark.parametrize("plan", ["Aetna Medicare Prime Extra (HMO)", "Aetna Medicare Premier (PPO) H5521-016"])
def test_a_medicare_plan_routes_to_this_driver(plan):
    assert driver_for("aetna-il", plan).key == "aetna-medicare-direct"


@pytest.mark.parametrize("plan", ["Aetna Choice POS II", "Aetna Open Choice PPO", None])
def test_a_commercial_plan_still_routes_to_the_commercial_driver(plan):
    """The Medicare split must not steal the rows the commercial driver already answers."""
    assert driver_for("aetna-il", plan).key == "aetna-ahpublic"


def test_other_payers_are_untouched_by_the_split():
    assert driver_for("unitedhealthcare-fl-south-florida", "AARP Medicare Advantage").key == "uhc-findcare"


def test_the_driver_declines_without_a_plan():
    """This directory answers per Medicare plan; with no plan there is no network to search."""
    d = AetnaMedicareDirectDriver()
    q = PortalQuery(payer_key="aetna-il", npi="1780175349", zip_code="60305", plan=None)

    class _Page:
        url = "about:blank"

    cap = d.capture(_Page(), q, lambda name: None)
    assert cap.status.name == "UNKNOWN"
    assert "no plan" in cap.note.lower()
