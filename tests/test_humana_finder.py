"""Adversarial regressions for the Humana Find Care driver.

Every test here pins a defect that was actually present in `humana_finder.py` before this review, or a
defect class that shipped in a sibling driver and that this driver's shape made reachable. The file had
never been reviewed and its author left no live run behind, so nothing was assumed to work: the fixtures
below are the REAL strings from this review's live headless runs against findcare.humana.com on
2026-07-28 (location "Thornton, CO 80229", network "Medicare PPO"), including Humana's own results
header, its session-store network list, and verbatim provider cards.

The two rows under test are the client's own: Test 2 "Humana Medicare CO" (Roulhac) and Ins Test 3
"Humana Medicare FL" (Shah).
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Error as PlaywrightError

from network_probe.portal.drivers.humana_finder import (
    HumanaFinderDriver,
    _badge,
    _echoes_term,
    _parse_total,
    _Read,
    _Walk,
)
from network_probe.portal.models import PortalQuery

ROULHAC = PortalQuery(payer_key="humana-co", npi="1801837109", provider_first_name="Maurice",
                      provider_last_name="Roulhac", plan="Humana Medicare", state="CO",
                      city="Thornton", zip_code="80229")
SHAH = PortalQuery(payer_key="humana-fl", npi="1710304746", provider_first_name="Kush",
                   provider_last_name="Shah", plan="Humana Medicare", state="FL", city="Tampa",
                   zip_code="33618")

# Verbatim cards from the live "Smith" search in network "Medicare PPO" near 80229.
CARD_TIMOTHY = (
    "Smith, Timothy J MD\nInternal Medicine Physician (PCP)\n|\nUCHealth Medical Group\n"
    "311 Steele St, Denver, CO 80206\n9.46 miles\nIn network\nAccepting new patients\n"
    "Board certified\nView details\nMore\n(303) 372-4010"
)
# Humana's own results header. It is a CHILD of the results-list container, so a card selector that is
# not id-filtered picks it up as if it were a provider.
RESULTS_HEADER = "59 in network results for “Smith”\nPrint/email results\nSort by most relevant"
EMPTY_COPY = "No results in network for “Roulhac” within 15 miles"

# The real sessionStorage.FCMEDICALSESSIONSTORE network list for Thornton, CO (12 rows: 1 group header
# with networkId 0, 11 selectable networks, two of which share the display name
# "Natl Med HMO Colorado-Home" under different networkIds).
CO_STORE = json.dumps({
    "customerId": 1,
    "selectedNetwork": {"networkId": 0, "networkName": "Medicare networks"},
    "networks": {"current": [
        {"networkId": 0, "networkName": "Medicare networks"},
        {"networkId": 603, "networkName": "Medicare PPO/Employer PPO Plus"},
        {"networkId": 3912, "networkName": "Medicare PPO"},
        {"networkId": 3912, "networkName": "Group Medicare PPO+"},
        {"networkId": 3912, "networkName": "Humana Honor PPO"},
        {"networkId": 3913, "networkName": "Natl Medicare HMO/SNP-Travel"},
        {"networkId": 3913, "networkName": "National Employer HMO – Travel"},
        {"networkId": 3914, "networkName": "HumanaGoldChoice Ntwk PFFS"},
        {"networkId": 3922, "networkName": "Natl Med HMO Colorado-Home"},
        {"networkId": 3973, "networkName": "National Employer HMO-Home"},
        {"networkId": 3980, "networkName": "Humana Dual Select HMO D-SNP"},
        {"networkId": 4037, "networkName": "Natl Med HMO Colorado-Home"},
    ], "future": []},
})


class _StubPage:
    """Just enough Page for `_network_list`: one `evaluate`, and a `locator` that refuses."""

    def __init__(self, raw):
        self.raw = raw

    def evaluate(self, expr, *args):
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw

    def locator(self, selector):
        raise PlaywrightError("no live DOM in this test")


@pytest.fixture
def d():
    return HumanaFinderDriver()


class TestBadgeInversion:
    """Defect class A: the positive phrase is a SUBSTRING of the negative ones.

    Two sibling drivers shipped a badge reader that tested "in network" first, so a card the payer had
    explicitly labelled "Not In Network" was read as in-network and became a false IN_NETWORK. This
    driver had no badge reader at all, which is the same bug with the test missing entirely: a card
    Humana labelled out-of-network was counted as a presence and returned as IN.
    """

    @pytest.mark.parametrize("text", [
        "Not in network", "not in-network", "NOT IN NETWORK", "Not-in-network",
        "Out of network", "out-of-network", "Out of the network", "Non-network",
        "non-participating", "Nonparticipating",
        "Roulhac, Maurice MD\nFamily Medicine\n9195 Grant St, Thornton, CO 80229\nNot in network",
    ])
    def test_every_negative_phrasing_reads_out(self, text):
        assert _badge(text) == "out", f"{text!r} must read as out-of-network"

    @pytest.mark.parametrize("text", ["In network", "in-network", "IN NETWORK", CARD_TIMOTHY])
    def test_positive_badges_still_read_in(self, text):
        assert _badge(text) == "in"

    def test_no_badge_is_not_a_verdict(self):
        assert _badge("Smith, Timothy J MD\n311 Steele St, Denver, CO 80206\n9.46 miles") is None
        assert _badge("") is None
        assert _badge(None) is None


class TestIdentity:
    """Defect class B: identity must come from the QUERY and match as a whole token."""

    def test_our_provider_matches(self, d):
        card = "Roulhac, Maurice A MD\nFamily Medicine Physician (PCP)\n|\n9195 Grant St Ste 300, " \
               "Thornton, CO 80229\n0.4 miles\nIn network\nView details"
        got, why = d._match([card], ROULHAC)
        assert why == "surname + first name"
        assert got is not None and got[0].startswith("Roulhac, Maurice")

    def test_same_surname_stranger_is_not_a_match(self, d):
        """The live "Smith" search returned 59 different Smiths. A surname-only test would have
        returned the first of them as our provider and turned an absence into a fabricated IN."""
        assert d._match([CARD_TIMOTHY], PortalQuery(
            payer_key="humana-co", npi="1", provider_first_name="Maurice",
            provider_last_name="Smith")) == (None, "no card matched")

    def test_first_name_substring_is_not_a_match(self, d):
        """The concrete false IN the old substring matcher produced for the Ins Test 3 row: the query
        is Kush Shah, and "kush" is a substring of "kushal", so `_norm(card)` containing both "shah"
        and "kush" made a DIFFERENT person (Kushal Shah) our provider. It must not match — and it must
        not be called absent either, since a fuller spelling of the same person is possible."""
        got, why = d._match(["Shah, Kushal MD\nCardiology\nTampa, FL 33618\nIn network"], SHAH)
        assert got is None
        assert why == "ambiguous"

    def test_substring_surname_is_rejected(self, d):
        """"Shah" is a substring of "Shahid": a substring surname test invents a namesake."""
        assert d._match(["Shahid, Kush MD\nTampa, FL"], SHAH)[0] is None

    def test_initial_only_is_ambiguous_not_a_verdict(self, d):
        """"Roulhac, M" could be Maurice or Michael. Returning it is a false IN; treating it as absent
        is a false OON while our provider may be that very row. It must decide nothing."""
        assert d._match(["Roulhac, M MD\nThornton, CO 80229\nIn network"], ROULHAC) == (
            None, "ambiguous")

    def test_a_middle_initial_is_not_a_first_initial(self, d):
        """Verified live: the Tampa "Shah" search returned "Shah, Anna K MD" among 26 cards. Her MIDDLE
        initial K collided with Kush under a blunt any-single-letter test, making a plainly different
        person "ambiguous" — which suppresses a legitimate absence reading. Only the token in the
        given-name position of Humana's "Last, First M CRED" format may make a namesake ambiguous."""
        got, why = d._match(["Shah, Anna K MD\nOphthalmology\nTampa, FL 33618\nIn network"], SHAH)
        assert (got, why) == (None, "no card matched")

    def test_a_card_not_in_last_comma_first_form_stays_conservative(self, d):
        """The live outlier "Vipul Shah MD PA" is a practice listing with no comma, so no token can be
        trusted as the given name; the blunt test applies rather than an assumed position."""
        assert d._match(["Shah, K MD\nTampa, FL"], SHAH) == (None, "ambiguous")
        assert d._match(["Vipul Shah MD PA\nTampa, FL"], SHAH) == (None, "no card matched")
        assert d._match(["K Shah MD PA\nTampa, FL"], SHAH) == (None, "ambiguous")

    def test_the_real_tampa_namesakes_are_not_our_provider(self, d):
        """The 26 verbatim name lines from the live Tampa "Shah" search in Humana Honor PPO. NPPES says
        NPI 1710304746 is KUSH SHAH MD; none of these is him, and none may be reported as him."""
        cards = [f"{n}\nSpecialty\nTampa, FL\nIn network" for n in (
            "Shah, Sailesh N DO", "Shah, Chirag V DO", "Shah, Jayesh S MD", "Shaha, Manish A MD",
            "Shah, Shalin R DO", "Motiwala, Shaheen MD", "Shah, Anna K MD", "Shah, Aman M MD",
            "Shah, Anjan R MD", "Shah, Suketu MD", "Shaha, Melanie L SLP", "Shah, Shivan M MD",
            "Shah Desai, Sejal MD", "Shah, Ami M DO", "Shah, Aashka MD", "Vipul Shah MD PA",
            "Shah, Sagar DPM", "Jivani, Shahenaz NP", "Shahwar, Durre MSN", "SHAH, NIKESH MD")]
        assert d._match(cards, SHAH) == (None, "no card matched")

    def test_ambiguity_does_not_mask_a_real_match(self, d):
        """A genuine match among namesakes still wins over the ambiguous one."""
        got, why = d._match(["Roulhac, M MD", "Roulhac, Maurice A MD\nIn network"], ROULHAC)
        assert why == "surname + first name" and got[0].startswith("Roulhac, Maurice")

    def test_results_header_is_not_a_provider(self, d):
        """Defect class G. Humana's results header is a child of the results-list container and echoes
        the search term, so "59 in network results for “Roulhac”" contains our surname while naming
        nobody. Read as a card it is a false IN; it must match nothing."""
        header = RESULTS_HEADER.replace("Smith", "Roulhac")
        assert d._match([header], ROULHAC)[0] is None

    def test_surname_only_in_the_card_body_decides_nothing(self, d):
        """The surname in the group/street but not in the name line means the layout is not what we
        think it is. Calling that an absence would be an OON read off a misparsed page."""
        card = "Jones, Robert MD\nFamily Medicine\n|\nRoulhac Family Clinic\nThornton, CO 80229"
        assert d._match([card], ROULHAC) == (None, "ambiguous")

    def test_missing_names_are_safe(self, d):
        assert d._match([], ROULHAC)[0] is None
        assert d._match([CARD_TIMOTHY], PortalQuery(payer_key="x", npi="0")) == (
            None, "no surname to match")

    def test_no_first_name_is_ambiguous_never_a_match(self, d):
        """With no first name from the 271 there is nothing to separate namesakes by, so a surname hit
        can neither be claimed nor dismissed."""
        q = PortalQuery(payer_key="humana-co", npi="1", provider_last_name="Smith")
        assert d._match([CARD_TIMOTHY], q) == (None, "ambiguous")


class TestStatedTotal:
    """Defect class C: completeness may never be inferred from the number of cards rendered.

    The old `_result_count` fell back to the rendered card count when the results header could not be
    read. That makes `cards_read >= total` trivially true, so the first search that rendered cards
    without our provider returned OUT_OF_NETWORK off a single page of a paginated list. Verified live:
    "Smith" states 59 results and renders 20.
    """

    def test_the_real_header_parses(self):
        assert _parse_total(RESULTS_HEADER) == 59

    def test_singular_and_thousands(self):
        assert _parse_total("1 in network result for “Roulhac”") == 1
        assert _parse_total("1,204 in network results for “Smith”") == 1204
        assert _parse_total("34 results for “Shah”") == 34

    @pytest.mark.parametrize("text", [
        "", None, "Sort by most relevant", "Print/email results",
        "Show\n10\n20\n30\nResults",          # the per-page control, not a total
        "Showing 20 of 59 results",           # not Humana's own wording — do not guess which number
        EMPTY_COPY,
    ])
    def test_anything_else_is_no_stated_total(self, text):
        """Anything that is not Humana's own "<N> [in network] results for …" wording yields None, and
        None means UNKNOWN. The parse is anchored at the start of the header for that reason: an
        unanchored search happily pulls a number out of unrelated copy, and the one number that must
        never be substituted for the total is the count of cards rendered."""
        assert _parse_total(text) is None


class TestTermEcho:
    """Defect class C: absence from an UNFILTERED or stale list is not absence.

    A submit click that silently fails leaves the previous search's results on screen. The results copy
    must name the term we just searched before that list is allowed to mean anything.
    """

    def test_the_term_we_searched_is_echoed(self):
        assert _echoes_term(RESULTS_HEADER, "Smith")
        assert _echoes_term(EMPTY_COPY, "Roulhac")

    def test_a_previous_search_does_not_echo_ours(self):
        assert not _echoes_term(RESULTS_HEADER, "Roulhac")
        assert not _echoes_term(RESULTS_HEADER, "Maurice Roulhac")

    def test_multi_token_terms_need_every_token(self):
        assert _echoes_term("2 in network results for “Maurice Roulhac”", "Maurice Roulhac")
        assert not _echoes_term("2 in network results for “Roulhac”", "Maurice Roulhac")

    def test_no_copy_is_no_echo(self):
        assert not _echoes_term("", "Smith")
        assert not _echoes_term(None, "Smith")


class TestReadCompleteness:
    """Defect classes C and D: only a provably whole, attributable result set may license an absence."""

    def _read(self, **kw):
        base = dict(term="Roulhac", total=3, cards=["a", "b", "c"], cards_ok=True, echoed=True)
        return _Read(**{**base, **kw})

    def test_a_whole_echoed_page_is_complete(self):
        assert self._read().complete

    def test_a_truncated_page_is_not_complete(self):
        """59 results, 20 cards: the live case. Absence from it proves nothing."""
        assert not self._read(total=59, cards=["x"] * 20).complete

    def test_no_stated_total_is_not_complete(self):
        assert not self._read(total=None).complete

    def test_an_empty_set_is_not_complete(self):
        """Doctrine: an empty result set is UNKNOWN, never OON."""
        assert not self._read(total=0, cards=[]).complete

    def test_a_failed_card_read_is_not_complete(self):
        """Defect class D: a swallowed exception must not hand back a partial list as if it were whole.
        `cards_ok=False` is how `_cards` reports a read that errored or hit its cap."""
        assert not self._read(cards_ok=False).complete

    def test_an_unattributed_list_is_not_complete(self):
        assert not self._read(echoed=False).complete


class TestNetworkList:
    """The network list comes from Humana's own session store, and its group headers are not networks.

    Verified live: `selectedNetwork` DEFAULTS to the header row {"networkId": 0, "networkName":
    "Medicare networks"}, so a header offered as a selectable network means pressing Select searches
    nothing while the walk reports success.
    """

    def test_headers_are_not_offered_as_networks(self, d):
        nets = d._network_list(_StubPage(CO_STORE))
        assert len(nets) == 11
        assert "Medicare networks" not in [n["name"] for n in nets]
        assert all(n["id"] for n in nets)

    def test_the_header_labels_the_line_of_business(self, d):
        nets = d._network_list(_StubPage(CO_STORE))
        assert {n["group"] for n in nets} == {"Medicare networks"}

    @pytest.mark.parametrize("raw", [
        None, "", "{}", "not json", '"a string"', "[]", '{"networks": null}',
        '{"networks": {"current": [null, 3, {"networkName": ""}]}}',
    ])
    def test_a_malformed_store_yields_nothing_rather_than_raising(self, d, raw):
        assert d._network_list(_StubPage(raw)) == []

    def test_an_unreadable_store_yields_nothing(self, d):
        assert d._network_list(_StubPage(PlaywrightError("evaluate blew up"))) == []


class TestNetworkPin:
    """Defect class E: a pin must be compared to the plan that was actually asked for.

    The old `_pick_network` scored shared words itself instead of using the shared, tested
    `plan_match.match_plan()` — the exact CareFlex mis-pin that module exists to prevent — and then let
    a word-overlap pin license an OUT_OF_NETWORK.
    """

    def _nets(self, *names, group="Medicare networks"):
        return [{"name": n, "group": group, "id": 100 + i} for i, n in enumerate(names)]

    def test_a_generic_medicare_plan_pins_nothing(self, d):
        """"Humana Medicare" shares only stop-words with every network in the market. Verified against
        the real CO list: nothing distinctive, so no pin and no absence reading."""
        nets = d._network_list(_StubPage(CO_STORE))
        pin = d._pick_network(nets, ROULHAC)
        assert pin.name is None and not pin.confirms
        assert "matched none" in pin.why

    def test_no_plan_pins_nothing(self, d):
        nets = d._network_list(_StubPage(CO_STORE))
        pin = d._pick_network(nets, PortalQuery(payer_key="humana-co", npi="1", plan=None))
        assert pin.name is None and not pin.confirms

    def test_no_networks_pins_nothing(self, d):
        assert d._pick_network([], ROULHAC).name is None

    def test_an_identifier_match_confirms_the_network(self, d):
        """Only a contract/PBP/market identifier may license an absence reading."""
        q = PortalQuery(payer_key="humana-fl", npi="1", plan="HumanaChoice H1036-123 (PPO)")
        pin = d._pick_network(self._nets("HumanaChoice FL H1036 (PPO)", "Medicare PPO"), q)
        assert pin.name == "HumanaChoice FL H1036 (PPO)" and pin.confirms

    def test_a_names_only_match_pins_but_does_not_confirm(self, d):
        q = PortalQuery(payer_key="humana-co", npi="1", plan="Humana Honor Freedom")
        pin = d._pick_network(self._nets("Humana Honor PPO", "Medicare PPO"), q)
        assert pin.name == "Humana Honor PPO" and not pin.confirms

    def test_a_product_type_conflict_vetoes_the_pin(self, d):
        """An HMO network cannot serve a PPO plan however many words they share. Pinning one would
        search a network the member is not in — and then read absence from it."""
        q = PortalQuery(payer_key="humana-co", npi="1", plan="Humana Honor PPO H1036")
        pin = d._pick_network(self._nets("Humana Honor HMO H1036"), q)
        assert pin.name is None and "different product" in pin.why

    def test_a_duplicated_network_name_cannot_be_identified(self, d):
        """Verified live: "Natl Med HMO Colorado-Home" appears twice under networkIds 3922 and 4037.
        Pinning by name searches one of them arbitrarily, so absence cannot be attributed."""
        nets = [{"name": "Natl Med HMO Colorado-Home H1036", "group": "Medicare networks", "id": 3922},
                {"name": "Natl Med HMO Colorado-Home H1036", "group": "Medicare networks", "id": 4037}]
        q = PortalQuery(payer_key="humana-co", npi="1", plan="Natl Med HMO Colorado-Home H1036")
        pin = d._pick_network(nets, q)
        assert pin.name is not None and pin.duplicate


class TestAbsenceLicence:
    """The doctrine, made executable: what must hold before absence may be called OUT_OF_NETWORK."""

    def _pin(self, **kw):
        from network_probe.portal.drivers.humana_finder import _Pin
        return _Pin(**{"name": "Medicare PPO", "why": "identifier match", "confirms": True, **kw})

    def _walk(self, **kw):
        return _Walk(reached=True, geo_echo="Thornton, CO 80229, USA", geo_ok=True, **kw)

    def test_a_confirmed_pin_in_the_right_market_licenses_absence(self, d):
        assert d._absence_blockers(self._pin(), self._walk()) == []

    def test_no_pin_blocks_absence(self, d):
        """Doctrine: no confirmed plan → UNKNOWN. The old driver instead argued that Humana's
        "all plans & networks" directory is a superset and returned OUT_OF_NETWORK with no plan
        confirmed at all — an unverified claim licensing the most dangerous verdict there is."""
        blockers = d._absence_blockers(self._pin(name=None, why="no plan came from the 271"),
                                      self._walk())
        assert any("no network was pinned" in b for b in blockers)

    def test_a_names_only_pin_blocks_absence(self, d):
        blockers = d._absence_blockers(self._pin(confirms=False, why="matched on 2 distinctive terms"),
                                      self._walk())
        assert any("names only" in b for b in blockers)

    def test_a_duplicated_network_name_blocks_absence(self, d):
        assert any("more than one network" in b
                   for b in d._absence_blockers(self._pin(duplicate=True), self._walk()))

    def test_the_wrong_market_blocks_absence(self, d):
        """The Places box can resolve a query to another state, and the portal then searches there.
        Its own address bar is the check: no clinic ZIP in it, no absence reading."""
        walk = _Walk(reached=True, geo_echo="Seattle, WA 98101, USA", geo_ok=False)
        assert any("does not contain the clinic ZIP" in b
                   for b in d._absence_blockers(self._pin(), walk))


class TestSearchTerms:
    def test_the_npi_is_never_typed_into_the_portal(self, d):
        """Whether Humana indexes the NPI was never established, and an unindexed identifier
        manufactures an empty result set that means nothing."""
        assert all(ROULHAC.npi not in term for term, _ in d._search_terms(ROULHAC))

    def test_surname_first_then_full_name(self, d):
        assert d._search_terms(ROULHAC) == [("Roulhac", "surname"), ("Maurice Roulhac", "full name")]

    def test_no_names_means_no_search(self, d):
        assert d._search_terms(PortalQuery(payer_key="humana-co", npi="1")) == []

    def test_a_surname_only_query_does_not_repeat_itself(self, d):
        q = PortalQuery(payer_key="humana-co", npi="1", provider_last_name="Roulhac")
        assert d._search_terms(q) == [("Roulhac", "surname")]

    def test_no_member_phi_is_ever_a_search_term(self, d):
        """Portals get provider name + clinic ZIP only."""
        terms = [t for t, _ in d._search_terms(ROULHAC)]
        assert terms == ["Roulhac", "Maurice Roulhac"]


class TestPlaceSuggestion:
    """Google Places reads a bare ZIP as a house number, so `zip in text` is not a ZIP check."""

    def test_the_real_suggestion_is_accepted(self, d):
        assert d._is_our_place("Thornton, CO 80229, USA", ROULHAC)

    def test_a_house_number_that_looks_like_the_zip_is_rejected(self, d):
        assert not d._is_our_place("33618 Samuel Ivy Drive, Tampa, FL 33619", SHAH)

    def test_a_neighbouring_zip_is_rejected(self, d):
        assert not d._is_our_place("Tampa, FL 33614, USA", SHAH)

    def test_the_right_zip_in_the_wrong_state_is_rejected(self, d):
        assert not d._is_our_place("Thornton, WA 80229, USA", ROULHAC)

    def test_no_zip_to_check_means_no_suggestion_is_trusted(self, d):
        q = PortalQuery(payer_key="humana-co", npi="1", city="Thornton", state="CO")
        assert not d._is_our_place("Thornton, CO 80229, USA", q)
