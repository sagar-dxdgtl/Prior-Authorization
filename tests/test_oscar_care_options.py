"""Regressions for the Oscar Care Options portal driver — one test per defect an adversarial review
proved on 2026-07-28 (recommendation: FIX_REQUIRED). All offline: the Playwright plumbing is scripted,
the verdict doctrine is the real code.

The four proved defects, and why each one mattered:

  1. FALSE DECISIVE. `if len(combos) == 1: confirmed = True` — "this state has exactly one Oscar
     network, so the state confirms it". A state's network count is a fact about the wizard we managed
     to read (the probe is capped, skips dropdowns that refuse to open, and only sees the "Oscar"
     network partner), not about the member. It licensed an OUT_OF_NETWORK against a network nobody
     had shown the member to be in.
  2. THE PIN WAS NEVER CROSS-CHECKED AGAINST THE PAGE THAT ANSWERED. `confirmed = pin['confirmed'] and
     bool(crumb)` — a breadcrumb naming a *different* plan confirmed the pin, because only its
     existence was tested.
  3. THE OON PROOF AND `result_count` COULD DESCRIBE A TRUNCATED LIST. `evidence = max(populated,
     key=count)` picks the broadest rung, which is normally the one at Oscar's 10-row cap, and the note
     then claimed that list was "complete for the query". Verified live: "Clarke" returns exactly 10
     rows (the cap) in all three Florida networks, while the narrowing query "Desiree Clarke" returns
     0 — Oscar answers nothing for any multi-word name query. The old code read that pair as
     "populated network + provider absent = OUT_OF_NETWORK".
  4. A SWALLOWED READ ERROR BECAME A RESULT COUNT. `except PlaywrightError: return rows` handed the
     caller a PREFIX of the suggestion list, which was then counted as a complete short list — i.e. as
     an absence.

Ground truth for the row this driver is scored on (client Test2 tab): Oscar Health FL, Clarke,
Desiree, NPI 1568423168, Lake Worth FL 33461 — staff determination "Physician OON". The portal cannot
settle it (no plan identifier + a capped surname list), so the honest answer is UNKNOWN; what matters
is that it is never IN_NETWORK and never an OON manufactured out of a truncated list.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from network_probe.portal.drivers.oscar_care_options import (
    _RESULTS,
    _SEARCH_INPUT,
    _SUGGEST_CAP,
    OscarCareOptionsDriver,
)
from network_probe.portal.models import PortalQuery, PortalStatus

# The Test2 row, as the capture layer builds it (provider + clinic ZIP only — never member PHI).
CLARKE = PortalQuery(
    payer_key="oscar-fl-south-florida", npi="1568423168",
    provider_first_name="Desiree", provider_last_name="Clarke",
    plan="Oscar Health", state="FL-South Florida", zip_code="33461",
)

SEARCH_URL = ("https://www.hioscar.com/search/?networkId=070&state=FL&year=2026"
              "&policyId=pid-broad-1&formularyPlanType=INDIVIDUAL_6_TIER")

# What the live breadcrumb looks like (verified 2026-07-28):
#   "2026 · Florida HMO Broad · Silver Simple Women's Health with Menopause Benefits"
PINNED_PLAN = "Silver Simple PCP Saver CSR 150 FL-0026"
GOOD_CRUMB = f"2026 · Florida HMO Broad · {PINNED_PLAN}"
OTHER_PLAN_CRUMB = "2026 · Florida HMO Broad · Bronze Simple HMO $0 $0"

# The ten rows Oscar actually returned for "Clarke" in network 070 on 2026-07-28 — i.e. AT the cap.
LIVE_CLARKE_ROWS = [
    ("Sabrina  \nClarke", "/people/d5ugGeirTiZ4SyjiViCN/?networkId=070"),
    ("Rachel  Cueto-\nClarke", "/people/rvewZgpCCAguqnfRYeLi/?networkId=070"),
    ("Matthew George \nClarke", "/people/BE2jnRq9qBB0y/?networkId=070"),
    ("Amy  \nClarke", "/people/pw8CXzYyHvw2g2YXjHsf/?networkId=070"),
    ("Mark A. \nClarke", "/people/42Bn9ohHrrJDKEWLK49C/?networkId=070"),
    ("Jacqueline  \nClarke\n Jemmott", "/people/MgaJcepLbq8BpCc5hvVG/?networkId=070"),
    ("Lauri Ann  \nClarke", "/people/244dec540c96faeb5663/?networkId=070"),
    ("Nickeisha  \nClarke", "/people/8SZgXEgCg8LbZRQ2fdJi/?networkId=070"),
    ("Kimberly L \nClarke", "/people/QD5CAU9HQva5DH9PadwW/?networkId=070"),
    ("Tanya Roxanne \nClarke", "/people/jF1X2u9vNj7P/?networkId=070"),
]
# A populated list UNDER the cap — the only shape that can carry an absence (live: "Herron" = 5 in 070).
FOUR_CLARKES = LIVE_CLARKE_ROWS[:1] + LIVE_CLARKE_ROWS[2:5]


def pin(**over) -> dict:
    """An identifier-grade pin, as `_resolve_network` would return it."""
    p = {
        "confirmed": True, "basis": "identifier",
        "why": "plan identifier match on FL-0026",
        "year": "2026", "area": "Florida - HMO Broad",
        "type": "HMO Open Access (Broad network, no referrals required)",
        "plan": PINNED_PLAN, "policy_id": "pid-broad-1", "network_id": None,
        "label": f"Florida - HMO Broad · HMO Open Access · {PINNED_PLAN}",
    }
    p.update(over)
    return p


# --- scripted portal -------------------------------------------------------------------------------


class _StubPage:
    def __init__(self, url: str = "https://www.hioscar.com/search/networks/") -> None:
        self.url = url


class ScriptedDriver(OscarCareOptionsDriver):
    """The real `capture()` over a scripted portal: navigation, dropdowns and typing are stubbed; the
    plan-confirmation, term-ladder, folding, identity and note logic are the shipped code."""

    def __init__(self, pinned: dict, crumb: str | None, answers: dict, url: str = SEARCH_URL) -> None:
        self.pinned, self.crumb, self.answers, self.search_url = pinned, crumb, answers, url

    def _open_wizard(self, page, trail):
        trail.append("care-options → Search network")
        return True

    def _resolve_network(self, page, q):
        return dict(self.pinned), [f"pin: {self.pinned['why']}"]

    def _submit(self, page):
        page.url = self.search_url
        return True

    def _breadcrumb(self, page):
        return self.crumb

    def _set_zip(self, page, zip_code, trail):
        trail.append(f"ZIP {zip_code}")

    def _suggest(self, page, term):
        return self.answers.get(term, ([], ""))

    def _says_no_results(self, page):
        return True


def run(pinned: dict, crumb: str | None, answers: dict, q: PortalQuery = CLARKE):
    return ScriptedDriver(pinned, crumb, answers).capture(_StubPage(), q, lambda label: f"{label}.png")


# --- defect 1: "one network in this state" is not plan confirmation --------------------------------


@pytest.fixture
def d() -> OscarCareOptionsDriver:
    return OscarCareOptionsDriver()


def combo(area: str, plans: list[tuple[str, str]], i: int = 0) -> dict:
    return {"area_i": i, "area": area, "type_i": 0, "type": "HMO Open Access",
            "plans": plans, "label": f"{area} / HMO Open Access"}


ONE_NETWORK = [combo("Florida - HMO Standard", [("Silver Simple PCP Saver CSR 150", "pid-1"),
                                                ("Bronze Simple HMO $0 $0", "pid-2")])]
TWO_NETWORKS = ONE_NETWORK + [
    combo("Florida - HMO Broad", [("Silver Simple Saver Guided Care CSR 150", "pid-3")], i=1),
]


class TestDecideNeverConfirmsOnNetworkCount:
    def test_single_network_state_does_not_confirm_the_plan(self, d):
        """Defect 1, the worst one: `len(combos) == 1 -> confirmed`. A lone combination can be all the
        capped probe managed to read, and it says nothing about which network the member bought — but
        it licensed an OUT_OF_NETWORK."""
        got = d._decide("Oscar Health", ONE_NETWORK)
        assert got["confirmed"] is False
        assert got["basis"] == "display-only"
        assert "matched no plan" in got["why"]

    def test_single_network_state_does_not_confirm_even_on_a_word_match(self, d):
        """Same shape, but now the plan string resembles a plan name. Still not confirmation: names are
        not plan identity (the whole reason portal/plan_match.py exists)."""
        got = d._decide("Silver Simple PCP Saver CSR 150", ONE_NETWORK)
        assert got["plan"] == "Silver Simple PCP Saver CSR 150"  # the search is pinned…
        assert got["confirmed"] is False                          # …but no verdict is licensed
        assert got["basis"] == "tokens"

    def test_identifier_match_is_the_only_thing_that_confirms(self, d):
        """The doctrine's one licensing route: an identifier (market code / contract) shared by the
        member's plan string and the portal label."""
        combos = [combo("Florida - HMO Standard", [("Silver Simple PCP Saver CSR 150 FL-0026", "p")])]
        got = d._decide("OSCAR SILVER SIMPLE PCP SAVER FL-0026", combos)
        assert got["confirmed"] is True
        assert got["basis"] == "identifier"

    def test_identifier_shared_by_two_networks_confirms_nothing(self, d):
        """A plan name (or identifier) that two of a state's networks both offer cannot pin either, so
        absence in the one we happened to drive is not absence from the member's."""
        dupe = [combo("Florida - HMO Standard", [("Silver Simple PCP Saver FL-0026", "p1")]),
                combo("Florida - HMO Broad", [("Silver Simple PCP Saver FL-0026", "p2")], i=1)]
        got = d._decide("Silver Simple PCP Saver FL-0026", dupe)
        assert got["confirmed"] is False
        assert got["basis"] == "cross-network-duplicate"

    def test_two_networks_and_no_plan_string_is_display_only(self, d):
        got = d._decide(None, TWO_NETWORKS)
        assert got["confirmed"] is False and got["basis"] == "display-only"

    def test_no_readable_plan_option_declines_entirely(self, d):
        assert d._decide("anything", [combo("Florida - HMO Broad", [])]) is None


def test_unconfirmed_pin_can_never_produce_out_of_network():
    """End to end: the exact situation defect 1 turned into an OON — a populated, under-cap surname list
    without our provider, in a network the plan string never identified."""
    cap = run(pin(confirmed=False, basis="display-only", why="plan 'Oscar Health' matched no plan"),
              GOOD_CRUMB, {"Clarke": (FOUR_CLARKES, "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert "not out-of-network" in cap.note
    assert cap.result_count == 4  # the count is still reported; it just does not license a verdict


def test_ground_truth_row_is_not_in_network():
    """The scored row (Clarke, Desiree — staff determination "Physician OON") must never come back
    IN_NETWORK. With no plan identifier and a capped surname list, UNKNOWN is the honest answer."""
    cap = run(pin(confirmed=False, basis="display-only", why="plan 'Oscar Health' matched no plan"),
              GOOD_CRUMB, {"Clarke": (LIVE_CLARKE_ROWS, ""), "Desiree Clarke": ([], "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert cap.status is not PortalStatus.IN_NETWORK


# --- defect 2: the pin must be cross-checked against the page that answered ------------------------


class TestPagePinCrossCheck:
    def test_breadcrumb_naming_a_different_plan_does_not_confirm(self, d):
        """Defect 2: `bool(crumb)` proved only that SOME breadcrumb rendered."""
        ok, why = d._page_confirms_pin(OTHER_PLAN_CRUMB, "pid-broad-1", pin(network_id="070"))
        assert ok is False and "NOT NAMED" in why

    def test_breadcrumb_naming_the_pin_confirms(self, d):
        ok, why = d._page_confirms_pin(GOOD_CRUMB, "pid-broad-1", pin(network_id="070"))
        assert ok is True and "plan named" in why

    def test_missing_breadcrumb_does_not_confirm(self, d):
        ok, why = d._page_confirms_pin(None, "pid-broad-1", pin(network_id="070"))
        assert ok is False and "no plan breadcrumb" in why

    def test_missing_network_id_does_not_confirm(self, d):
        ok, why = d._page_confirms_pin(GOOD_CRUMB, "pid-broad-1", pin(network_id=None))
        assert ok is False and "no networkId" in why

    def test_policy_id_mismatch_overrides_a_good_looking_breadcrumb(self, d):
        """Two independent statements of what was searched; a contradiction withdraws confirmation."""
        ok, why = d._page_confirms_pin(GOOD_CRUMB, "pid-standard-9", pin(network_id="070"))
        assert ok is False and "MISMATCHES" in why

    def test_plan_labels_differing_only_in_digits_are_not_the_same_plan(self, d):
        """Oscar bakes CSR level and cost sharing into plan names, and those variants live in different
        networks. An alpha-only key would make these compare equal."""
        assert d._same_label("Silver Simple CSR 150 HMO $0 $5", "Silver Simple CSR 250 HMO $0 $5") is False
        assert d._same_label("Silver Simple CSR 150 HMO $0 $5", "Silver Simple CSR 150 HMO $0 $0") is False
        assert d._same_label("Silver Simple CSR 150 HMO $0 $5", "Silver Simple CSR 150 HMO $0 $5") is True

    def test_ellipsis_truncated_breadcrumb_still_corroborates_by_long_prefix(self, d):
        ok, _ = d._page_confirms_pin(
            "2026 · Florida HMO Broad · Silver Simple PCP Saver CSR 1…", "pid-broad-1",
            pin(network_id="070"))
        assert ok is True

    def test_a_short_prefix_cannot_corroborate_everything(self, d):
        assert d._same_label("Silver…", PINNED_PLAN) is False

    def test_network_segment_is_matched_by_containment_not_equality(self, d):
        """Oscar prints its network NAME ("Florida HMO Broad") for the dropdown's area label
        ("Florida - HMO Broad"), and "Individual Georgia HMO Open Access" for the area "Georgia"."""
        assert d._names_network("Florida HMO Broad", "Florida - HMO Broad") is True
        assert d._names_network("Individual Georgia HMO Open Access", "Georgia") is True
        assert d._names_network("Individual Georgia HMO Open Access", "Florida - HMO Broad") is False

    def test_wrong_coverage_year_in_the_breadcrumb_does_not_confirm(self, d):
        ok, why = d._page_confirms_pin(f"2025 · Florida HMO Broad · {PINNED_PLAN}", "pid-broad-1",
                                       pin(network_id="070"))
        assert ok is False and "year INCONSISTENT" in why


def test_breadcrumb_mismatch_downgrades_a_decisive_absence_to_unknown():
    cap = run(pin(), OTHER_PLAN_CRUMB, {"Clarke": (FOUR_CLARKES, "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert "could not be pinned" in cap.note


def test_corroborated_identifier_pin_plus_a_complete_absence_is_out_of_network():
    """The one shape that may be an OON: identifier-grade pin, page corroborates it, an exact-surname
    query returned a populated list under the cap, and our provider is not in it."""
    cap = run(pin(), GOOD_CRUMB, {"Clarke": (FOUR_CLARKES, "")})
    assert cap.status is PortalStatus.OUT_OF_NETWORK
    assert cap.result_count == 4
    assert "exact-surname query 'Clarke' returned 4" in cap.note
    assert "portal walk:" in cap.note


# --- defect 3: neither the proof nor result_count may describe a truncated list --------------------


def rung(term: str, count: int, kind: str = "surname", match: str | None = None,
         why: str = "no suggestion matched") -> dict:
    return {"term": term, "kind": kind, "count": count, "capped": count >= _SUGGEST_CAP,
            "shot": f"{term}.png", "decisive": kind == "surname", "match": match, "href": None,
            "match_why": why, "match_badge": None}


class TestFoldEvidence:
    def test_a_capped_rung_is_never_the_evidence(self, d):
        """Defect 3: `max(populated, key=count)` chose the broadest rung — the capped one — and the
        note then called it "complete for the query"."""
        got = d._fold([rung("Clarke", 10), rung("Desiree Clarke", 0, kind="full name")], [])
        assert got["evidence"] is None
        assert got["proof"] is None

    def test_the_narrowing_zero_is_not_proof_that_the_cap_hid_nobody(self, d):
        """Live-verified: "Desiree Clarke" returns 0 in every Florida network while "Clarke" returns 10.
        The zero is a query-form artefact, so the pair must not read as "populated network + absent"."""
        got = d._fold([rung("Clarke", 10), rung("Desiree Clarke", 0, kind="full name")], [])
        assert got["any_results"] is True and got["evidence"] is None
        assert got["report_count"] == 10  # what we report is the capped rung, and the note says so

    def test_an_under_cap_surname_rung_is_the_evidence(self, d):
        got = d._fold([rung("Herron", 5)], [])
        assert got["evidence"]["count"] == 5
        assert "under Oscar's 10-row" in got["proof"]

    def test_a_full_name_rung_can_never_carry_an_absence(self, d):
        """Only an exact-surname query is a query our provider would necessarily match if listed."""
        got = d._fold([rung("Desiree Clarke", 3, kind="full name")], [])
        assert got["evidence"] is None

    def test_the_widest_complete_surname_rung_wins(self, d):
        got = d._fold([rung("Clarke", 4), rung("Clarke", 7)], [])
        assert got["evidence"]["count"] == 7

    def test_the_reason_there_is_no_evidence_is_recorded_truthfully(self, d):
        """The note must say WHY the absence is not usable, and the two reasons are different: a
        truncated list, or a list from a query form that cannot carry an absence at all."""
        capped = d._fold([rung("Clarke", 10)], [])
        assert "AT Oscar's 10-row cap" in capped["why_no_evidence"]
        loose = d._fold([rung("Desiree Clarke", 3, kind="full name")], [])
        assert "cannot carry an absence" in loose["why_no_evidence"]


def test_a_populated_non_surname_list_never_licenses_an_absence():
    """A rung that is populated and under the cap still proves nothing unless it is the exact-surname
    query — and the note says exactly that instead of blaming the cap."""
    no_surname = PortalQuery(payer_key="oscar-fl-south-florida", npi="1568423168",
                             provider_first_name="Desiree", plan="Oscar Health", state="FL",
                             zip_code="33461")
    cap = run(pin(), GOOD_CRUMB, {"Desiree": (FOUR_CLARKES[:3], "")}, q=no_surname)
    assert cap.status is PortalStatus.UNKNOWN
    assert "cannot carry an absence" in cap.note


def test_capped_surname_list_is_unknown_not_out_of_network():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": (LIVE_CLARKE_ROWS, ""), "Desiree Clarke": ([], "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert cap.result_count == _SUGGEST_CAP
    assert "AT Oscar's 10-row" in cap.note
    assert "complete answer" not in cap.note  # the claim the old note made about a truncated list


def test_empty_result_set_is_unknown():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": ([], "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert cap.result_count == 0
    assert "empty result set" in cap.note


# --- defect 4: a swallowed read error must not become a result count -------------------------------


class _Row:
    def __init__(self, text: str, href: str, raises: bool = False) -> None:
        self._text, self._href, self._raises = text, href, raises

    def inner_text(self):
        if self._raises:
            raise PlaywrightError("Element is not attached to the DOM")
        return self._text

    def get_attribute(self, _name):
        return self._href


class _List:
    def __init__(self, rows) -> None:
        self._rows = rows

    def count(self):
        return len(self._rows)

    def nth(self, i):
        return self._rows[i]

    @property
    def first(self):
        return self._rows[0]


class _Box:
    def __init__(self, visible: bool = True) -> None:
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def click(self, **_kw):
        pass

    def wait_for(self, **_kw):
        pass

    def fill(self, _v):
        pass

    def type(self, _v, **_kw):
        pass


class _SuggestPage:
    """Just enough Page for `_suggest`: a hidden placeholder face, a typable box, a row list."""

    def __init__(self, rows) -> None:
        self.url = SEARCH_URL
        self._rows = rows

    def locator(self, selector: str):
        if "o-placeholder" in selector:
            return _Box(visible=False)
        if selector == _SEARCH_INPUT:
            return _Box()
        assert _RESULTS in selector
        return _List(self._rows)

    def wait_for_timeout(self, _ms):
        pass


class TestSuggestReadIsAllOrNothing:
    def test_a_mid_read_detach_returns_no_rows_at_all(self, d):
        """Defect 4: `except PlaywrightError: return rows` returned the PREFIX collected so far, and
        the caller counted it — a truncated list masquerading as a complete short one, i.e. an
        absence."""
        rows = [_Row("A \nClarke", "/people/1"), _Row("B \nClarke", "/people/2"),
                _Row("C \nClarke", "/people/3", raises=True), _Row("D \nClarke", "/people/4")]
        got, why = d._suggest(_SuggestPage(rows), "Clarke")
        assert got is None and why == "read-failed"

    def test_a_complete_read_returns_every_row(self, d):
        rows = [_Row("Sabrina  \nClarke", "/people/1"), _Row("Amy  \nClarke", "/people/2")]
        got, why = d._suggest(_SuggestPage(rows), "Clarke")
        assert why == "" and got == [("Sabrina  \nClarke", "/people/1"), ("Amy  \nClarke", "/people/2")]

    def test_more_rows_than_we_will_read_is_discarded_not_counted(self, d):
        rows = [_Row(f"P{i} \nClarke", f"/people/{i}") for i in range(_SUGGEST_CAP * 2 + 1)]
        got, why = d._suggest(_SuggestPage(rows), "Clarke")
        assert got is None and why == "read-truncated"


def test_a_discarded_rung_contributes_no_count_and_no_verdict():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": (None, "read-failed"),
                                  "Desiree Clarke": (None, "read-failed")})
    assert cap.status is PortalStatus.UNKNOWN
    assert cap.result_count is None
    assert "no suggestion list" in cap.note and "could be read to the end" in cap.note
    assert "rung DISCARDED" in cap.note  # the walk trail says so too


def test_an_unusable_search_box_is_unknown_not_absence():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": (None, "input-unusable")})
    assert cap.status is PortalStatus.UNKNOWN
    assert "never became usable" in cap.note


# --- identity: expectation from the QUERY, whole-token surname, initials decide nothing ------------


class TestIdentity:
    def test_our_provider_matches(self, d):
        name, href, why = d._match([("Desiree  \nClarke", "/people/x")], CLARKE)
        assert name == "Desiree Clarke" and href == "/people/x" and why == "surname + first name"

    def test_same_surname_stranger_is_not_a_match(self, d):
        """Every one of these is a real row Oscar returned for "Clarke" in FL, and none is our
        provider. A term-derived expectation (the bare surname) matched them all."""
        name, _, why = d._match(LIVE_CLARKE_ROWS, CLARKE)
        assert name is None and why == "no suggestion matched"

    def test_substring_surname_is_rejected(self, d):
        assert d._match([("Desiree  \nClarkeson", "/p")], CLARKE)[0] is None
        assert d._match([("Desiree  \nMcClarke", "/p")], CLARKE)[0] is None

    def test_initial_only_row_decides_nothing(self, d):
        """"D. Clarke" could be Desiree or Denise. Returning it is a false IN; skipping it silently is a
        false OON while our provider may be the very row we skipped."""
        name, _, why = d._match([("D. \nClarke", "/p")], CLARKE)
        assert name is None and why == "ambiguous"

    def test_a_longer_compound_surname_is_ambiguous_not_a_match(self, d):
        """Live rows: "Jacqueline Clarke Jemmott" and the line-wrapped "Rachel  Cueto-\\nClarke". If the
        given name is compatible with ours we cannot tell a compound surname apart from ours."""
        assert d._match([("Desiree  \nClarke\n Jemmott", "/p")], CLARKE)[2] == "ambiguous"
        assert d._match([("Desiree  Cueto-\nClarke", "/p")], CLARKE)[2] == "ambiguous"

    def test_a_compound_surname_with_a_different_given_name_is_simply_absent(self, d):
        """Jacqueline is not Desiree, so that row needs no hedging — it is somebody else."""
        rows = [("Jacqueline  \nClarke\n Jemmott", "/p"), ("Rachel  Cueto-\nClarke", "/p")]
        assert d._match(rows, CLARKE) == (None, None, "no suggestion matched")

    def test_middle_names_and_initials_on_the_row_still_match(self, d):
        for row in ("Desiree A. \nClarke", "Desiree Ann \nClarke", "Des \nClarke"):
            assert d._match([(row, "/p")], CLARKE)[0] is not None, row

    def test_expectation_comes_from_the_query_not_the_search_term(self, d):
        """The structural fix: whatever term produced the rows, identity is checked against the query."""
        rows = [("Sabrina  \nClarke", "/p")]
        assert d._match(rows, CLARKE)[0] is None
        sabrina = PortalQuery(payer_key="x", npi="0", provider_first_name="Sabrina",
                              provider_last_name="Clarke")
        assert d._match(rows, sabrina)[0] is not None

    def test_a_query_without_a_first_name_decides_nothing(self, d):
        anon = PortalQuery(payer_key="x", npi="0", provider_last_name="Clarke")
        assert d._match([("Sabrina  \nClarke", "/p")], anon)[2] == "ambiguous"

    def test_missing_names_are_safe(self, d):
        assert d._match([("Desiree  \nClarke", "/p")], PortalQuery(payer_key="x", npi="0"))[0] is None
        assert d._match([], CLARKE)[0] is None
        assert d._match([("", "")], CLARKE)[0] is None


def test_an_ambiguous_namesake_is_unknown_end_to_end():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": ([("D. \nClarke", "/p")] + FOUR_CLARKES, "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert "namesake" in cap.note


def test_a_confirmed_match_is_in_network():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": (FOUR_CLARKES + [("Desiree  \nClarke", "/people/me")], "")})
    assert cap.status is PortalStatus.IN_NETWORK
    assert cap.matched_name == "Desiree Clarke"
    assert "name-only" in cap.note  # the note never claims the NPI was seen on this screen


def test_a_match_in_an_unconfirmed_network_is_unknown():
    cap = run(pin(confirmed=False, why="plan 'Oscar Health' matched no plan"), GOOD_CRUMB,
              {"Clarke": ([("Desiree  \nClarke", "/people/me")], "")})
    assert cap.status is PortalStatus.UNKNOWN
    assert "not provably this member's network" in cap.note


# --- network badges: every negative phrasing before any positive one ------------------------------


class TestBadge:
    @pytest.mark.parametrize("text", [
        "Not In Network", "not in network", "NOT IN-NETWORK", "is not currently in network",
        "Out of Network", "out-of-network", "Non-Network", "non participating",
        "No longer in this network", "Leaving the network", "not accepting new patients",
        "Desiree Clarke — Not In Network — Lake Worth, FL",
    ])
    def test_every_negative_phrasing_reads_out(self, d, text):
        """"not in network" CONTAINS "in network": a positives-first test returns "in" and the caller
        then reports IN over the payer's own contrary label. Two sibling drivers did exactly that."""
        assert d._badge(text) == "out", f"{text!r} must read as out-of-network"

    @pytest.mark.parametrize("text", ["In Network", "in-network", "IN NETWORK", "participating"])
    def test_positive_phrasings_read_in(self, d, text):
        assert d._badge(text) == "in"

    def test_a_plain_name_row_carries_no_badge(self, d):
        """Oscar's rows are name-only today, so a badge must never be invented from a name."""
        assert d._badge("Desiree  \nClarke") is None
        assert d._badge("Oon  \nClarke") is None  # "OON" as a bare word is deliberately not a badge
        assert d._badge("") is None


def test_a_contrary_row_label_is_never_read_as_in_network():
    cap = run(pin(), GOOD_CRUMB, {"Clarke": ([("Desiree  \nClarke\nNot in network", "/p")], "")})
    assert cap.status is PortalStatus.OUT_OF_NETWORK
    assert "labels that listing out-of-network" in cap.note


def test_a_contrary_row_label_in_an_unconfirmed_network_is_unknown():
    cap = run(pin(confirmed=False, why="no identifier"), GOOD_CRUMB,
              {"Clarke": ([("Desiree  \nClarke\nNot in network", "/p")], "")})
    assert cap.status is PortalStatus.UNKNOWN


# --- the term ladder and state bridging -----------------------------------------------------------


class TestTermLadder:
    def test_surname_comes_first_and_the_npi_is_never_typed(self, d):
        assert d._terms(CLARKE) == [("Clarke", "surname"), ("Desiree Clarke", "full name")]
        assert all(CLARKE.npi not in t for t, _ in d._terms(CLARKE))

    def test_the_narrowing_query_is_only_attempted_when_the_surname_list_was_capped(self, d):
        assert d._should_narrow([]) is True
        assert d._should_narrow([rung("Clarke", 10)]) is True
        assert d._should_narrow([rung("Clarke", 4)]) is False
        assert d._should_narrow([rung("Clarke", 10, match="Desiree Clarke")]) is False

    def test_an_uncapped_surname_list_skips_the_second_lookup(self):
        """Human-scale: no second round trip when the first list was already complete."""
        drv = ScriptedDriver(pin(), GOOD_CRUMB, {"Clarke": (FOUR_CLARKES, ""),
                                                 "Desiree Clarke": (LIVE_CLARKE_ROWS, "")})
        cap = drv.capture(_StubPage(), CLARKE, lambda label: f"{label}.png")
        assert cap.status is PortalStatus.OUT_OF_NETWORK
        assert "skipped the 'Desiree Clarke' narrowing query" in cap.note


class TestAreasForState:
    AREAS = [("Florida - EPO Off-Exchange", ""), ("Florida - HMO Broad", ""),
             ("Florida - HMO Standard", ""), ("Georgia", ""), ("Ohio with Cleveland Clinic", "")]

    def test_roster_market_suffix_is_tolerated(self, d):
        got = d._areas_for_state(self.AREAS, "FL-South Florida")
        assert [t for _, t in got] == ["Florida - EPO Off-Exchange", "Florida - HMO Broad",
                                       "Florida - HMO Standard"]

    def test_plain_state_code(self, d):
        assert [t for _, t in d._areas_for_state(self.AREAS, "GA-Atlanta")] == ["Georgia"]

    def test_unknown_state_yields_nothing(self, d):
        assert d._areas_for_state(self.AREAS, "ZZ") == []
