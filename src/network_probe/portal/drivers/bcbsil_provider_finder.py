"""BCBS Illinois (HCSC) provider-network check via the BCBSA National Doctor and Hospital Finder.

Covers the Ins Test 3 "BCBS Illinois" row. Everything below was observed live on 2026-07-28.

WHY NOT BCBSIL's OWN GUEST FINDER
---------------------------------
`www.bcbsil.com/find-care/find-a-doctor-or-hospital` is a marketing page, not a search tool. Its
"Search as a Guest" button (`a.cmp-button`) opens BCBSIL's real Provider Finder at
`https://my.providerfinderonline.com/?ci=IL-UUX&corp_code=IL` — and that host is behind an **Imperva
interstitial that renders an hCaptcha "I am human" challenge** ("my.providerfinderonline.com -
Additional security check is required"). Verified deterministic: 3 direct navigations plus the genuine
click-through from bcbsil.com (which carries the natural referrer and cookies) all met it, and it did
not self-clear after 30s. Per this package's settled policy the challenge is *detected, screenshotted
and reported* — never solved, and no stealth/fingerprint patching is used. The 2026-07-28 probe
recorded "no bot protection" for this portal because it only probed the bcbsil.com **marketing** page;
the docs note calling HCSC Imperva-protected is therefore CORRECT, just about a different host.

So this driver reads the answer from the other public, member-facing BCBS find-a-doctor portal that
covers BCBSIL's network: the BCBSA **National Doctor and Hospital Finder** at `provider.bcbs.com`
(HealthSparq/Sapphire — the page ships `HSQ_SVG_SPRITE_SHEET` and `@hsq-search-results-page_*` CSS
modules). No Imperva, no challenge, and it is *hard plan-gated*: provider search is unreachable until
a Blue plan is confirmed, so an unpinned directory read is not even possible here.

OBSERVED FLOW (each step gated on the previous; all selectors verified live)
---------------------------------------------------------------------------
    provider.bcbs.com                 `#button-welcome`  "Choose a location and plan"
    "Choose your search location"     `#input-location` (placeholder "Enter an address, city or zip
                                      code") -> suggestions render as `#suggestion-location-<N>`
                                      -> commit with the button "Yes, this is correct"
    "Find your plan by prefix"        three single-letter inputs `#alpha-one-input`/`-two-`/`-three-`
                                      (the member's ID alpha prefix — we never have it and would not
                                      send member data anyway) OR the button "Browse a list of plans"
    "Browse all plans"                one button per Blue licensee whose **DOM id is the plan code**
                                      (`#ILIL1M` = "Illinois, Blue Cross and Blue Shield",
                                      `#TXTX3M`, `#KYIN3M`, …); `#plan-name-input` filters the list
                                      -> then the button "Confirm selection"   <- pins the network
    dashboard                         header now shows `LOCATION <city, ST zip>` + `PLAN <plan>`
    "Doctors by name"                 `#SEARCH_AUTOSUGGEST_INPUT`, Enter -> results page whose URL
                                      carries `productCode=<plan code>&query=<term>&radius=<miles>`

The `productCode=` in the results URL is the machine-checkable proof that the answer came from the
pinned network — that, not a chip's text, is what gates OUT_OF_NETWORK here.

TRAPS THIS DRIVER EXISTS TO AVOID (each one cost a live run)
-----------------------------------------------------------
1. FUZZY SUBSTITUTION LOOKS EXACTLY LIKE A POPULATED RESULT SET. Searching "Raikar" printed
   "No results for Raikar / We've made a best guess of \"Randar\"." and then rendered **1 card, "1
   search results", "Displaying 1-1 of 1"** for *Daniel Randar, LPT* — a different person. Counting
   that as "results came back and ours is not among them" would manufacture a false OUT_OF_NETWORK.
   Every failing term did this ("Bao"->"Bae", "Raik"->"Raikovskii", "Rai"->"Rafi"). A result set is
   only real when the "No results for …" / "best guess of …" notice is ABSENT.
2. THE PAGE ECHOES THE QUERY, SO PAGE-TEXT NAME MATCHING GIVES A FALSE IN_NETWORK. Searching
   "Bao-Lan Raikar" put the surname on the page three times (H1, breadcrumb, "No results for
   Bao-Lan Raikar") while returning zero real matches. Names are therefore read ONLY from
   `[class*='provider-card'] h2`, never from `inner_text("body")`.
3. A NAMESAKE IS NOT OUR PROVIDER. The wide-radius search for "Raikar" returns *Sanjay V. Raikar,
   MBBS* — in-network, 71 mi away, and not the person we asked about. Surname-only agreement is
   treated as a namesake (it strengthens the OON case), never as a match. Where the card carries only
   an initial that is compatible with our first name, the read is AMBIGUOUS and downgrades to UNKNOWN.
4. `[class*='search-result']` MATCHES THE PAGE SHELL (11 nodes for 1 result, header included) — the
   local analogue of UHC's `[role=option]` count poisoning. Cards are `[class*='provider-card']`.
5. THE DEFAULT 25-MILE RADIUS CAN HIDE THE WHOLE ANSWER. At 25 mi "Raikar" was a fuzzy empty; at
   100 mi the *same* query returned a genuine, complete set (the namesake) — turning an inconclusive
   read into a decisive one. The driver widens once rather than concluding from the tight search.
6. THERE IS NO NPI SEARCH. `Advanced search` is a filter drawer (languages, specialty, Blue
   Distinction, search area …) with no identifier field, and an NPI typed as a query returns the
   distinct hard-empty state "No results available for \"<npi>\" within your search area". The
   provider's NAME is the only handle, so the NPI is never sent as a search term.
7. ABSENCE ON PAGE 1 OF N PROVES NOTHING. Results paginate 10 at a time ("Displaying 1-10 of 36").
   An OON is only claimed when the whole set is on screen; we never page through a network.

PLAN GRANULARITY — a real limitation, recorded because the verdicts depend on it. The national finder
pins the **licensee** plan ("Illinois, Blue Cross and Blue Shield"), not a product network:
`#plan-name-input` filtered to "Blue Choice" returns 0 rows, so PPO / Blue Choice PPO / BlueCare
Direct cannot be distinguished here. The asymmetry that follows is deliberate:
  * OUT_OF_NETWORK is SAFE — absent from the plan-level directory implies absent from every narrower
    product network inside it.
  * IN_NETWORK is WEAKER — present at plan level does not prove presence in a narrow-network product,
    so that note says so explicitly and points at BCBSIL's own (challenge-gated) finder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

ENTRY = "https://provider.bcbs.com/"

# BCBSIL's own Provider Finder, reached from the bcbsil.com "Search as a Guest" button. Recorded for
# the operator: it is Imperva + hCaptcha gated (see the module docstring). A headed run lets a human
# satisfy it themselves, after which browser.save_session()/storage_state reuse makes it driveable.
BCBSIL_GUEST_FINDER = "https://my.providerfinderonline.com/?ci=IL-UUX&corp_code=IL"

_WELCOME = "#button-welcome"  # "Choose a location and plan"
_LOCATION = "#input-location"
_LOC_SUGGESTION = "[id^='suggestion-location-']:visible"
_SEARCH = "#SEARCH_AUTOSUGGEST_INPUT"
_CARD = "[class*='provider-card']"

# A plan row's DOM id IS its plan code: 6 chars, state-of-member + state-of-licensee + index ("ILIL1M",
# "KYIN3M" = Kentucky served by Anthem Indiana). Anchored so no generated Ember id can slip in.
_PLAN_CODE = re.compile(r"^[A-Z]{2}[A-Z0-9]{4}$")

# The clinic-local search a staffer runs, then one widening. 150 is the portal's own stated maximum
# ("Search up to 150 miles"); 100 is used because it was verified live to return a complete set.
_RADIUS_LOCAL = 25
_RADIUS_WIDE = 100

# "No real results" comes in two shapes, and both must count as ZERO:
#   fuzzy   — "No results for X" + "We've made a best guess of \"Y\"." + a substituted card
#   empty   — "No results available for \"X\" within your search area", no cards at all
_FUZZY_NOTICE = re.compile(r"No results for\s+(.+?)\s*\n|best guess of\s*\"([^\"]*)\"", re.I)
_HARD_EMPTY = re.compile(r"No results? available\s*for", re.I)
_DISPLAYING = re.compile(r"Displaying\s+([\d,]+)\s*-\s*([\d,]+)\s+of\s+([\d,]+)", re.I)
_RESULT_COUNT = re.compile(r"([\d,]+)\s+search results", re.I)


def _tokens(s: str | None) -> set[str]:
    """Alphabetic name tokens, lowercased. Token-wise (not substring) so "lan" cannot match "Milan"."""
    return {t for t in re.split(r"[^A-Za-z]+", (s or "").lower()) if t}


def _int(s: str) -> int:
    return int(s.replace(",", ""))


@dataclass
class _Read:
    """One results page, as read. `kind` is the difference between an answer and an artefact."""

    kind: str  # "genuine" | "fuzzy" | "empty"
    total: int  # results the portal says exist for this query
    shown: int  # cards actually on screen (paging is never followed)
    cards: list[tuple[str, str]]  # (provider name, full card text) — read from the cards ONLY
    radius: int | None  # radius the URL says produced this page
    plan_code: str | None  # productCode the URL says produced this page

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.cards]

    @property
    def real(self) -> bool:
        return self.kind == "genuine" and self.total > 0 and bool(self.cards)

    @property
    def complete(self) -> bool:
        """Is the whole result set on screen? Absence from page 1 of N is not absence."""
        return self.shown >= self.total


class BcbsilProviderFinderDriver(PortalDriver):
    key = "bcbsil-provider-finder"
    portal_name = "BCBS Illinois — BCBSA National Doctor and Hospital Finder"

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail_box: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The walk trail always rides along: a verdict is only as trustworthy as the path to it,
            # and a silently-partial walk that then reported OON would be the worst failure mode.
            if trail_box:
                note = f"{note} [portal walk: {' → '.join(trail_box)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        if not q.provider_last_name:
            # Name-only portal (trap 6): with no surname there is nothing to search, and the NPI is
            # not indexed. Say so rather than sending a query that is guaranteed to come back empty.
            return result(
                PortalStatus.UNKNOWN,
                f"NPI {q.npi} could not be looked up: this portal has no identifier search (its "
                f"Advanced search is a filter drawer with no NPI field) and the query carried no "
                f"provider surname to search by.",
            )

        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            return result(PortalStatus.BLOCKED, f"navigation to {ENTRY} timed out.",
                          screenshot=shot("nav-timeout"))
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation to {ENTRY} failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))
        self._settle(page, 5_000)  # the Ember shell hydrates well after domcontentloaded

        plan_label, plan_code, trail = self._walk_to_plan(page, q)
        trail_box.extend(trail)
        shot("plan-walk")
        if not plan_code:
            # No plan, no search: this portal will not expose "Doctors by name" at all until a plan is
            # confirmed, so there is nothing to fall back to and certainly no basis for an OON.
            return result(
                PortalStatus.UNKNOWN,
                f"could not pin a Blue plan for {q.plan or q.state or 'this member'} in the National "
                f"Doctor and Hospital Finder, and the portal gates provider search behind plan "
                f"selection — so no network was searched and absence would mean nothing.",
                screenshot=shot("no-plan"),
            )

        if not self._open_name_search(page):
            return result(
                PortalStatus.BLOCKED,
                f"plan {plan_label!r} ({plan_code}) was pinned but the 'Doctors by name' search input "
                f"({_SEARCH}) never appeared — the portal shell did not hydrate or the flow changed.",
                screenshot=shot("no-search-input"),
            )
        trail_box.append("search: Doctors by name")

        soft: PortalCapture | None = None  # best non-decisive read, kept in case nothing decides
        for term, kind, radius, read in self._attempts(page, q, trail_box):
            where = (f"plan {plan_label!r} ({plan_code}) within {read.radius or radius} miles of "
                     f"{q.zip_code or 'the clinic'}")

            matched, why = self._match(read.cards, q)
            if matched:
                name, card = matched
                badge = self._badge(card)
                if badge == "out":
                    # The portal naming our provider and labelling the card out-of-network is a
                    # POSITIVE statement, not an absence — strictly better evidence than a missing row.
                    return result(
                        PortalStatus.OUT_OF_NETWORK,
                        f"NPI {q.npi} was found in the BCBSA National Doctor and Hospital Finder by "
                        f"{kind} ({term!r}) for {where} — listed as {name!r} — and the portal badges "
                        f"that listing OUT-OF-NETWORK for this plan.",
                        result_count=read.total, matched_name=name,
                        screenshot=shot(f"badged-oon-{kind}"),
                    )
                return result(
                    PortalStatus.IN_NETWORK,
                    f"NPI {q.npi} matched in the BCBSA National Doctor and Hospital Finder by {kind} "
                    f"({term!r}) for {where} — listed as {name!r}"
                    + (", badged In-network by the portal" if badge == "in"
                       else " (the card carried no explicit network badge)")
                    + ". NOTE: this finder pins the BCBSIL *licensee* plan, not a product network, so "
                    "a narrow-network product (Blue Choice PPO, BlueCare Direct) still needs BCBSIL's "
                    "own Provider Finder to confirm.",
                    result_count=read.total, matched_name=name, screenshot=shot(f"match-{kind}"),
                )
            if why == "ambiguous":
                soft = soft or result(
                    PortalStatus.UNKNOWN,
                    f"the {kind} search {term!r} in {where} returned {read.total} result(s) including a "
                    f"name that agrees on the surname and is initial-compatible with "
                    f"{q.provider_first_name or 'the provider'} ({read.names!r}) — too close to call "
                    f"either way, so this is not reported as a match or as an absence.",
                    result_count=read.total, screenshot=shot(f"ambiguous-{kind}"),
                )
                continue

            if not read.real:
                # fuzzy substitution or a hard empty: zero real results. Never an OON (trap 1).
                continue
            if not read.complete:
                soft = soft or result(
                    PortalStatus.UNKNOWN,
                    f"the {kind} search {term!r} in {where} returned {read.total} results but only "
                    f"{read.shown} are on screen — absence from page 1 of a paginated set is not "
                    f"absence, and this layer does not page through a payer's network.",
                    result_count=read.total, screenshot=shot(f"partial-{kind}"),
                )
                continue
            if read.plan_code != plan_code:
                soft = soft or result(
                    PortalStatus.UNKNOWN,
                    f"the {kind} search {term!r} returned {read.total} results but the results URL did "
                    f"not carry productCode={plan_code} (saw {read.plan_code!r}), so the network that "
                    f"was actually searched is unproven and absence from it is not evidence.",
                    result_count=read.total, screenshot=shot(f"unpinned-{kind}"),
                )
                continue

            others = ", ".join(read.names[:6]) or "none listed"
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"the BCBSA National Doctor and Hospital Finder returned a complete set of "
                f"{read.total} result(s) for {kind} {term!r} in {where} — {others} — and NPI {q.npi} "
                f"({self._full_name(q)}) is not among them. The plan is confirmed by "
                f"productCode={plan_code} in the results URL and the set is fully displayed "
                f"({read.shown} of {read.total}), so this is out-of-network for this plan. Absent from "
                f"the licensee-level directory also means absent from every narrower BCBSIL product "
                f"network inside it.",
                result_count=read.total, screenshot=shot(f"absent-{kind}"),
            )

        if soft:
            return soft
        return result(
            PortalStatus.UNKNOWN,
            f"every name search for {self._full_name(q)} (NPI {q.npi}) in plan {plan_label!r} "
            f"({plan_code}) came back with no real results — the portal answered each term either with "
            f"its own \"No results for …\" notice plus a spelling substitution, or with \"No results "
            f"available … within your search area\". Both are zero result sets, not a list our "
            f"provider is missing from. An empty result set cannot distinguish out-of-network from a "
            f"failed search, and this portal has no NPI search to cross-check with, so the honest "
            f"answer is unknown. BCBSIL's own Provider Finder "
            f"({BCBSIL_GUEST_FINDER}) would settle it but is Imperva/hCaptcha gated — a headed "
            f"operator run can satisfy that challenge once and the session is then reused.",
            result_count=0, screenshot=shot("no-real-results"),
        )

    # --- plan-first walk ------------------------------------------------------------------------

    def _walk_to_plan(self, page: Page, q: PortalQuery) -> tuple[str | None, str | None, list[str]]:
        """Location → commit → plan list → confirm. Returns (plan label, plan code, trail).

        The trail records every step reached so a capture that stops early says exactly where. No
        consent banner or coachmark was ever observed on this host across eight live runs on
        2026-07-28, so nothing is dismissed here — if one appears, this is where it belongs.
        """
        trail: list[str] = []
        if not self._click(page, _WELCOME, "Choose a location and plan"):
            return None, None, trail + ["'Choose a location and plan' NOT clickable"]
        trail.append("opened location+plan chooser")

        if q.zip_code and self._commit_location(page, q.zip_code):
            trail.append(f"location committed: {q.zip_code}")
        else:
            # Location is mandatory here ("we need a location to find in-network places nearby"); the
            # plan step never renders without it, so stop rather than pretend to have searched.
            return None, None, trail + [f"location {q.zip_code!r} NOT committed"]

        if not self._click(page, None, "Browse a list of plans"):
            return None, None, trail + ["'Browse a list of plans' NOT clickable"]
        trail.append("plan list opened")

        code, label, why = self._pick_plan(page, q)
        if not code:
            return None, None, trail + [f"no plan row matched {q.plan!r}/{q.state!r} ({why})"]
        trail.append(f"plan selected: {label} [{code}] via {why}")

        # "Confirm selection" is what actually pins the network — selecting the row only stages it.
        if not self._click(page, None, "Confirm selection"):
            return None, None, trail + [f"plan {label!r} staged but 'Confirm selection' NOT clickable"]
        trail.append("plan confirmed")
        return label, code, trail

    def _commit_location(self, page: Page, zip_code: str) -> bool:
        """Type the ZIP, take the geocoded suggestion, then commit it.

        Two things bite here. The first suggestion is "Use my current location", not a place — taking
        it by index would search the datacentre's geography. And filling the field is not enough: a
        "Yes, this is correct" button gates the plan step (the same committing-button pattern UHC has).
        """
        try:
            inp = page.locator(_LOCATION).first
            inp.wait_for(state="visible", timeout=15_000)
            inp.click()
            inp.fill(zip_code)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        page.wait_for_timeout(3_000)  # the geocoder debounces

        picked = False
        try:
            opts = page.locator(_LOC_SUGGESTION)
            for i in range(opts.count()):
                text = (opts.nth(i).inner_text() or "").strip()
                if "current location" in text.lower():
                    continue
                opts.nth(i).click()
                picked = True
                break
        except (PlaywrightTimeout, PlaywrightError):
            picked = False
        if not picked:
            return False
        page.wait_for_timeout(2_000)
        return self._click(page, None, "Yes, this is correct")

    def _pick_plan(self, page: Page, q: PortalQuery) -> tuple[str | None, str | None, str]:
        """Choose the Blue licensee row for this member's state, refined by the 271 plan string.

        Rows are keyed by plan code (`#ILIL1M`), and the code's first two characters are the member's
        state — which is a far stronger handle than the label text, because a dozen rows read
        "…, Anthem Blue Cross and Blue Shield" and token-matching alone would pick between them by
        coincidence. We never fall back to "just take the first row": with no state and no plan-token
        agreement the network stays unpinned and the capture stays UNKNOWN.
        """
        try:
            rows = page.evaluate(
                """() => Array.from(document.querySelectorAll('button'))
                     .filter(e => (e.offsetWidth || e.offsetHeight))
                     .map(e => ({id: e.id || '', txt: (e.innerText || '').trim()}))"""
            )
        except PlaywrightError:
            return None, None, "plan list unreadable"
        rows = [r for r in rows if _PLAN_CODE.match(r["id"])]
        if not rows:
            return None, None, "no plan-code rows found"

        state = (q.state or "")[:2].upper()  # roster states can be "FL-Tampa" / "CO-Denver"
        in_state = [r for r in rows if r["id"][:2] == state] if state else []
        want = {t for t in _tokens(q.plan) if len(t) >= 4}

        def score(row: dict) -> int:
            return len({t for t in _tokens(row["txt"]) if len(t) >= 4} & want)

        if in_state:
            best = max(in_state, key=score)
            why = f"state {state}" + (f" + plan tokens ({score(best)})" if score(best) else "")
            if len(in_state) > 1 and score(best) == 0:
                # e.g. Florida has two identically-labelled rows. Both are that state's Blue plan, so
                # this is not a coin flip between unrelated networks — but say so out loud.
                why += f"; {len(in_state)} rows for {state} and the plan string did not discriminate"
        else:
            scored = [r for r in rows if score(r)]
            if not scored:
                return None, None, f"no row for state {state!r} and no plan-token overlap"
            best = max(scored, key=score)
            why = f"plan tokens only ({score(best)}); no row for state {state!r}"

        if not self._click(page, f"#{best['id']}", best["txt"]):
            return None, None, f"row {best['id']} not clickable"
        return best["id"], best["txt"].splitlines()[0][:120], why

    def _open_name_search(self, page: Page) -> bool:
        """Open the "Doctors by name" category and wait for its input."""
        if not self._click(page, None, "Doctors by name"):
            return False
        try:
            page.locator(_SEARCH).first.wait_for(state="visible", timeout=15_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        return True

    # --- searching ------------------------------------------------------------------------------

    def _attempts(self, page: Page, q: PortalQuery, trail: list[str]):
        """Yield (term, kind, radius, read) for the search ladder, stopping when the caller returns.

        Surname first: this is a name index, and the bare surname is what actually finds people (the
        full name "Bao-Lan Raikar" was answered with a spelling substitution while "Raikar" reached the
        real Raikar rows). Then the same term widened once — the 25-mile default hid the entire answer
        on the verified run (trap 5). The NPI is deliberately absent: it is not indexed here (trap 6).
        """
        surname = (q.provider_last_name or "").strip()
        full = self._full_name(q)
        ladder: list[tuple[str, str, int]] = [(surname, "surname", _RADIUS_LOCAL),
                                              (surname, "surname", _RADIUS_WIDE)]
        if full and full.lower() != surname.lower():
            ladder.append((full, "full name", _RADIUS_WIDE))

        route: str | None = None
        for term, kind, radius in ladder:
            if route is None:
                # First pass drives the real UI: type into the search box and submit. That also builds
                # the canonical results route whose parameters later passes vary.
                if not self._submit(page, term):
                    trail.append(f"search {term!r} @{radius}mi NOT submitted")
                    continue
                route = page.url
            else:
                # Later passes re-route through the app's own hash parameters (query/radius). Verified
                # live; it is the same router the filter UI drives, and it is deterministic.
                if not self._reroute(page, route, term, radius):
                    trail.append(f"search {term!r} @{radius}mi NOT reachable via the results route")
                    continue
            read = self._read(page)
            trail.append(f"{kind} {term!r} @{read.radius or radius}mi → {read.kind} "
                         f"{read.shown}/{read.total}")
            yield term, kind, radius, read

    def _submit(self, page: Page, term: str) -> bool:
        try:
            box = page.locator(_SEARCH).first
            box.wait_for(state="visible", timeout=10_000)
            box.click()
            box.fill(term)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        page.wait_for_timeout(3_000)  # the autosuggest debounces before Enter routes to the results
        try:
            box.press("Enter")
        except (PlaywrightTimeout, PlaywrightError):
            return False
        # Settle separately from the submit: a slow settle must never be read as a failed search.
        self._settle(page, 5_000)
        return "/search/" in page.url

    def _reroute(self, page: Page, route: str, term: str, radius: int) -> bool:
        """Re-run the pinned search with a different term/radius via the SPA's own hash parameters.

        The router DOUBLE-encodes its values — a UI-driven search for "Bao Lan Raikar" produces
        `query=Bao%2520Lan%2520Raikar`, and `location=Gurnee%252C%2520IL%252060031`. Writing a
        single-encoded `%20` here would hand the app a differently-parsed term, so separators are
        emitted as `%2520` to match what the UI itself puts on the route.
        """
        encoded = re.sub(r"[^A-Za-z0-9]+", "%2520", term.strip())
        url = re.sub(r"query=[^&]*", f"query={encoded}", route)
        url = re.sub(r"radius=\d+", f"radius={radius}", url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 6_000)
        if f"radius={radius}" not in page.url or "/search/" not in page.url:
            return False
        # And confirm the page really is answering OUR term. Without this a router change would let a
        # verdict be read off the previous query's results — the worst kind of silent wrong answer. The
        # results header echoes the query verbatim, so that is the honest thing to check against; every
        # heading is scanned rather than just the first, because a decorative or screen-reader heading
        # ahead of it would otherwise read as empty and silently discard a good attempt.
        want = _tokens(term)
        try:
            heads = page.evaluate(
                """() => Array.from(document.querySelectorAll('h1, h2'))
                     .slice(0, 12).map(e => e.innerText || '')"""
            )
        except PlaywrightError:
            return False
        return any(want <= _tokens(h) for h in heads)

    def _read(self, page: Page) -> _Read:
        """Read one results page: which of the three states it is in, how many, and whose names."""
        try:
            text = page.inner_text("body") or ""
        except PlaywrightError:
            text = ""

        # One evaluate, not one round-trip per node. Iterating `locator.nth(i)` here was a real
        # mistake: the context default timeout is 45s, cards NEST (an outer wrapper and an inner
        # element both match `provider-card`), and calling `.locator("h2").inner_text()` on a nested
        # node that has no h2 blocks for the full 45s — a 10-card page took minutes instead of seconds.
        cards: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            raw = page.evaluate(
                """(sel) => Array.from(document.querySelectorAll(sel))
                     .filter(e => (e.offsetWidth || e.offsetHeight))
                     .slice(0, 60)
                     .map(e => { const h = e.querySelector('h2');
                                 return {name: h ? (h.innerText || '').trim() : '',
                                         text: (e.innerText || '')}; })""",
                _CARD,
            )
        except PlaywrightError:
            raw = []
        for row in raw:
            name = (row.get("name") or "").strip()
            name = name.splitlines()[0][:120] if name else ""
            if name and name not in seen:  # dedupe the nesting, or one result counts twice
                seen.add(name)
                cards.append((name, row.get("text") or ""))

        m = _DISPLAYING.search(text)
        if m:
            shown, total = _int(m.group(2)), _int(m.group(3))
        else:
            c = _RESULT_COUNT.search(text)
            total = _int(c.group(1)) if c else len(cards)
            shown = len(cards)

        if _FUZZY_NOTICE.search(text):
            kind = "fuzzy"  # the cards belong to a substituted spelling, not to our query
        elif _HARD_EMPTY.search(text) or not cards:
            kind = "empty"
        else:
            kind = "genuine"

        r = re.search(r"radius=(\d+)", page.url)
        p = re.search(r"productCode=([A-Za-z0-9]+)", page.url)
        return _Read(kind=kind, total=total, shown=max(shown, len(cards)), cards=cards,
                     radius=int(r.group(1)) if r else None, plan_code=p.group(1) if p else None)

    def _badge(self, card_text: str) -> str | None:
        """The portal's own network label on a card: "in", "out", or None when it carries neither.

        Order matters — "out-of-network" contains "network", and every observed card carried the chip
        "In-network", so the negative must be tested first.
        """
        low = (card_text or "").lower()
        # Every negative phrasing must be enumerated here, not just the "out of network" ones: the
        # positive test is a substring check, and "not in network" CONTAINS "in network". Verified —
        # before this line existed, _badge("Not In Network") returned "in", and the caller treats any
        # badge that is not "out" as in-network, so a card the payer had explicitly labelled Not In
        # Network became a false IN_NETWORK verdict.
        if any(neg in low for neg in
               ("out-of-network", "out of network", "not in network", "not in-network",
                "non-network", "nonparticipating", "non-participating")):
            return "out"
        if "in-network" in low or "in network" in low:
            return "in"
        return None

    def _match(self, cards: list[tuple[str, str]], q: PortalQuery) -> tuple[tuple[str, str] | None, str]:
        """Is one of these cards our provider? Returns ((name, card text) or None, reason).

        Token-wise, and the surname alone is never enough: the verified run's wide search returned
        *Sanjay V. Raikar* for *Bao-Lan Raikar*. A surname hit therefore needs a first-name token to
        become a match; a surname hit whose card carries only an initial compatible with our first
        name is reported as "ambiguous" so it can decide nothing in either direction.
        """
        want_last = _tokens(q.provider_last_name)
        want_first = _tokens(q.provider_first_name)
        if not want_last:
            return None, "no surname to match"
        ambiguous = False
        for name, body in cards:
            got = _tokens(name)
            if not (want_last & got):
                continue
            if want_first & got:
                return (name, body), "surname + first name"
            if not want_first:
                # Surname agrees and we have no first name to separate namesakes: cannot decide.
                ambiguous = True
                continue
            if {t for t in got if len(t) == 1} & {f[0] for f in want_first}:
                ambiguous = True
        return None, "ambiguous" if ambiguous else "no card matched"

    # --- helpers --------------------------------------------------------------------------------

    def _full_name(self, q: PortalQuery) -> str:
        return " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()

    def _click(self, page: Page, selector: str | None, label: str, timeout_ms: int = 12_000) -> bool:
        """Click by id/selector, then by accessible button name, then — last — by loose text.

        Loose text is last because it is dangerous: on portals like this one a heading can contain the
        same words as the button ("Choose your search location" vs the Select control), and clicking
        the heading leaves the walk reporting success while the next step never populates.
        """
        for kind in ("selector", "role", "text"):
            try:
                if kind == "selector":
                    if not selector:
                        continue
                    loc = page.locator(selector).first
                elif kind == "role":
                    loc = page.get_by_role("button", name=label, exact=True).first
                else:
                    loc = page.get_by_text(label, exact=True).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            # The click landed. Settling is best-effort and MUST NOT invalidate it — treating a
            # networkidle timeout as a failed click is how a walk ends up denying a step it completed.
            self._settle(page)
            return True
        return False

    def _settle(self, page: Page, pause_ms: int = 3_000) -> None:
        """Best-effort wait for the next step. Never raises: this page streams analytics beacons
        (Adobe demdex, Decibel) and may never reach networkidle."""
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass
