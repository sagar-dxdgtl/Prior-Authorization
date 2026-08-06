"""ZIP → county, and why it must answer in SETS.

A ZIP is not a place. US ZIP codes are USPS delivery routes, so they do not nest inside counties:
10,186 of 33,791 ZCTAs (30%) span more than one, and one spans six. That is the whole reason the
"just compare the two ZIPs" idea cannot work — 30144 (Kennesaw) and 30188 (Woodstock) are different
ZIPs that both include Cobb.

Ground truth here is not the dataset, it is **UHC Find Care's own county dropdown**, read live on
2026-08-06. That is the geography the plan lists are actually keyed on, and the Census 2020
ZCTA-to-County Relationship File reproduced it exactly on all three ZIPs, including the counts.

The popular libraries do NOT. Measured the same day, `pgeocode` and `zipcodes` return one county per
ZIP and answer 30101 -> "Cobb", 30188 -> "Cherokee" — discarding three of 30101's four counties. Cobb
offers 12 plans and Paulding 14, so that is a confident wrong answer, which is worse than none. If
anyone ever swaps the data source, `test_the_portal_ground_truth_is_reproduced` fails.
"""

from __future__ import annotations

import pytest

from network_probe.geo.zip_county import (
    counties_for_zip,
    describe,
    same_county,
    spans_multiple_counties,
)

# ZIP -> the counties UHC's own dropdown listed, verbatim.
PORTAL_TRUTH = {
    "30101": {"Bartow County", "Cherokee County", "Cobb County", "Paulding County"},
    "30144": {"Cobb County"},
    "30188": {"Cherokee County", "Cobb County"},
}


@pytest.mark.parametrize("zip_code,expected", sorted(PORTAL_TRUTH.items()))
def test_the_portal_ground_truth_is_reproduced(zip_code, expected):
    """The dataset must agree with the payer's own dropdown — that is the only geography that pays."""
    assert {c.name for c in counties_for_zip(zip_code)} == expected


def test_a_multi_county_zip_is_not_collapsed_to_its_dominant_county():
    """The failure mode of every one-county-per-ZIP library. 30101 is 68% Cobb, and answering "Cobb"
    would hide Paulding's different 14-plan list."""
    got = counties_for_zip("30101")
    assert len(got) == 4
    assert got[0].name == "Cobb County", "most land first"
    assert {c.name for c in got} > {"Cobb County"}


def test_shares_are_ordered_and_normalised_but_are_not_a_decision():
    got = counties_for_zip("30188")
    assert [c.name for c in got] == ["Cherokee County", "Cobb County"]
    assert got[0].land_share > got[1].land_share
    assert abs(sum(c.land_share for c in got) - 1.0) < 1e-6


def test_fips_codes_are_five_digits():
    assert {c.fips for c in counties_for_zip("30144")} == {"13067"}  # Cobb GA
    assert all(len(c.fips) == 5 and c.fips.isdigit() for c in counties_for_zip("30101"))


# --- input shapes ----------------------------------------------------------------------------------


@pytest.mark.parametrize("given", ["30144", "30144-1234", " 30144 ", "30144\n"])
def test_zip_plus_four_and_whitespace_resolve(given):
    assert {c.name for c in counties_for_zip(given)} == {"Cobb County"}


@pytest.mark.parametrize("given", ["", None, "abc", "123", "99999"])
def test_an_unusable_zip_is_empty_not_an_error(given):
    assert counties_for_zip(given) == ()
    assert spans_multiple_counties(given) is False, (
        "no data is not evidence of ambiguity — a caller must not decline on it"
    )


# --- the three-valued comparison, which is the point ------------------------------------------------


def test_the_same_zip_is_the_same_county():
    assert same_county("30144", "30144") is True


def test_two_zips_in_one_shared_single_county_are_the_same_county():
    assert same_county("30144", "30144-9999") is True


def test_disjoint_zips_are_certainly_different_counties():
    assert same_county("30144", "33101") is False  # Cobb GA vs Miami-Dade FL


def test_an_overlapping_but_unpinned_pair_is_UNKNOWN_not_a_match():
    """The exact case a ZIP-equality test gets wrong. 30144 is Cobb; 30188 is Cherokee-OR-Cobb, so
    the member may or may not share the clinic's county. That is not a match and not a mismatch."""
    assert same_county("30144", "30188") is None


def test_an_unknown_zip_is_unknown_not_false():
    assert same_county("30144", "99999") is None
    assert same_county(None, "30144") is None


def test_same_county_never_returns_true_on_a_guess():
    """Certainty requires BOTH sides to pin one county. 30101 and 30188 both contain Cobb, but
    neither is pinned to it."""
    assert same_county("30101", "30188") is not True


# --- human phrasing ---------------------------------------------------------------------------------


def test_describe_reads_naturally_for_one_and_for_many():
    assert describe("30144") == "entirely Cobb County"
    d = describe("30101")
    assert d.startswith("spanning ") and "Cobb County" in d and "2 more" in d
    assert describe("99999") == ""


def test_spans_multiple_counties_matches_the_portal():
    assert spans_multiple_counties("30144") is False
    assert spans_multiple_counties("30101") is True
    assert spans_multiple_counties("30188") is True


# --- the dataset itself -----------------------------------------------------------------------------


def test_the_crosswalk_is_national_and_not_a_truncated_download():
    from network_probe.geo.zip_county import _table

    table = _table()
    assert len(table) > 30_000, f"only {len(table)} ZCTAs — the shipped crosswalk looks truncated"
    multi = sum(1 for v in table.values() if len(v) > 1)
    assert multi > 8_000, (
        f"only {multi} multi-county ZIPs; ~30% is expected and a low count means the source "
        f"collapsed to one county per ZIP, which is the exact defect this module exists to avoid"
    )
