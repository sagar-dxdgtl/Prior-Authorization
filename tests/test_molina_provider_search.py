"""Defect regressions for the Molina (Zelis / Sapphire) portal driver.

Every test here pins a defect that was either found in this driver on its first review or shipped in a
sibling driver — they are systemic classes, not per-driver accidents. The fixtures are verbatim strings
from live pages captured on 2026-07-28 against molina.sapphirecareselect.com with the network pinned to
"TX - Texas STAR", so a test that passes here is a test against what the portal really renders.

The Ins Test 3 row: Clinton Twaddell, NPI 1437131901, clinic 5801 Oakbend Trail Suite 180,
Fort Worth TX 76152, plan "Molina Healthcare Texas STAR" (Managed Medicaid).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from network_probe.portal.drivers.molina_provider_search import (
    CARDS,
    NONE_HDR,
    NONE_SUB,
    RESULTS_FOR,
    RESULTS_HDR,
    Card,
    MolinaProviderSearchDriver,
    NetworkPin,
    ResultSet,
    plan_attested,
    plan_negated,
)
from network_probe.portal.models import PortalQuery

PLAN = "TX - Texas STAR"

TWADDELL = PortalQuery(
    payer_key="molina-healthcare-tx-dallas", npi="1437131901",
    provider_first_name="Clinton", provider_last_name="Twaddell",
    plan="Molina Healthcare Texas STAR", state="TX-Dallas", zip_code="76152",
)

# Verbatim live card text (NPI search, 2026-07-28).
CARD_OURS = (
    'Clinton W Twaddell, MD\nSurgery\nNPI: 1437131901\nUnited Vein And Vascular Centers\n'
    '1750 Broad Park Cir S Ste 300, Mansfield, TX 76063\nGet directions (est. 19.1 miles away)\n'
    'View More Locations\nPhone: 800-991-6117\nWebsite \nAccepting New Patients\n1\nAffiliation\n'
    'In "TX - Texas STAR" Plan/Program\nMatched on:PROVIDER IDENTIFIER'
)
# Verbatim live card text (surname "Garcia" control) — a different provider entirely.
CARD_STRANGER = (
    'Joanna Garcia, NP\nNeonatal Nurse Practitioner  Hospitalist\nNPI: 1235681834\nCompare\n'
    'Magella Medical Associates\n6100 Harris Pkwy, Fort Worth, TX 76132\n'
    'Get directions (est. 0.9 miles away)\nPhone: 817-433-5000\nWebsite \nAccepting New Patients\n1\n'
    'Affiliation\nIn "TX - Texas STAR" Plan/Program\nMatched on:NAME'
)

# The 8 in-state options the portal really offers for TX (of 90 nationwide), in portal order.
TX_POOL = [
    (66, "TX - Texas STAR"),
    (67, "TX - Texas STAR+PLUS"),
    (68, "TX - Molina Medicare Complete Care Plus (HMO D-SNP)"),
    (69, "TX - CHIP TDI"),
    (70, "TX - CHIP PERINATE"),
    (71, "TX - CHIP RSA"),
    (72, "TX - CHIP PERINATE RSA"),
    (73, "TX - Molina Medicare Complete Care (HMO D-SNP)"),
    (74, "TX - Molina Marketplace"),
]

# A results URL the portal really produced, with and without its radius already widened.
URL_EXPANDED = (
    "https://molina.sapphirecareselect.com/search/name/Twaddell?ci=molina&network_id=32"
    "&geo_location=32.670747,-97.415816&limit=10&radius=25&page=1&radiusExpanded=true"
)
URL_NARROW = (
    "https://molina.sapphirecareselect.com/search/name/Garcia?ci=molina&network_id=32"
    "&geo_location=32.670747,-97.415816&limit=10&radius=10&page=1"
)


_RAISE = object()  # sentinel: this card element raises on inner_text(), as a detached node does


class _FakeLoc:
    """The slice of Playwright's Locator that `_read_results` uses."""

    def __init__(self, texts):
        self._texts = texts

    def count(self):
        return len(self._texts)

    def nth(self, i):
        return _FakeEl(self._texts[i])

    @property
    def first(self):
        return _FakeEl(self._texts[0] if self._texts else "")


class _FakeEl:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        if self._text is _RAISE:
            raise PlaywrightError("Element is not attached to the DOM")
        return self._text


class _FakePage:
    """Just enough page to drive `_read_results` offline, keyed by the driver's real selectors."""

    def __init__(self, card_texts, header="", none_sub="", results_for="", url=URL_EXPANDED):
        self.url = url
        self._by_sel = {
            CARDS: card_texts,
            RESULTS_HDR: [header] if header else [],
            NONE_SUB: [none_sub] if none_sub else [],
            NONE_HDR: [],
            RESULTS_FOR: [results_for] if results_for else [],
        }

    def locator(self, sel):
        return _FakeLoc(self._by_sel.get(sel, []))


@pytest.fixture
def d():
    return MolinaProviderSearchDriver()


def card(d, text):
    return d._parse_card(text)


def complete_set(d, cards, term="Twaddell", url=URL_EXPANDED, total=None):
    """A result set that clears every completeness guard, so one guard can be isolated per test."""
    parsed = [card(d, c) for c in cards]
    return ResultSet(
        cards=parsed, total=total if total is not None else len(parsed),
        results_for=f'Results for: "{term}"', dom_count=len(parsed), read_complete=True, url=url,
    )


STRONG = NetworkPin(PLAN, "whole-token run", strong=True)


class TestBadgeInversion:
    """DEFECT CLASS A. `In "X" Plan/Program` is a SUBSTRING of `Not in "X" Plan/Program`, so a naive
    positive test reads an out-of-network row as in-network. Two sibling drivers shipped this."""

    def test_positive_phrase_is_attested(self):
        assert plan_attested(CARD_OURS, PLAN) is True
        assert plan_negated(CARD_OURS, PLAN) is False

    @pytest.mark.parametrize("phrase", [
        'Not in "TX - Texas STAR" Plan/Program',
        'Not In "TX - Texas STAR" Plan/Program',
        'NOT IN "TX - Texas STAR" Plan/Program',
        'No longer in "TX - Texas STAR" Plan/Program',
        'Non-participating in "TX - Texas STAR" Plan/Program',
        'Excluded in "TX - Texas STAR" Plan/Program',
        'Terminated in "TX - Texas STAR" Plan/Program',
        'Out of network in "TX - Texas STAR" Plan/Program',
        'Out-of-Network in "TX - Texas STAR" Plan/Program',
    ])
    def test_every_negative_phrasing_is_not_read_as_positive(self, phrase):
        """The whole defect class in one assertion: each of these CONTAINS the positive phrase."""
        assert plan_attested(phrase, PLAN) is False, "negative phrasing read as in-network"
        assert plan_negated(phrase, PLAN) is True

    def test_word_boundary_alone_would_not_have_saved_us(self):
        r"""Documents WHY the fix reads the preceding text rather than relying on `\bIn\b`: the word
        boundary matches the "In" inside "Not In" perfectly well."""
        import re
        naive = re.compile(r'\bIn\s+"TX - Texas STAR"\s+Plan/Program', re.I)
        assert naive.search('Not In "TX - Texas STAR" Plan/Program') is not None
        assert plan_attested('Not In "TX - Texas STAR" Plan/Program', PLAN) is False

    def test_a_negated_row_makes_the_driver_say_out_of_network(self, d):
        """The verdict-level consequence: a negated row must never become IN_NETWORK."""
        negated = CARD_OURS.replace('In "TX - Texas STAR"', 'Not in "TX - Texas STAR"')
        assert plan_negated(negated, PLAN) is True
        assert plan_attested(negated, PLAN) is False

    def test_a_different_network_does_not_attest_ours(self):
        assert plan_attested('In "TX - Texas STAR+PLUS" Plan/Program', PLAN) is False


class TestIdentity:
    """DEFECT CLASS B. Identity must come from the QUERY's NPI matched against the CARD's own NPI line —
    never from the search term, never from a name alone, never from an arbitrary first row."""

    def test_our_card_is_ours(self, d):
        assert d._is_ours(card(d, CARD_OURS), TWADDELL) is True

    def test_same_surname_stranger_is_not_ours(self, d):
        """"Garcia" returns 11 different Garcias in this network; a name hit is not an identity."""
        assert d._is_ours(card(d, CARD_STRANGER), TWADDELL) is False

    def test_matched_on_provider_identifier_does_not_make_row_zero_ours(self, d):
        """THE HEADLINE DEFECT FOUND IN THIS DRIVER.

        The reviewed code ended `_matched_name` with:
            if kind == "NPI" and "PROVIDER IDENTIFIER" in self._body(page).upper():
                return self._headline(cards[0])
        That returns an ARBITRARY first row as our provider on evidence that is not about identity at
        all — a page-wide string, not a card-scoped one. A keyword fallback that happens to render
        "Matched on:PROVIDER IDENTIFIER" for somebody else became a false IN_NETWORK.
        """
        stranger_with_identifier_badge = CARD_STRANGER.replace(
            "Matched on:NAME", "Matched on:PROVIDER IDENTIFIER"
        )
        assert d._is_ours(card(d, stranger_with_identifier_badge), TWADDELL) is False

    def test_npi_echoed_in_the_search_term_is_not_a_match(self, d):
        """The empty state renders "No results for 1437131901" — the page contains the NPI precisely
        because the portal could NOT find it. A term echo must never be identity."""
        echo = "No results for 1437131901\nFor TX - Texas STAR in 76132 or within 3500 miles max radius"
        assert d._is_ours(card(d, echo), TWADDELL) is False

    def test_npi_inside_a_longer_number_is_not_a_match(self, d):
        """The NPI is captured from the card's `NPI: <10 digits>` line, so a 10-digit run inside a phone
        number or a longer identifier cannot match."""
        assert d._is_ours(card(d, "Some Clinic\nPhone: 11437131901\nFax: 1437131901555"),
                          TWADDELL) is False

    def test_a_card_with_no_npi_line_is_not_a_provider_row(self, d):
        """DEFECT CLASS G: UI chrome and half-hydrated skeletons must not count as providers we ruled
        out. The NPI line hydrates seconds after the name."""
        chrome = card(d, "Sort By\nDistance\nAll Filters")
        assert chrome.is_provider_row is False
        assert d._is_ours(chrome, TWADDELL) is False

    def test_provider_rows_exclude_chrome_from_the_count(self, d):
        rs = ResultSet(cards=[card(d, CARD_OURS), card(d, "Map View\nAll Filters")])
        assert len(rs.cards) == 2 and len(rs.providers) == 1

    def test_missing_query_npi_can_never_match(self, d):
        assert d._is_ours(card(d, CARD_OURS), PortalQuery(payer_key="x", npi="")) is False


class TestAmbiguity:
    """DEFECT CLASS B, other half: an initial-only row must decide NOTHING — reporting absence while our
    provider may be the very row we skipped is a false OON."""

    def test_initial_only_same_surname_blocks_a_verdict(self, d):
        amb = 'C Twaddell, MD\nSurgery\nNPI: 9999999999\nIn "TX - Texas STAR" Plan/Program'
        why = d._ambiguous([card(d, amb)], TWADDELL)
        assert why and "initial" in why[0]

    def test_a_different_first_initial_is_not_ambiguous(self, d):
        """"R Twaddell" cannot be Clinton, so it does not block absence."""
        other = 'R Twaddell, MD\nSurgery\nNPI: 9999999999\nIn "TX - Texas STAR" Plan/Program'
        assert d._ambiguous([card(d, other)], TWADDELL) == []

    def test_our_own_row_is_not_flagged_ambiguous(self, d):
        assert d._ambiguous([card(d, CARD_OURS)], TWADDELL) == []

    def test_different_surname_is_not_ambiguous(self, d):
        assert d._ambiguous([card(d, CARD_STRANGER)], TWADDELL) == []

    def test_substring_surname_is_not_treated_as_our_surname(self, d):
        """Whole-token surname matching. Cigna's typeahead answered "Orem" with "Shoaf, Noremi D" —
        "noremi" contains "orem". Here: "Twaddells" and "MacTwaddell" are other people."""
        for name in ("Ann Twaddells, MD", "Bob MacTwaddell, MD"):
            row = f'{name}\nNPI: 9999999999\nIn "TX - Texas STAR" Plan/Program'
            assert d._ambiguous([card(d, row)], TWADDELL) == []
            assert d._filtered_to_provider(
                ResultSet(cards=[card(d, row)], results_for=""), "Twaddell", TWADDELL) is False


class TestNetworkPin:
    """DEFECT CLASS E. The pin must be compared to what was actually selected, and only a decisive
    identification may license an OON."""

    def test_texas_star_is_pinned_uniquely(self, d):
        pin, idx = d._best_network(TX_POOL, "Molina Healthcare Texas STAR")
        assert (pin.label, idx, pin.strong) == ("TX - Texas STAR", 66, True)

    def test_star_plus_wins_when_the_plan_really_says_star_plus(self, d):
        """"Texas STAR" is a token-run inside "Texas STAR+PLUS", so the LONGEST run must win —
        otherwise a STAR+PLUS member is checked against the plain STAR network."""
        pin, idx = d._best_network(TX_POOL, "Molina Healthcare Texas STAR+PLUS")
        assert (pin.label, idx) == ("TX - Texas STAR+PLUS", 67)

    def test_substring_containment_would_have_confused_the_two_networks(self):
        """Documents why whole-token runs replaced normalised-substring containment."""
        import re
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731 - the old, defective test
        assert norm("Texas STAR") in norm("Texas STAR+PLUS")  # the bug the token matcher removes

    def test_chip_rsa_does_not_swallow_chip_perinate_rsa(self, d):
        pin, _ = d._best_network(TX_POOL, "Molina Texas CHIP PERINATE RSA")
        assert pin.label == "TX - CHIP PERINATE RSA"

    def test_a_vague_plan_string_declines_rather_than_guessing(self, d):
        """The roster's own plan_line is literally "Managed Medicaid" — it identifies no Molina network,
        so the driver must decline and the verdict must stay UNKNOWN."""
        pin, idx = d._best_network(TX_POOL, "Managed Medicaid")
        assert pin.label is None and idx is None and pin.strong is False

    def test_no_plan_string_declines(self, d):
        pin, idx = d._best_network(TX_POOL, None)
        assert pin.label is None and idx is None

    def test_a_weak_plan_match_does_not_license_an_oon(self, d):
        """Doctrine: only a decisive identification may license absence-as-OON. `plan_match`'s own
        MEDIUM basis string says word overlap "does NOT license an out-of-network reading"."""
        weak = NetworkPin(PLAN, "plan_match medium: two distinctive terms", strong=False)
        blockers = d._oon_blockers(
            TWADDELL, complete_set(d, [CARD_STRANGER]), "Twaddell", weak, PLAN, "metro", "", "76132")
        assert any("pinned only weakly" in b for b in blockers)


class TestPlanConfirmationOnTheAnswerPage:
    """DEFECT CLASS E/F. Confirmation must compare the answer page to the plan actually pinned, not
    merely prove that something rendered."""

    def test_answer_page_naming_our_network_confirms(self, d):
        assert d._page_plan_text(complete_set(d, [CARD_STRANGER])).count(PLAN) == 1
        assert plan_attested(d._page_plan_text(complete_set(d, [CARD_STRANGER])), PLAN) is True

    def test_answer_page_naming_a_DIFFERENT_network_does_not_confirm(self, d):
        other = CARD_STRANGER.replace('TX - Texas STAR', 'TX - Texas STAR+PLUS')
        blockers = d._oon_blockers(
            TWADDELL, complete_set(d, [other]), "Twaddell", STRONG, PLAN, "metro", "", "76132")
        assert any("does not restate the pinned network" in b for b in blockers)

    def test_empty_state_subheader_attests_the_network(self, d):
        """The live empty state: `For TX - Texas STAR in 76132 or within 3500 miles max radius`."""
        rs = ResultSet(none_sub=f"For {PLAN} in 76132 or within 3500 miles max radius")
        assert PLAN in d._page_plan_text(rs)

    def test_attestation_note_never_quotes_another_networks_line(self, d):
        """DEFECT CLASS F: the reviewed `_attestation` fell back to a bare `For … in \\d{5}` regex, which
        happily quotes the empty state of a DIFFERENT network and presents it as this plan's proof."""
        foreign = Card(text="For AZ - Molina Complete Care in 85001 or within 3500 miles max radius",
                       headline="", npi=None, tokens=[])
        note = d._attestation(None, foreign, PLAN, False)
        assert "AZ - Molina Complete Care" not in note
        assert "did not restate the network" in note

    def test_attestation_quotes_our_own_row_when_present(self, d):
        note = d._attestation(None, card(d, CARD_OURS), PLAN, True)
        assert 'In "TX - Texas STAR" Plan/Program' in note


class TestCompletenessGuards:
    """DEFECT CLASSES C and D. Absence is evidence only from a set proven complete, name-filtered and
    network-wide."""

    def test_the_clean_case_produces_no_blockers(self, d):
        """The only shape that may become OUT_OF_NETWORK: radius already widened, portal total equals the
        rows read, list filtered to our surname, every row carrying an NPI, plan restated, geo proven."""
        rs = complete_set(d, [CARD_STRANGER], term="Twaddell")
        rs.cards[0] = card(d, CARD_STRANGER.replace("Joanna Garcia", "Ann Twaddell"))
        assert d._oon_blockers(TWADDELL, rs, "Twaddell", STRONG, PLAN, "metro", "", "76132") == []

    def test_truncated_page_one_cannot_support_absence(self, d):
        """LIVE-PROVEN: "Garcia" returned `11 Providers:` with 10 cards. Reading page 1 of a longer list
        as the whole network is a false OON — and paging through it is systematic downloading."""
        rs = complete_set(d, [CARD_STRANGER] * 10, term="Garcia", total=11)
        blockers = d._oon_blockers(TWADDELL, rs, "Garcia", STRONG, PLAN, "metro", "", "76132")
        assert any("reported 11 results but only 10 were read" in b for b in blockers)

    def test_missing_total_cannot_support_absence(self, d):
        """The reviewed code only blocked when `total > shown`; with `total is None` (the header regex
        missing, which is exactly what the empty state does) it read page 1 as complete."""
        rs = complete_set(d, [CARD_STRANGER])
        rs.total = None
        blockers = d._oon_blockers(TWADDELL, rs, "Twaddell", STRONG, PLAN, "metro", "", "76132")
        assert any("stated no result total" in b for b in blockers)

    def test_a_partial_read_cannot_support_absence(self, d):
        """DEFECT CLASS D: the reviewed `_read_results` wrapped the whole card loop in one
        `except PlaywrightError: pass`, so a failure on card 3 of 12 returned 3 cards and the caller
        counted them as the complete set."""
        rs = complete_set(d, [CARD_STRANGER])
        rs.read_complete, rs.dom_count = False, 12
        blockers = d._oon_blockers(TWADDELL, rs, "Twaddell", STRONG, PLAN, "metro", "", "76132")
        assert any("could not be read completely" in b for b in blockers)

    def test_read_results_marks_a_failed_card_read_incomplete(self, d):
        """The fix at the source: one unreadable card must not silently shrink the set.

        A card element detaching mid-read is the real-world case (this SPA re-renders the list while the
        radius ladder runs). The reviewed code swallowed it and returned a short list as if complete.
        """
        rs = d._read_results(_FakePage(card_texts=[CARD_STRANGER, _RAISE, CARD_STRANGER],
                                       header="3 Providers:"))
        assert rs.read_complete is False, "a failed card read was reported as a complete set"
        assert len(rs.cards) == 2 and rs.dom_count == 3
        # And the caller must refuse to read that as absence.
        assert any("could not be read completely" in b for b in
                   d._oon_blockers(TWADDELL, rs, "Twaddell", STRONG, PLAN, "metro", "", "76132"))

    def test_read_results_parses_the_real_total_and_cards(self, d):
        """The portal's own header wording, verbatim: "11 Providers:"."""
        rs = d._read_results(_FakePage(card_texts=[CARD_OURS], header="11 Providers:"))
        assert rs.total == 11 and rs.read_complete is True and len(rs.providers) == 1

    def test_read_results_flags_a_capped_read(self, d):
        """More cards than the read cap means the page was not fully read, so absence is unprovable."""
        rs = d._read_results(_FakePage(card_texts=[CARD_STRANGER] * 45, header="45 Providers:"))
        assert rs.read_complete is False and rs.dom_count == 45

    def test_a_radius_limited_set_cannot_support_absence(self, d):
        """LIVE-PROVEN: the portal widens its radius only when the in-radius search came back empty, and
        then stamps `radiusExpanded=true`. Without it the set covers 10 miles, not the network."""
        rs = complete_set(d, [CARD_STRANGER], term="Garcia", url=URL_NARROW)
        blockers = d._oon_blockers(TWADDELL, rs, "Garcia", STRONG, PLAN, "metro", "", "76132")
        assert any("radiusExpanded" in b for b in blockers)

    def test_radius_expanded_is_detected_on_the_real_url(self, d):
        assert d._radius_expanded(URL_EXPANDED) is True
        assert d._radius_expanded(URL_NARROW) is False
        assert d._radius_expanded("") is False

    def test_an_unfiltered_result_set_cannot_support_absence(self, d):
        """If the portal ignored the name and answered with a specialty/keyword list, absence from it
        says nothing about our provider. The echo or a whole-token surname must prove the filter."""
        rs = complete_set(d, [CARD_STRANGER])
        rs.results_for = ""  # no echo, and the rows are Garcias, not Twaddells
        blockers = d._oon_blockers(TWADDELL, rs, "Twaddell", STRONG, PLAN, "metro", "", "76132")
        assert any("not shown to be filtered" in b for b in blockers)

    def test_the_portals_own_echo_proves_the_filter(self, d):
        rs = ResultSet(cards=[card(d, CARD_STRANGER)], results_for='Results for: "Twaddell"')
        assert d._filtered_to_provider(rs, "Twaddell", TWADDELL) is True

    def test_chrome_rows_block_absence(self, d):
        rs = complete_set(d, [CARD_STRANGER, "Map View\nAll Filters"])
        rs.results_for = 'Results for: "Twaddell"'
        blockers = d._oon_blockers(TWADDELL, rs, "Twaddell", STRONG, PLAN, "metro", "", "76132")
        assert any("no readable name+NPI" in b for b in blockers)


class TestLocationGranularity:
    """The geography that scopes the search must be shown to be the clinic's, or absence means nothing."""

    def test_exact_clinic_zip_is_best(self, d):
        assert d._location_granularity("Fort Worth, TX — 76152", TWADDELL)[0] == "zip"

    def test_same_sectional_centre_counts_as_the_clinic_metro(self, d):
        """The clinic ZIP 76152 does not geocode on this portal (verified twice: it returns only "Use
        Current Location"). 76132 is the same 761xx sectional centre, i.e. the same metro."""
        assert d._location_granularity("Fort Worth, TX — 76132", TWADDELL)[0] == "metro"

    def test_a_far_away_texas_city_is_only_state_and_cannot_license_an_oon(self, d):
        """`_location_terms` falls back to the bare state code when the ZIP will not geocode, which can
        land on any Texas city. Absence 300 miles from the clinic is not absence near the clinic."""
        geo, why = d._location_granularity("Texarkana, TX — 75501", TWADDELL)
        assert geo == "state" and "only the state matched" in why
        blockers = d._oon_blockers(
            TWADDELL, complete_set(d, [CARD_STRANGER]), "Twaddell", STRONG, PLAN, geo, why, "Texarkana")
        assert any("not demonstrably the clinic's" in b for b in blockers)

    def test_the_portals_san_diego_default_is_rejected(self, d):
        """The landing URL arrives pinned to Molina's HQ geo. A CA reading for a TX clinic must never
        license an absence."""
        assert d._location_granularity("San Diego, CA — 92123", TWADDELL)[0] == "none"

    def test_no_location_is_rejected(self, d):
        assert d._location_granularity("", TWADDELL)[0] == "none"


class TestSearchTermsAndState:
    def test_market_suffixed_state_yields_the_bare_code(self, d):
        """The roster market for Fort Worth is "TX-Dallas"; the portal's network labels use "TX -"."""
        assert d._state_code(TWADDELL) == "TX"
        assert d._state_code(PortalQuery(payer_key="x", npi="1", state="TX")) == "TX"
        assert d._state_code(PortalQuery(payer_key="x", npi="1", state=None)) is None

    def test_search_terms_are_npi_then_surname_then_full_name(self, d):
        assert d._search_terms(TWADDELL) == [
            ("1437131901", "NPI"), ("Twaddell", "surname"), ("Clinton Twaddell", "full name"),
        ]

    def test_no_member_phi_is_ever_a_search_term(self, d):
        """Portals get provider name + clinic ZIP only. Nothing here can carry a member id or DOB."""
        terms = [t for t, _ in d._search_terms(TWADDELL)]
        assert terms == ["1437131901", "Twaddell", "Clinton Twaddell"]

    def test_location_terms_start_at_the_clinic_zip(self, d):
        assert d._location_terms(TWADDELL) == ["76152", "TX"]

    def test_location_terms_never_include_the_state_name(self, d):
        """"Texas" suggests "Texas County, MO" first — a silent out-of-state geocode."""
        assert "Texas" not in d._location_terms(TWADDELL)
