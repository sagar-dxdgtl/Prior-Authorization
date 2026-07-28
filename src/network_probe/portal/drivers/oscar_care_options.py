"""Oscar Care Options — www.hioscar.com/care-options (guest, no login).

Covers the Oscar rows of the client's test sheets (GA-Atlanta, FL-South Florida, FL-Tampa, AZ,
TX-Houston, NJ). We already have a JSON adapter for the same data (`payers/adapters/oscar.py`,
contract in docs/discovery/DISCOVERY.md), so this driver exists mainly to produce SCREENSHOT EVIDENCE
a human can read: Oscar's own search page showing the pinned plan and the provider in (or missing
from) that plan's in-network list.

Why plan-first is not optional here, stated as bluntly as the live data allows (re-verified
2026-07-28 against Oscar's own public JSON directory for coverage year 2026):

    Florida has THREE Oscar networks — 066 "Florida - HMO Standard", 070 "Florida - HMO Broad" and
    019 "Florida - EPO Off-Exchange" — and they DISAGREE about providers: searching "Herron" returns
    4 names in 066 and 019 but 5 in 070 (Christopher J Herron is in the Broad network only). Georgia
    likewise has two ("Individual Georgia HMO Open Access" 065 and "…Guided Care" 063). Search the
    wrong network of a state and the verdict inverts. So the network must be pinned from the member's
    plan, and when the plan cannot pin one the verdict is UNKNOWN — never a coin-flipped OON.

WHAT MAY LICENSE A VERDICT HERE (the part an adversarial review turned over, 2026-07-28)
---------------------------------------------------------------------------------------
The previous version of this driver confirmed the network whenever a state offered only ONE Oscar
network ("the state alone pins it") and whenever the member's plan string merely *resembled* a plan
name better than the runner-up. Neither is plan confirmation:

  * "this state has one network" is a fact about the wizard we managed to read, not about the member.
    The area/type probe is capped (`_MAX_AREAS`/`_MAX_TYPES`), skips combinations whose dropdowns
    fail to open, and only ever sees the "Oscar" network partner — so a lone combination can be an
    artefact of a truncated walk. It licensed an OUT_OF_NETWORK against a network nobody had shown
    the member to be in.
  * plan NAMES are not plan identity. That lesson is `portal/plan_match.py`'s whole reason to exist
    (a loose "AARP Medicare Advantage" pinned one of several AARP products purely on shared words).

So plan matching is delegated to the shared, tested `plan_match.match_plan()`, and only
`PlanMatch.confirms_network` — an *identifier* match (contract/PBP or a market code printed in both
the member's plan string and the portal's label) — may license a decisive verdict. A token-only match
pins the search (it decides which network the screenshot is of) but the verdict stays UNKNOWN, and no
match at all means the plan pin is display-only.

Consequence, stated plainly so nobody reads a bug into it and measured so nobody has to guess: Oscar's
ACA plan strings as they reach us ("Oscar Health", "Silver Simple PCP Saver CSR 150") carry no
contract or market identifier, and neither do Oscar's own labels — 0 of the 90 plan labels the three
Florida networks offer for 2026 contain an identifier `plan_match.identifiers()` recognises. Worse for
name matching, 18 of those 72 distinct plan names are sold by MORE THAN ONE Florida network ("AIAN Cost
Share", "Bronze Classic Standard", …), so even an exact name match cannot pin a network for a quarter
of them (hence `_decide`'s cross-network-duplicate guard). This driver's honest ceiling for Oscar's ACA
rows is therefore UNKNOWN plus a screenshot — the correct answer, not a regression. The missing input
is a plan identifier on BOTH sides: the member's 271/ID card and the portal label (for ACA that would
be the 14-character HIOS plan id, which Oscar's UUID policyId is not and its labels do not print).
Supply that on both sides and the decisive paths below open up unchanged.

Observed flow (live, re-driven 2026-07-28) — every step gated on the previous one:

    /care-options                     "Search network" button (guest lane; "Log in to search" is the
                                      member lane and is never used)
    /search/networks/                 cascading dropdowns, each appearing only once the previous is
                                      filled: Coverage year -> Network partner (auto-fills "Oscar")
                                      -> Coverage area -> Network type -> Plan
                                      then "Search in-network care options" (disabled until all set)
    /search/?networkId=070&state=FL&year=2026&policyId=ae4d0f9f-…&formularyPlanType=INDIVIDUAL_6_TIER
                                      ^ the pinned network AND the pinned plan's policyId are in the
                                      URL, and the page prints
                                      "2026 · Florida HMO Broad · Silver Simple Women's Health with
                                       Menopause Benefits · Change network & plan"
                                      Those two are cross-checked against what we selected
                                      (`_page_confirms_pin`): a breadcrumb naming a DIFFERENT plan
                                      used to confirm the pin, because only `bool(crumb)` was tested.

Traps that cost a live run, all recorded so nobody re-derives them:

  * The provider search box `#typeahead-input` is `o-input_hidden` and sits UNDER a
    `div[role=button].o-placeholder`. Clicking the input times out ("<span>Find in-network doctors,
    places, and more</span> … intercepts pointer events"); click the placeholder first.
  * Pressing Enter in the search box does NOT open a results page — it navigates to the top
    suggestion's profile. The suggestion list IS the result set. Never press Enter.
  * The dropdowns' `button[aria-haspopup=listbox]` is a 0x0 overlay and is never clickable. Open a
    dropdown by clicking its `[class*='o-visibleContent']`; the options render as `li[role=option]`
    in a portal elsewhere in the DOM.
  * The search page pre-fills a ZIP (30308, Atlanta) — the clinic ZIP must overwrite it, or the
    distance-sorted list is scoped to the wrong place.
  * Oscar's directory is NOT NPI-searchable (DISCOVERY.md: an NPI query returns 0 results), and no
    NPI appears anywhere on the profile page or in its source. Identity here is name-only; the NPI
    check belongs to the JSON adapter, which reads it from `doctor_name_fields.npi`.
  * A MULTI-WORD QUERY RETURNS NOTHING. Verified twice on 2026-07-28: "Desiree Clarke" answered
    "No results. Try another search." in the browser (network 070) and 0 rows from the autocomplete
    API in 066, 070 and 019, while the bare surname "Clarke" returned 10 in each. So a zero from a
    "First Last" query is a QUERY-FORM artefact, not absence — the old code used exactly that zero as
    proof that the 10-result cap was not hiding our provider, which is unsound. Only an exact-surname
    query can carry an absence, and only while it is under the cap.
  * The suggestion list is HARD-CAPPED at 10 (the same cap the JSON adapter guards against) and the
    UI shows all 10 (verified: 10 `a[href*='/people/']` rows for "Clarke"), so `count >= 10` is the
    truncation signal. Absence from a capped list proves nothing → UNKNOWN.

Verdict rules this driver actually implements (each one testable offline, tests/test_oscar_care_options.py):

    IN_NETWORK      a suggestion is our provider (surname as whole tokens + first-name agreement),
                    the row carries no contrary network label, AND the plan pin is identifier-grade
                    and corroborated by the answering page.
    OUT_OF_NETWORK  the plan pin is identifier-grade and corroborated, an exact-SURNAME query
                    returned between 1 and 9 names (populated, under the cap, so complete), and none
                    of them is our provider.
    UNKNOWN         everything else: no/weak plan pin, breadcrumb that does not name the pin, empty
                    result set, capped result set, a suggestion list we could not read to the end, or
                    a namesake we cannot tell apart from our provider.
    BLOCKED         the wizard could not be reached at all.

Roster keys this portal answers for: oscar-ga-atlanta, oscar-az, oscar-fl-south-florida,
oscar-fl-tampa, oscar-tx-houston, oscar-nj-vascular-health — one wizard serves them all. NB the key
is `oscar-ga-atlanta`, not `oscar-ga`: roster keys are slug(label)-slug(state) and the state column
carries a market suffix ("GA-Atlanta"), which `_areas_for_state` therefore tolerates.

Live evidence behind this file (2026-07-28, headless Chromium + the public JSON API, a handful of
lookups, no paging):
    wizard walk FL-South Florida → 3 coverage areas read → pinned "Florida - HMO Broad / HMO Open
        Access (Broad network…) / Silver Simple Women's Health with Menopause Benefits" (display-only
        pin: plan string "Oscar Health" resembles nothing), submit → networkId=070,
        breadcrumb "2026 · Florida HMO Broad · Silver Simple Women's Health with Menopause Benefits",
        URL policyId == the pinned option's id.
    "Clarke"          → 10 provider rows (AT the cap) in 070; the same 10 from the API in 066/070/019
    "Desiree Clarke"  → 0 rows, "No results. Try another search."
    "Herron"          → 5 rows in 070 (4 in 066/019) — a populated, under-cap list, i.e. the shape an
                        absence verdict would need.
    ⇒ Ground-truth row (Clarke, Desiree, NPI 1568423168, Lake Worth FL 33461, staff determination
      "Physician OON"): this portal cannot settle it. The plan string carries no identifier and the
      surname list is at the cap, so the driver answers UNKNOWN with the screenshot — it does NOT
      answer IN_NETWORK, and it no longer manufactures an OON out of a capped list plus a
      multi-word query that returns nothing.
"""

from __future__ import annotations

import re
from datetime import date

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus
from network_probe.portal.plan_match import match_plan

ENTRY = "https://www.hioscar.com/care-options"
NETWORKS = "https://www.hioscar.com/search/networks/"

# --- network-and-plan wizard (/search/networks/) -----------------------------------------------------
# Oscar's design system hashes its class names but keeps a readable `o-*` companion class on the same
# element, so `[class*='o-thing']` is the most durable handle available here — there is not a single
# data-testid on the page.
_WRAP = "[class*='o-dropdownWrapper']"
_FIELD = "[class*='o-visibleContent']"  # the clickable face of a dropdown
_OPTION = "li[role=option]"
_SUBMIT = "Search in-network care options"

# --- plan-pinned search page (/search/?networkId=…) --------------------------------------------------
_ZIP = "#zip-input"
_SEARCH_INPUT = "#typeahead-input"
# The clickable face that covers the real input until first use. Scoped to the search widget because
# the ZIP field renders a placeholder of its own and an unscoped `.first` could grab the wrong one.
_SEARCH_FACE = "[class*='o-multientityInput'] [class*='o-placeholder']"
_RESULTS = "[class*='o-resultsContainer']"
_PROVIDER_ROW = "a[href*='/people/']"  # provider suggestions only; facilities/drugs use other paths

# Oscar's autocomplete returns at most 10 rows and ignores every paging parameter (DISCOVERY.md), and
# the UI renders all 10 — so a rung whose count reaches this is POSSIBLY TRUNCATED and can never carry
# an absence. Verified 2026-07-28: "Clarke" = 10 rows in the browser and from the API in all three FL
# networks.
_SUGGEST_CAP = 10

# Cost ceilings for the combination probe. Oscar's widest state today is Florida with 3 coverage
# areas; these are generous enough for that and bound the click count if Oscar expands. NB these caps
# are precisely why "the state offers exactly one network" can never confirm a plan: a lone
# combination may simply be all we looked at.
_MAX_AREAS = 4
_MAX_TYPES = 3

# Recall floor used ONLY to describe a display-only pin ("resembles the member's plan" vs "chosen
# arbitrarily so the page renders"). It has no bearing on any verdict — verdict-grade plan matching is
# `plan_match.match_plan()`, and only its identifier tier licenses IN/OON.
_DISPLAY_RECALL = 0.6

# Oscar's 2026 footprint, read off the live Coverage-area dropdown (26 areas across these 20 states).
# The dropdown lists full state names ("Georgia", "Florida - HMO Standard", "Ohio with Cleveland
# Clinic"), while PortalQuery carries the postal code, so the two need bridging.
_STATE_NAMES = {
    "AL": "Alabama", "AZ": "Arizona", "FL": "Florida", "GA": "Georgia", "IA": "Iowa",
    "IL": "Illinois", "KS": "Kansas", "MI": "Michigan", "MO": "Missouri", "MS": "Mississippi",
    "NC": "North Carolina", "NE": "Nebraska", "NJ": "New Jersey", "NY": "New York", "OH": "Ohio",
    "OK": "Oklahoma", "PA": "Pennsylvania", "TN": "Tennessee", "TX": "Texas", "VA": "Virginia",
}

# Negative network phrasings, tested BEFORE any positive one because "not in network" CONTAINS
# "in network" — two sibling drivers returned a false IN over the payer's own contrary badge. Oscar's
# suggestion rows carry no badge today (verified: the rows are two lines of name and nothing else), so
# this is a veto that guards against a UI change, never the basis of an IN.
# Deliberately NOT matching the bare abbreviation "OON": this regex also separates badge lines from
# NAME lines in `_match`, and "Oon" is a real surname — a three-letter alarm word would delete the very
# row it was meant to annotate.
_NEG_BADGE = re.compile(
    r"\bnot\b[^.;|]{0,24}\bin[-\s]?network\b|\bout[-\s]?of[-\s]?network\b|\bnon[-\s]?network\b|"
    r"\bnon[-\s]?participating\b|\bno longer\b[^.;|]{0,24}\bnetwork\b|\bleaving the network\b|"
    r"\bnot\s+accepting\b",
    re.I,
)
_POS_BADGE = re.compile(r"\bin[-\s]?network\b|\bparticipating\b", re.I)


def _norm(s: str | None) -> str:
    """Alphabetic-only key, for NAMES."""
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _key(s: str | None) -> str:
    """Alphanumeric key, for LABELS. Digits are kept on purpose: Oscar bakes cost sharing and CSR
    variants into plan names ("… CSR 150 HMO $0 $5"), so an alpha-only key would make "CSR 150 $0 $5"
    and "CSR 250 $0 $0" — different plans in different networks — compare equal."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str | None) -> list[str]:
    # Single characters are dropped: Oscar bakes cost-sharing into plan names ("… HMO $0 $5"), and a
    # bare "0" matched almost every other plan, inflating the recall of plans that share nothing real.
    return [t for t in re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split() if len(t) >= 2]


def _name_tokens(s: str | None) -> list[str]:
    """Lowercase alphabetic name tokens, in order: "Rachel Cueto-Clarke" -> ['rachel','cueto','clarke']."""
    return [t for t in re.split(r"[^a-z]+", (s or "").lower()) if t]


def _label_tokens(s: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 3}


def _run_in(seq: list[str], sub: list[str]) -> int:
    """Index where `sub` appears as a contiguous run of whole tokens in `seq`, else -1. A substring
    test would let "Clarke" match "Clarkeson" and invent a network status for a stranger."""
    if not sub or len(sub) > len(seq):
        return -1
    for i in range(len(seq) - len(sub) + 1):
        if seq[i:i + len(sub)] == sub:
            return i
    return -1


def _plan_score(hint: str | None, candidate: str) -> tuple[float, float]:
    """(combined, candidate_recall) — resemblance only, used to choose which plan the SCREENSHOT shows
    when `match_plan` declines. Deliberately kept as the JSON adapter's arithmetic
    (`payers/adapters/oscar.py::_plan_match_score`) so the two agree about which plan looks closest,
    but it never decides a verdict: an unconfirmed pin caps the answer at UNKNOWN.
    """
    cand_toks, hint_toks = _tokens(candidate), _tokens(hint)
    if not hint_toks or not cand_toks:
        return (0.0, 0.0)
    hint_flat, cand_flat = " ".join(hint_toks), " ".join(cand_toks)
    cand_recall = sum(1 for t in cand_toks if t in hint_flat) / len(cand_toks)
    hint_recall = sum(1 for t in hint_toks if t in cand_flat) / len(hint_toks)
    return (cand_recall + hint_recall, cand_recall)


class OscarCareOptionsDriver(PortalDriver):
    key = "oscar-care-options"
    portal_name = "Oscar Care Options"

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail_box: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The walk trail travels with every verdict: a silently-partial walk that then reports OON
            # is the worst failure mode this layer has, and the trail makes it impossible to hide.
            if trail_box:
                note = f"{note} [portal walk: {' → '.join(trail_box)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        if not self._open_wizard(page, trail_box):
            return result(PortalStatus.BLOCKED, "could not reach Oscar's network-and-plan selector from "
                          f"{ENTRY} or {NETWORKS}.", screenshot=shot("nav-failed"))

        pin, walk = self._resolve_network(page, q)
        trail_box.extend(walk)
        if pin is None:
            return result(PortalStatus.UNKNOWN, "Oscar gates every provider search behind a pinned "
                          "network, and this member's plan could not be pinned — there is no unpinned "
                          "Oscar directory to fall back to, so no network verdict is possible.",
                          screenshot=shot("no-plan-pinned"))

        if not self._submit(page):
            return result(PortalStatus.UNKNOWN, f"pinned {pin['label']} but Oscar's "
                          f"'{_SUBMIT}' button never opened the search page.",
                          screenshot=shot("submit-failed"))
        # Both pins are in the URL Oscar navigated to: networkId (which network answered) and policyId
        # (which plan option). They are the machine-checkable half of the pin cross-check below.
        pin["network_id"] = self._url_param(page.url, "networkId")
        url_policy = self._url_param(page.url, "policyId")
        trail_box.append(f"search page: networkId={pin['network_id'] or '?'}")
        crumb = self._breadcrumb(page)

        # The pin is only as good as the page that answers. Test the breadcrumb's TEXT against what we
        # selected — `bool(crumb)` used to be the whole test, so a breadcrumb naming a different plan
        # confirmed the pin — and cross-check the URL's policyId against the option we clicked.
        page_ok, page_why = self._page_confirms_pin(crumb, url_policy, pin)
        trail_box.append(page_why)
        confirmed = bool(pin["confirmed"]) and page_ok
        if pin["confirmed"] and not page_ok:
            trail_box.append("identifier-grade plan pin WITHDRAWN: the answering page does not "
                             "corroborate it")

        found = self._search(page, q, trail_box, shot)
        if found is None:
            return result(PortalStatus.UNKNOWN, f"pinned {pin['label']} but the provider-search box "
                          "never became usable.", screenshot=shot("no-search-box"))

        where = f"{crumb or pin['label']} (networkId={pin['network_id'] or 'unknown'})"
        near = f" near {q.zip_code}" if q.zip_code else ""
        who = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p) or "the provider"
        name_only = (f"NB Oscar shows no NPI anywhere in this UI, so identity here is name-only "
                     f"(surname as whole tokens + first-name agreement); NPI {q.npi} is confirmed by "
                     f"the Oscar JSON adapter, not by this screenshot.")

        if found["match"]:
            if found["match_badge"] == "out":
                # Oscar's own contrary label on the row we matched. Never read as in-network.
                return result(
                    PortalStatus.OUT_OF_NETWORK if confirmed else PortalStatus.UNKNOWN,
                    f"Oscar lists {found['match']!r} in {where}{near} but labels that listing "
                    f"out-of-network in its own words, so presence is not participation"
                    + ("." if confirmed else f", and the network is not provably this member's: "
                                             f"{pin['why']}."),
                    result_count=found["match_count"], matched_name=found["match"],
                    screenshot=found["match_shot"],
                )
            if confirmed:
                return result(
                    PortalStatus.IN_NETWORK,
                    f"Oscar lists {found['match']!r} among the in-network suggestions for {where}"
                    f"{near}, matched on {found['match_term']!r} — Oscar's autocomplete is scoped to "
                    f"the pinned network (network_id is a required parameter), so this is Oscar's own "
                    f"statement that the provider is in that network's directory. Profile: "
                    f"{found['href'] or 'n/a'}. {name_only}",
                    result_count=found["match_count"], matched_name=found["match"],
                    screenshot=found["match_shot"],
                )
            return result(
                PortalStatus.UNKNOWN,
                f"Oscar lists {found['match']!r} in {where}{near}, but that is not provably this "
                f"member's network: {pin['why']}. Presence in one of a state's several Oscar networks "
                f"is not evidence for the network the member actually holds — settle it with the "
                f"plan name off the member's 271 or ID card. {name_only}",
                result_count=found["match_count"], matched_name=found["match"],
                screenshot=found["match_shot"],
            )

        if found["ambiguous"]:
            return result(
                PortalStatus.UNKNOWN,
                f"Oscar's suggestions for {found['searched']} in {where}{near} include a listing that "
                f"shares this surname but carries only an initial (or a longer compound surname) "
                f"where a given name would settle it, so it cannot be shown to be {who} rather than a "
                f"namesake. Neither in- nor out-of-network follows. {name_only}",
                result_count=found["report_count"], screenshot=found["report_shot"],
            )

        # Order matters: "we never read a list" must be answered before anything that talks about what
        # the results contained, or the note would claim an absence nobody observed.
        if not found["steps"]:
            return result(
                PortalStatus.UNKNOWN,
                f"no suggestion list in {where}{near} could be read to the end "
                f"({found['failure_note']}), so there is no result set to reason about. A partially "
                f"read list is not a result count.",
                screenshot=found["report_shot"],
            )
        if not confirmed:
            return result(
                PortalStatus.UNKNOWN,
                f"searched {where}{near} for {found['searched']} and {who} was not among the "
                f"results — but the member's network could not be pinned ({pin['why']}), and "
                f"absence from the wrong Oscar network is not out-of-network. Settle it with the "
                f"plan name off the member's 271 or ID card.",
                result_count=found["report_count"], screenshot=found["report_shot"],
            )
        if not found["any_results"]:
            return result(
                PortalStatus.UNKNOWN,
                f"Oscar returned no suggestions at all in {where}{near} for {found['searched']}. An "
                f"empty result set does not distinguish out-of-network from a query form Oscar does "
                f"not answer — verified live, its autocomplete returns nothing for any multi-word "
                f"name query.",
                result_count=0, screenshot=found["report_shot"],
            )
        if found["evidence"] is None:
            return result(
                PortalStatus.UNKNOWN,
                f"searched {where}{near} for {found['searched']} without finding {who}, but no result "
                f"list can carry that absence: {found['why_no_evidence']}. Returning UNKNOWN rather "
                f"than a possibly-wrong out-of-network.",
                result_count=found["report_count"], screenshot=found["report_shot"],
            )
        # Decisive absence: the plan pin is identifier-grade AND corroborated by the answering page,
        # and an exact-surname query came back populated but UNDER the cap — a complete list of the
        # in-network people with this surname, which does not include ours. Both the count and the
        # screenshot come from that one rung, never from a broader (possibly capped) one.
        return result(
            PortalStatus.OUT_OF_NETWORK,
            f"Oscar's in-network search for {where}{near} does not list {who} (NPI {q.npi}) — "
            f"out-of-network for this plan. {found['proof']}. {name_only}",
            result_count=found["evidence"]["count"], screenshot=found["evidence"]["shot"],
        )

    # --- entry -------------------------------------------------------------------------------------

    def _open_wizard(self, page: Page, trail: list[str]) -> bool:
        """Enter through the public /care-options page, as a staffer does, and take the guest lane."""
        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError):
            trail.append(f"{ENTRY} did not load")
        else:
            self._settle(page, 2_500)
            self._dismiss_overlays(page)
            try:
                # "Search network" = the guest lane. "Log in to search" is the member lane; never used.
                page.get_by_role("button", name="Search network", exact=True).first.click(timeout=8_000)
            except (PlaywrightTimeout, PlaywrightError):
                trail.append("'Search network' button not found on /care-options")
            else:
                self._settle(page, 3_000)
                if "/search/networks" in page.url:
                    trail.append("care-options → Search network")
                    return True

        try:  # the wizard is a real URL of its own, so a changed landing page is not fatal
            page.goto(NETWORKS, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 3_000)
        self._dismiss_overlays(page)
        trail.append("network selector opened directly")
        return "/search/networks" in page.url

    # --- the network-and-plan wizard ---------------------------------------------------------------

    def _resolve_network(self, page: Page, q: PortalQuery) -> tuple[dict | None, list[str]]:
        """Pin one Oscar network+plan and say whether that pin is verdict-grade.

        Returns (pin, trail). `pin["confirmed"]` is True only for an identifier-grade
        `plan_match.match_plan()` hit that belongs to exactly one of the state's networks;
        `pin["why"]` explains it either way. None means the wizard could not be driven at all — Oscar
        has no unpinned directory to degrade to.
        """
        trail: list[str] = []

        years = self._peek(page, "Coverage year")
        if not years:
            return None, trail + ["Coverage-year dropdown never appeared"]
        want = str(date.today().year)
        yi = next((i for i, (t, _) in enumerate(years) if t.strip() == want), 0)
        if not self._select(page, "Coverage year", yi):
            return None, trail + ["Coverage year not selectable"]
        year = years[yi][0].strip()
        trail.append(f"year: {year}")

        areas = self._peek(page, "Coverage area")
        if not areas:
            return None, trail + ["Coverage-area dropdown never appeared"]
        cands = self._areas_for_state(areas, q.state)
        if not cands:
            return None, trail + [f"no Oscar coverage area for state {q.state!r} "
                                  f"(offered: {len(areas)} areas)"]
        trail.append(f"coverage areas in {q.state}: {', '.join(t for _, t in cands)}")

        combos = self._probe_combos(page, q, cands, trail)
        if not combos:
            return None, trail + ["no (coverage area, network type) combination could be read"]

        decision = self._decide(q.plan, combos)
        if decision is None:
            return None, trail + ["no Oscar plan option could be read for any network in this state"]
        trail.append(decision["trail"])

        best = combos[decision["combo_i"]]
        # Whatever we concluded, drive the wizard to the chosen combination: even an unconfirmed pin is
        # worth a screenshot, and a match there is reported as UNKNOWN rather than IN.
        if not self._select(page, "Coverage area", best["area_i"]):
            return None, trail + [f"could not re-select coverage area {best['area']!r}"]
        if best["type_i"] is not None and not self._select(page, "Network type", best["type_i"]):
            return None, trail + [f"could not re-select network type {best['type']!r}"]
        if not self._select(page, "Plan", decision["plan_i"]):
            return None, trail + [f"could not select plan {decision['plan']!r}"]
        trail.append(f"pinned: {best['area']} / {best['type']} / {decision['plan']}")

        return {
            "confirmed": decision["confirmed"],
            "basis": decision["basis"],
            "why": decision["why"],
            "year": year,
            "area": best["area"],
            "type": best["type"],
            "plan": decision["plan"],
            "policy_id": decision["policy_id"],
            "network_id": None,  # filled from the search URL after submit
            "label": f"{best['area']} · {best['type']} · {decision['plan']}",
        }, trail

    def _decide(self, plan: str | None, combos: list[dict]) -> dict | None:
        """Choose the (network, plan) to drive and decide whether that choice is verdict-grade.

        Pure function over what the dropdowns said, so the doctrine is unit-testable offline.

        Verdict-grade means ALL of:
          * `plan_match.match_plan()` returned a match, and
          * that match is `confirms_network` (an identifier — contract/PBP or market code — shared by
            the member's plan string and the portal label; word overlap is explicitly not enough), and
          * the matched label belongs to exactly ONE of the state's networks, because a plan name that
            two networks both offer cannot pin either.

        Everything else yields a pin whose only job is to make the screenshot show something: the
        caller must, and does, cap such a capture at UNKNOWN. "This state offers exactly one network"
        is deliberately NOT a confirmation route — see the module docstring.
        """
        pool = [
            {"combo_i": ci, "plan_i": pi, "label": label, "policy_id": policy}
            for ci, c in enumerate(combos)
            for pi, (label, policy) in enumerate(c["plans"])
        ]
        if not pool:
            return None
        nets = len(combos)
        m = match_plan(plan, [p["label"] for p in pool])
        if m is not None:
            chosen = pool[m.index]
            homes = {p["combo_i"] for p in pool if _key(p["label"]) == _key(chosen["label"])}
            if len(homes) > 1:
                return self._decision(chosen, combos, False, "cross-network-duplicate", (
                    f"plan {plan!r} matched {chosen['label']!r} ({m.basis}) but "
                    f"{', '.join(sorted(combos[h]['label'] for h in homes))} all offer that same "
                    f"plan name, so it cannot pin one network"
                ))
            if m.confirms_network:
                return self._decision(chosen, combos, True, "identifier", (
                    f"plan {plan!r} pins {chosen['label']!r} in "
                    f"{combos[chosen['combo_i']]['label']} by {m.basis} — the only network of the "
                    f"{nets} read for this state that offers it"
                ))
            return self._decision(chosen, combos, False, "tokens", (
                f"plan {plan!r} only resembles {chosen['label']!r} in "
                f"{combos[chosen['combo_i']]['label']} by wording ({m.basis}) — that pins which "
                f"network this screenshot is of, but plan NAMES are not plan identity, so it licenses "
                f"no verdict"
            ))

        # No match at all: pick the closest-looking plan purely so the wizard can proceed and a human
        # gets a screenshot. This is display-only and is labelled as such in the note.
        ranked = sorted(
            (( _plan_score(plan, p["label"]), p) for p in pool),
            key=lambda pair: pair[0], reverse=True,
        )
        (score, recall), chosen = ranked[0]
        resembles = recall >= _DISPLAY_RECALL
        return self._decision(chosen, combos, False, "display-only", (
            f"plan {plan!r} matched no plan in any of the {nets} Oscar network(s) read for this state "
            f"({'; '.join(c['label'] for c in combos)}) — plan_match declined"
            + (f"; the closest by wording is {chosen['label']!r} (it recalls {recall:.0%} of its own "
               f"words from the plan string)" if resembles
               else f"; nothing resembles it — the closest option recalls only {recall:.0%} of its own "
                    f"words (combined score {score:.2f}), below the {_DISPLAY_RECALL:.0%} floor, so "
                    f"{chosen['label']!r} was pinned purely so the search page would render")
        ))

    def _decision(self, chosen: dict, combos: list[dict], confirmed: bool, basis: str,
                  why: str) -> dict:
        return {
            "combo_i": chosen["combo_i"], "plan_i": chosen["plan_i"],
            "plan": chosen["label"], "policy_id": chosen["policy_id"],
            "confirmed": confirmed, "basis": basis, "why": why,
            "trail": ("network pinned (identifier): " if confirmed
                      else f"network NOT confirmed ({basis}): ") + why,
        }

    def _probe_combos(self, page: Page, q: PortalQuery, cands, trail: list[str]) -> list[dict]:
        """Read every candidate (coverage area, network type) plan list — no submits, no lookups.

        Note what this can and cannot establish: it collects the plan lists we managed to read. It
        does NOT enumerate Oscar's offering (the probe is capped, skips combinations whose dropdowns
        refuse to open, and only ever sees the "Oscar" network partner), which is why the count of
        combinations returned is never treated as evidence about the member.
        """
        combos: list[dict] = []
        for area_i, area in cands[:_MAX_AREAS]:
            if not self._select(page, "Coverage area", area_i):
                trail.append(f"coverage area {area!r} not selectable")
                continue
            types = self._peek(page, "Network type")
            if not types:
                # Some areas expose no type step at all; the plan list then hangs off the area.
                types = [(self._value(page, "Network type") or "(single network type)", "")]
                type_idxs: list[int | None] = [None]
            else:
                type_idxs = list(range(min(len(types), _MAX_TYPES)))
            for ti in type_idxs:
                type_label = types[ti or 0][0]
                if ti is not None and len(types) > 1 and not self._select(page, "Network type", ti):
                    trail.append(f"network type {type_label!r} not selectable")
                    continue
                plans = self._peek(page, "Plan")
                if not plans:
                    trail.append(f"no plan list for {area} / {type_label}")
                    continue
                combos.append({
                    "area_i": area_i, "area": area,
                    "type_i": ti, "type": type_label,
                    # (visible plan name, the option id — which is the real policyId Oscar puts in the
                    # search URL, so it doubles as a cross-check on the page that answers)
                    "plans": [(self._plan_label(text), (opt_id or "").split("[")[0])
                              for text, opt_id in plans],
                    "label": f"{area} / {type_label}",
                })
        return combos

    def _plan_label(self, text: str) -> str:
        """A plan option's own name. The first option of each metal tier carries the tier heading
        ("Bronze\\nBronze AIAN HMO $0 $0"), which would otherwise pollute the token scores."""
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def _areas_for_state(self, areas, state: str | None) -> list[tuple[int, str]]:
        """Coverage areas belonging to the member's state. Oscar labels them by full state name, with
        a market suffix when a state has several ("Florida - HMO Broad", "Ohio with Cleveland Clinic").
        """
        raw = (state or "").strip()
        # The roster's state column carries a market suffix ("GA-Atlanta", "FL-South Florida",
        # "NJ-Vascular Health"), and those keys are what the payer_key is built from — so try the
        # leading segment before giving up. A full state name passes through unchanged.
        code = raw.split("-")[0].strip()
        name = _STATE_NAMES.get(code.upper()) or _STATE_NAMES.get(raw.upper()) or raw
        if not name:
            return []
        key = name.lower()
        return [(i, t) for i, (t, _) in enumerate(areas)
                if t.strip().lower() == key or t.strip().lower().startswith(f"{key} ")]

    # --- proving the pin survived to the page that answered ----------------------------------------

    def _url_param(self, url: str, name: str) -> str | None:
        m = re.search(rf"[?&]{re.escape(name)}=([^&]+)", url or "")
        return m.group(1) if m else None

    def _breadcrumb(self, page: Page) -> str | None:
        """Oscar's own statement of what it is searching: "2026 · <network> · <plan> · Change network
        & plan". Its presence is what turns a pinned URL into readable proof on the screenshot."""
        for line in self._page_text(page).splitlines():
            if "·" in line and "change network" in line.lower():
                return line.split("· Change")[0].strip(" ·")
        return None

    def _page_confirms_pin(self, crumb: str | None, url_policy: str | None, pin: dict) -> tuple[bool, str]:
        """Does the page that answered actually say it searched what we pinned?

        The defect this replaces: `confirmed = pin['confirmed'] and bool(crumb)`. `bool(crumb)` proves
        only that SOME breadcrumb rendered; its text was never compared to the pin, so a breadcrumb
        naming a different plan still confirmed. Now three independent things are checked, and any
        contradiction withdraws confirmation:

          * the breadcrumb names the PINNED PLAN (label equality on an alphanumeric key, so digits
            like "CSR 150 $0 $5" count; an ellipsis-truncated segment may match by long prefix);
          * the breadcrumb's network segment carries every distinguishing word of the pinned coverage
            area (Oscar prints "Florida HMO Broad" for the dropdown's "Florida - HMO Broad", and
            "Individual Georgia HMO Open Access" for "Georgia", so containment — not equality — is
            the honest test here, and the plan check is what separates two networks of one state);
          * the URL's policyId equals the option id we clicked, and the URL carries a networkId at all.
        """
        if not pin.get("network_id"):
            return False, "the search URL carried no networkId, so the answering network is unproven"
        policy = "unknown"
        if pin.get("policy_id") and url_policy:
            policy = "matches the pinned option" if _key(url_policy) == _key(pin["policy_id"]) \
                else "MISMATCHES the pinned option"
        if not crumb:
            return False, (f"no plan breadcrumb on the search page, so the pin is unverified on the "
                           f"page that answered (URL policyId {policy})")
        segs = [s.strip() for s in crumb.split("·") if s.strip()]
        plan_ok = any(self._same_label(s, pin.get("plan")) for s in segs)
        net_ok = any(self._names_network(s, pin.get("area")) for s in segs)
        year_segs = [s for s in segs if re.fullmatch(r"\d{4}", s)]
        year_ok = not year_segs or not pin.get("year") or any(s == pin["year"] for s in year_segs)
        ok = plan_ok and net_ok and year_ok and policy != "MISMATCHES the pinned option"
        detail = (
            f"breadcrumb {crumb!r} vs pin: plan "
            f"{'named' if plan_ok else 'NOT NAMED (breadcrumb describes a different plan)'}, network "
            f"{'consistent' if net_ok else 'INCONSISTENT'}, year "
            f"{'consistent' if year_ok else 'INCONSISTENT'}, URL policyId {policy}"
        )
        return ok, detail

    def _same_label(self, seg: str | None, label: str | None) -> bool:
        a, b = _key(seg), _key(label)
        if not a or not b:
            return False
        if a == b:
            return True
        # Oscar truncates a long label with an ellipsis in the breadcrumb; accept a prefix, but only a
        # substantial one, so "Silver…" cannot corroborate every Silver plan in the state.
        return bool((seg or "").rstrip().endswith(("…", "...")) and len(a) >= 12 and b.startswith(a))

    def _names_network(self, seg: str | None, area: str | None) -> bool:
        want = _label_tokens(area)
        return bool(want) and want <= _label_tokens(seg)

    # --- dropdown primitives -----------------------------------------------------------------------

    def _fields(self, page: Page) -> list[dict]:
        """Every dropdown currently rendered, with its label and chosen value."""
        try:
            return page.evaluate(
                """(sel) => [...document.querySelectorAll(sel)].map((w, i) => ({
                      i,
                      label: ((w.querySelector("[class*='o-dropdownLabel']")||{}).innerText||'').trim(),
                      value: ((w.querySelector("[class*='o-value']")||{}).innerText||'').trim(),
                   }))""",
                _WRAP,
            )
        except PlaywrightError:
            return []

    def _index(self, page: Page, label: str) -> int | None:
        return next((f["i"] for f in self._fields(page) if f["label"].lower() == label.lower()), None)

    def _value(self, page: Page, label: str) -> str | None:
        return next((f["value"] for f in self._fields(page) if f["label"].lower() == label.lower()), None)

    def _open(self, page: Page, label: str) -> bool:
        """Open one dropdown. The aria `button[aria-haspopup=listbox]` is a 0x0 overlay and never
        clickable — the clickable face is `[class*='o-visibleContent']`."""
        i = self._index(page, label)
        if i is None:
            return False
        try:
            page.locator(_WRAP).nth(i).locator(_FIELD).first.click(timeout=8_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        page.wait_for_timeout(1_200)  # the option list is portaled in after a beat
        try:
            return page.locator(_OPTION).count() > 0
        except PlaywrightError:
            return False

    def _close(self, page: Page, label: str) -> None:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            if page.locator(_OPTION).count():  # Escape ignored -> toggle the field shut
                i = self._index(page, label)
                if i is not None:
                    page.locator(_WRAP).nth(i).locator(_FIELD).first.click(timeout=5_000)
                    page.wait_for_timeout(400)
        except (PlaywrightTimeout, PlaywrightError):
            pass

    def _peek(self, page: Page, label: str) -> list[tuple[str, str]]:
        """Read a dropdown's options and leave it as we found it — closed, nothing selected."""
        if not self._open(page, label):
            return []
        loc = page.locator(_OPTION)
        out: list[tuple[str, str]] = []
        try:
            for i in range(loc.count()):
                out.append(((loc.nth(i).inner_text() or "").strip(), loc.nth(i).get_attribute("id") or ""))
        except PlaywrightError:
            pass
        self._close(page, label)
        return out

    def _select(self, page: Page, label: str, idx: int) -> bool:
        if not self._open(page, label):
            return False
        try:
            page.locator(_OPTION).nth(idx).click(timeout=8_000)
        except (PlaywrightTimeout, PlaywrightError):
            self._close(page, label)
            return False
        # Settle AFTER the click and never inside the same try: a slow settle must not be mistaken for
        # a failed selection (the mistake that made the first UHC walk disown a click it had made).
        self._settle(page, 2_000)
        return True

    def _submit(self, page: Page) -> bool:
        try:
            btn = page.get_by_role("button", name=_SUBMIT).first
            btn.wait_for(state="visible", timeout=10_000)
            if btn.is_disabled():
                return False
            btn.click()
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 4_000)
        return "networkId=" in page.url

    # --- the plan-pinned search page ---------------------------------------------------------------

    def _search(self, page: Page, q: PortalQuery, trail: list[str], shot) -> dict | None:
        """Run the term ladder against the pinned network and read the suggestion list.

        The suggestion list IS the result set — Oscar answers "who in this network matches what you
        typed" inline and has no results page (Enter navigates to the top suggestion's profile).

        Rung roles are not interchangeable, and this is the heart of the absence logic:
          * the exact SURNAME rung is the only one that can carry an absence, and only while it is
            populated and under the 10-row cap;
          * the "First Last" rung can only ever find PRESENCE. Oscar answers nothing for a multi-word
            query (verified live in the browser and against the API in three FL networks), so it is
            attempted only when the surname rung hit the cap, and its zero is never evidence.
        """
        if q.zip_code:
            # Before typing: the open suggestion panel overlaps the ZIP field, and Oscar pre-fills a
            # default ZIP that would otherwise scope the distance sort to the wrong city.
            self._set_zip(page, q.zip_code, trail)

        terms = self._terms(q)
        if not terms:
            return None
        steps: list[dict] = []
        failures: list[tuple[str, str]] = []
        for n, (term, kind) in enumerate(terms, start=1):
            if kind == "full name" and not self._should_narrow(steps):
                trail.append(f"skipped the {term!r} narrowing query: unnecessary (Oscar returns "
                             f"nothing for multi-word queries and the surname list was not capped)")
                continue
            rows, why = self._suggest(page, term)
            if rows is None:
                # A partial read is NOT a result set. The old code returned the rows collected so far
                # and the caller counted them, so a mid-read DOM detach could look like a short,
                # complete list — i.e. an absence.
                failures.append((term, why))
                trail.append(f"search {kind} {term!r}: {why} — rung DISCARDED, it contributes no "
                             f"result count")
                shot(f"search-{n}-{kind}-{why}")
                if why == "input-unusable" and not steps:
                    return None
                continue
            png = shot(f"search-{n}-{kind}")
            capped = len(rows) >= _SUGGEST_CAP
            name, href, why_m = self._match(rows, q)
            trail.append(
                f"search {kind} {term!r}: {len(rows)} suggestion(s)"
                + (f" — AT Oscar's {_SUGGEST_CAP}-row cap, so possibly truncated" if capped else "")
                + (" — Oscar printed 'No results'" if not rows and self._says_no_results(page) else "")
                + (f" — {why_m}" if not name else f" — matched {name!r} ({why_m})")
            )
            steps.append({
                "term": term, "kind": kind, "count": len(rows), "capped": capped, "shot": png,
                # only an exact-surname query can carry an absence (see the docstring)
                "decisive": kind == "surname",
                "match": name, "href": href, "match_why": why_m,
                "match_badge": self._badge(name),
            })
            if name or why_m == "ambiguous":
                break
        return self._fold(steps, failures)

    def _should_narrow(self, steps: list[dict]) -> bool:
        """Only worth typing a full name when the surname list was truncated and had no match."""
        return not steps or (steps[-1]["capped"] and not steps[-1]["match"])

    def _fold(self, steps: list[dict], failures: list[tuple[str, str]]) -> dict:
        """Turn the ladder's rungs into the numbers and the screenshot each verdict needs.

        The rung that licenses an OON must be populated, complete (under the cap) and an exact-surname
        query. `max(populated, key=count)` — the old choice — picks the BROADEST rung, which is
        usually the one that hit the cap, so both the proof sentence and `result_count` could describe
        a truncated list while the note claimed it was complete.
        """
        hit = next((s for s in steps if s["match"]), None)
        ambiguous = any(s["match_why"] == "ambiguous" for s in steps)
        usable = [s for s in steps if s["decisive"] and 0 < s["count"] < _SUGGEST_CAP]
        evidence = max(usable, key=lambda s: s["count"]) if usable else None
        populated = [s for s in steps if s["count"]]
        capped = [s for s in steps if s["capped"]]
        # What the UNKNOWN branches report: the decisive rung if there is one, else the most
        # informative rung we actually read to the end — the capped one when the cap is the reason we
        # are answering UNKNOWN. Never a discarded (partial) read, and the note always says which
        # query and whether it was at the cap.
        report = evidence or (max(capped, key=lambda s: s["count"]) if capped else None) \
            or (max(populated, key=lambda s: s["count"]) if populated else None) \
            or (steps[-1] if steps else None)
        searched = ", ".join(
            f"{s['term']!r} ({s['count']}{' — at the cap' if s['capped'] else ''})" for s in steps
        ) or "no readable query"
        proof = why_none = None
        if evidence:
            proof = (f"the exact-surname query {evidence['term']!r} returned {evidence['count']} "
                     f"in-network name(s) in this network — under Oscar's {_SUGGEST_CAP}-row "
                     f"autocomplete cap, so that list is Oscar's complete answer for this surname — "
                     f"and none of them is this provider")
        elif capped:
            why_none = (f"every populated suggestion list was AT Oscar's {_SUGGEST_CAP}-row cap, so it "
                        f"is truncated and the provider may simply be behind the cap; Oscar answers "
                        f"nothing at all for a multi-word name query, so there is no narrower query "
                        f"to run")
        elif populated:
            why_none = ("the only populated suggestion list came from a query form that cannot carry "
                        "an absence (only an exact-surname query would necessarily list this "
                        "provider), and no exact-surname query returned anything")
        return {
            "steps": steps,
            "failures": failures,
            "failure_note": "; ".join(f"{t!r}: {w}" for t, w in failures) or "no query was attempted",
            "match": hit["match"] if hit else None,
            "href": hit["href"] if hit else None,
            "match_term": hit["term"] if hit else None,
            "match_count": hit["count"] if hit else 0,
            "match_shot": hit["shot"] if hit else None,
            "match_badge": hit["match_badge"] if hit else None,
            "ambiguous": ambiguous,
            "any_results": bool(populated),
            "searched": searched,
            "evidence": evidence,
            "report_count": report["count"] if report else None,
            "report_shot": report["shot"] if report else None,
            "proof": proof,
            "why_no_evidence": why_none,
        }

    def _terms(self, q: PortalQuery) -> list[tuple[str, str]]:
        """Surname first, then "First Last" — but see `_search`: the second rung is a presence
        attempt for a capped list only, never absence evidence.

        The NPI is deliberately NOT tried: Oscar's directory is name-only and an NPI query returns 0
        results (DISCOVERY.md, verified live).
        """
        terms: list[tuple[str, str]] = []
        if q.provider_last_name:
            terms.append((q.provider_last_name.strip(), "surname"))
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        if full and full != (q.provider_last_name or "").strip():
            terms.append((full, "full name"))
        return terms

    def _set_zip(self, page: Page, zip_code: str, trail: list[str]) -> None:
        try:
            z = page.locator(_ZIP).first
            z.wait_for(state="visible", timeout=10_000)
            z.click()
            z.fill(zip_code)
            page.wait_for_timeout(1_500)
        except (PlaywrightTimeout, PlaywrightError):
            trail.append(f"ZIP field not settable (portal default kept) for {zip_code}")
            return
        trail.append(f"ZIP {zip_code}")

    def _suggest(self, page: Page, term: str) -> tuple[list[tuple[str, str]] | None, str]:
        """Type one term and read the provider suggestions.

        Returns (rows, "") for a list read to the end, or (None, reason) — and a reason is never a
        result set: "input-unusable" (could not type), "read-failed" (the DOM changed under the read,
        so what we have is a PREFIX of the list) or "read-truncated" (more rows than we will read).
        Absence from a partial list is not absence, so the caller discards such a rung entirely.
        """
        # First use: the input carries `o-input_hidden` and a div[role=button] placeholder sits on top
        # of it, whose <span> swallows the click ("intercepts pointer events"). Click the face first.
        # On later terms the face is gone, so this is best-effort rather than required.
        try:
            face = page.locator(_SEARCH_FACE).first
            if face.is_visible():
                face.click(timeout=8_000)
                page.wait_for_timeout(800)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            box = page.locator(_SEARCH_INPUT).first
            box.wait_for(state="visible", timeout=10_000)
            box.fill("")
            page.wait_for_timeout(400)
            box.type(term, delay=60)  # typed, not filled: the typeahead listens for key events
        except (PlaywrightTimeout, PlaywrightError):
            return None, "input-unusable"
        page.wait_for_timeout(4_000)  # debounce + the network-scoped autocomplete round trip

        limit = _SUGGEST_CAP * 2  # generous: Oscar caps at 10, and >limit means we cannot read it all
        rows: list[tuple[str, str]] = []
        try:
            loc = page.locator(f"{_RESULTS} {_PROVIDER_ROW}")
            total = loc.count()
            for i in range(min(total, limit)):
                rows.append(((loc.nth(i).inner_text() or "").strip(),
                             loc.nth(i).get_attribute("href") or ""))
        except (PlaywrightTimeout, PlaywrightError):
            return None, "read-failed"
        if total > limit:
            return None, "read-truncated"
        return rows, ""

    def _says_no_results(self, page: Page) -> bool:
        """Oscar's own empty state ("No results. Try another search."), which distinguishes a search
        that ran and found nobody from one we failed to read. Both are UNKNOWN; the note differs."""
        try:
            loc = page.locator(_RESULTS)
            if not loc.count():
                return False
            return "no results" in (loc.first.inner_text() or "").lower()
        except PlaywrightError:
            return False

    def _badge(self, text: str | None) -> str | None:
        """Any network label Oscar puts on a row. NEGATIVE phrasings are tested first because
        "not in network" CONTAINS "in network" — the substring trap that made two sibling drivers
        report a false IN over the payer's own contrary label."""
        if not text:
            return None
        if _NEG_BADGE.search(text):
            return "out"
        if _POS_BADGE.search(text):
            return "in"
        return None

    def _match(self, rows, q: PortalQuery) -> tuple[str | None, str | None, str]:
        """Is one of the suggestions OUR provider? Returns (name, href, why).

        Identity is derived from the QUERY, never from the search term (a term-derived expectation is
        the bare surname on the first rung, which leaves no first name to check and matches every
        namesake — the verified false IN in a sibling driver). Oscar renders a row as
        "<given names>\\n<surname>", so:

          * the surname must appear as WHOLE TOKENS and be the row's ENTIRE surname. A substring test
            would match "Clarkeson"; a containment test would accept "Clarke Jemmott" (verified live:
            searching "Clarke" in FL returns "Jacqueline Clarke Jemmott" and "Rachel Cueto-Clarke",
            two different people) — and Oscar wraps a hyphenated surname across the line break
            ("Rachel  Cueto-\\nClarke"), so a trailing hyphen moves that token to the surname side.
          * a known first name must AGREE: same token, or one a prefix of the other by 3+ characters
            so "Doug"/"Douglas" works both ways.
          * an initial-only card ("D. Clarke" for Desiree), or a longer compound surname that is
            otherwise compatible with us, decides NOTHING — it is reported "ambiguous" and the caller
            answers UNKNOWN. Returning it would be a false IN; treating it as absent would be a false
            OON while our provider may be the very row we skipped.

        Oscar's rows carry no credentials and no NPI today (verified live: two lines of name, nothing
        else), so there is deliberately no credential-suffix stripping — an unexpected row shape ends
        up "ambiguous" (UNKNOWN), which is the safe direction in both senses.
        """
        want_last = _name_tokens(q.provider_last_name)
        if not want_last:
            return None, None, "no surname in the query to match on"
        want_first = _name_tokens(q.provider_first_name)
        ambiguous = False
        for text, href in rows:
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            if not lines:
                continue
            # A network label on the row is not part of the name. Keep it out of the surname parse, but
            # the returned display name keeps every line so the caller can still read the label.
            name_lines = [ln for ln in lines if self._badge(ln) is None] or lines
            given, surname = self._split_row(name_lines, want_last)
            if surname is None:
                continue
            agrees = self._first_agrees(given, want_first)
            if agrees == "no":
                continue  # a different person with the same surname
            if surname == want_last:
                if agrees == "yes":
                    name = re.sub(r"\s+", " ", " ".join(lines)).strip()[:120]
                    return name, href, "surname + first name"
                ambiguous = True  # only an initial, or no first name in the query to check
            elif _run_in(surname, want_last) >= 0:
                ambiguous = True  # e.g. "Desiree Clarke Jemmott" for Desiree Clarke
        return None, None, "ambiguous" if ambiguous else "no suggestion matched"

    def _split_row(self, lines: list[str], want_last: list[str]) -> tuple[list[str], list[str] | None]:
        """(given tokens, surname tokens) for one suggestion row, or (…, None) when the surname is not
        in the row at all."""
        if len(lines) >= 2:
            given = _name_tokens(lines[0])
            surname = _name_tokens(" ".join(lines[1:]))
            if lines[0].rstrip().endswith("-") and given:
                # "Rachel  Cueto-\nClarke": the wrapped token belongs to the surname.
                surname = given[-1:] + surname
                given = given[:-1]
            return given, (surname if surname else None)
        toks = _name_tokens(lines[0])
        i = _run_in(toks, want_last)
        if i < 0:
            return toks, None
        return toks[:i], toks[i:]

    def _first_agrees(self, given: list[str], want_first: list[str]) -> str:
        """"yes" | "initial" (compatible but undecidable) | "no" | "unknown" (nothing to check)."""
        if not want_first:
            return "unknown"
        if not given:
            return "no"
        best = "no"
        for g in given:
            for f in want_first:
                if g == f and len(g) >= 2:
                    return "yes"
                if len(g) >= 3 and len(f) >= 3 and (g.startswith(f) or f.startswith(g)):
                    return "yes"
                if (len(g) == 1 and g == f[0]) or (len(f) == 1 and f == g[0]) or \
                        (g.startswith(f) or f.startswith(g)):
                    best = "initial"
        return best

    # --- shared plumbing ---------------------------------------------------------------------------

    def _dismiss_overlays(self, page: Page) -> None:
        """Cookie/consent overlays. Re-run after each navigation — they come back."""
        for sel in ("#onetrust-accept-btn-handler", "[id*='accept-recommended']",
                    "button[aria-label*='Close' i]"):
            try:
                b = page.locator(sel).first
                if b.is_visible():
                    b.click(timeout=4_000)
                    page.wait_for_timeout(800)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _settle(self, page: Page, pause_ms: int = 2_500) -> None:
        """Best-effort wait for the next step. Never raises: hioscar.com streams analytics and may
        never reach networkidle, and a settle timeout must never invalidate the click before it."""
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
