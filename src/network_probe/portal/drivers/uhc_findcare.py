"""UHC Find Care (guest) — findcare.guest.uhc.com.

Covers three of the eleven Ins Test 3 rows (UHC Medicare Advantage GA, UHC Commercial NHP Access HMO
FL, UHC AARP Medicare Advantage FL) and is the reason this whole layer exists: on Test 2, flex.optum's
FHIR said David Naar (NPI 1760457477) was IN-network for AARP Medicare Advantage FL-0026, while this
portal showed him absent. The portal was right.

Observed structure (2026-07-28): the entry URL hydrates into a Find Care shell with two Abyss-design
inputs — `primary-search-input` (keyword: name, NPI, procedure) and `location-search-input`. Plan
selection is a separate guest step; when the shell exposes it we drive it from the 271's plan string,
and when we cannot confirm which network was searched the verdict stays UNKNOWN rather than OON.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal import browser as pb
from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

"""Observed guest flow (2026-07-28), each step gated on the previous:

    /guest-plan-selection            "Type of coverage"  -> Employer and Individual | Medicare |
                                                            Medicaid | ACA Marketplace | SHOP
    /select-coverage-type?…=MR       "Type of care"      -> Medical | Behavioral Health | Dental | Vision
    …                                county / ZIP        -> pick the member's county
    …                                plan list           -> pick the 271's plan  <- pins the network
    /browse                          "Find care"         -> primary-search-input + location-search-input

Going straight to /browse (as the first cut did) yields an *unpinned* generic directory — the page
even offers "Sign in for the most accurate plan". That path can never justify an OON, which is why the
walk below is mandatory rather than an optimisation.
"""

ENTRY = "https://findcare.guest.uhc.com/guest-plan-selection"
BROWSE = "https://findcare.guest.uhc.com/guest-plan-selection/browse"

_SEARCH = "[data-testid='primary-search-input']"
_LOCATION = "[data-testid='location-search-input']"

# Waiting is condition-based, not clock-based: see `_settle` and `_await_suggestions` for the measured
# reasons. Both caps exist only so a wedged portal cannot hang a walk forever — in a healthy run
# neither is reached.
_TYPEAHEAD_DEBOUNCE_MS = 1_200  # let the location autocomplete close before reading provider hits
_TYPEAHEAD_MAX_S = 12.0  # a genuinely empty typeahead must be waited out, not assumed
_TYPEAHEAD_POLL_MS = 300
_SEARCH_INPUT_MAX_MS = 30_000  # plan selection re-renders the shell; waiting costs nothing if it is quick

# Dismissable overlays that sit on top of the first step.
_OVERLAYS = (
    "[data-testid='guest-start-modal-abyss-modal-base-close-button']",
    "[data-testid='guest-coachmark-container-abyss-coachmark-close-button']",
)

# Coverage type -> the card to click, keyed by the line of business we infer from the plan string.
# testids follow UHC's own abbreviations (ei/mr/cs/ifp/shop), with the visible label as fallback.
_COVERAGE = {
    "medicare": ("[data-testid='explore-mr-link-abyss-link-root']", "Medicare"),
    "medicaid": ("[data-testid='explore-cs-link-abyss-link-root']", "Medicaid"),
    "aca": ("[data-testid='explore-ifp-link-abyss-link-root']", "ACA Marketplace"),
    "commercial": ("[data-testid='explore-ei-link-abyss-link-root']", "Employer and Individual"),
}

_RESULT_CARDS = (
    "[data-testid*='provider-card']",
    "[data-testid*='result-card']",
    "[data-testid*='search-result']",
    "[class*='provider-card']",
)
_PLAN_SURFACES = (
    "[data-testid*='plan-card']",
    "[data-testid*='plan-name']",
    "[data-testid*='plan-list'] li",
    "[role=radio]",
    "[role=option]",
)


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


class UhcFindCareDriver(PortalDriver):
    key = "uhc-findcare"
    portal_name = "UHC Find Care (guest)"
    # This portal commits a location from the ZIP alone (`_commit_location`); a state or city on the
    # query is never typed anywhere. Without the narrowing, a state-only query passes the guard in
    # run_capture and then spends ~120s on a portal whose plan list never populates.
    location_fields = ("zip_code",)

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail_box: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # Always carry the walk trail: a verdict is only as trustworthy as the path that produced it.
            if trail_box:
                note = f"{note} [portal walk: {' → '.join(trail_box)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            pass
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))
        page.wait_for_timeout(3_000)  # Abyss shell hydrates after networkidle

        plan_confirmed, trail = self._walk_to_plan(page, q)
        trail_box.extend(trail)
        shot("plan-walk")
        if not plan_confirmed:
            # Fall through to the unpinned directory: still worth a screenshot and an IN if the
            # provider shows up (presence in UHC's directory at all is a real signal), but never an OON.
            try:
                page.goto(BROWSE, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(3_000)
            except (PlaywrightTimeout, PlaywrightError):
                pass

        # Generous on purpose. This is a condition-based wait, so a longer ceiling costs nothing when
        # the input appears promptly — but plan selection re-renders the whole shell, and the walk
        # arrives here with less slack now that `_settle` no longer spends a dead 10s per step. A run
        # on 2026-07-31 hit the old 15s and reported BLOCKED after having pinned the plan correctly.
        try:
            search = page.locator(_SEARCH).first
            search.wait_for(state="visible", timeout=_SEARCH_INPUT_MAX_MS)
        except (PlaywrightTimeout, PlaywrightError):
            return result(PortalStatus.BLOCKED, "no provider-search input appeared — portal shell did "
                          "not hydrate, or the guest flow changed.", screenshot=shot("no-search-input"))

        if q.zip_code:
            self._set_location(page, q.zip_code)

        # NPI first (exact, unambiguous); fall back to the provider name, which is what a staffer types.
        for term, kind in self._search_terms(q):
            found, count, matched = self._run_search(page, search, term, q)
            if found:
                status, note = self.presence_verdict(
                    plan_confirmed=plan_confirmed, kind=kind, term=term, matched=matched,
                    npi=q.npi, zip_code=q.zip_code, plan=q.plan,
                )
                return result(status, note, result_count=count, matched_name=matched,
                              screenshot=shot(f"match-{kind}"))
            if count and plan_confirmed:
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"UHC Find Care returned {count} result(s) for {kind} {term!r} in plan "
                    f"{q.plan!r} near {q.zip_code or 'the clinic'}, and NPI {q.npi} is not among "
                    f"them — out-of-network for this plan.",
                    result_count=count, screenshot=shot(f"absent-{kind}"),
                )
            if count:
                return result(
                    PortalStatus.UNKNOWN,
                    f"UHC Find Care returned {count} result(s) for {kind} {term!r} without NPI "
                    f"{q.npi}, but the guest plan could not be confirmed — absence from an "
                    f"unconfirmed network is not evidence of out-of-network.",
                    result_count=count, screenshot=shot(f"absent-noplan-{kind}"),
                )

        return result(
            PortalStatus.UNKNOWN,
            f"UHC Find Care returned no results for NPI {q.npi} or "
            f"{q.provider_last_name or 'the provider name'} near {q.zip_code or 'the clinic'}"
            + (f" in plan {q.plan!r}" if plan_confirmed else " (guest plan unconfirmed)")
            + ". An empty result set does not distinguish out-of-network from a failed search.",
            result_count=0, screenshot=shot("no-results"),
        )

    # --- steps -------------------------------------------------------------------------------------

    def _search_terms(self, q: PortalQuery):
        """NPI first, then the provider's name — the two ways this portal can find one provider."""
        # Broadest-last is wrong for a typeahead: it matches prefixes, so the bare SURNAME finds more
        # than "First Last" does. Verified live — "Orem" returned 2 suggestions, "Randall Orem" returned
        # none. Order: NPI (exact, if indexed) -> surname (the staffer's own search) -> full name.
        terms: list[tuple[str, str]] = []
        if q.npi:
            terms.append((q.npi, "NPI"))
        if q.provider_last_name:
            terms.append((q.provider_last_name, "surname"))
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        if full and full != (q.provider_last_name or ""):
            terms.append((full, "full name"))
        return terms

    def _walk_to_plan(self, page: Page, q: PortalQuery) -> tuple[bool, list[str]]:
        """Walk coverage type → type of care → county → plan list, pinning the member's network.

        Returns (plan_confirmed, trail). `trail` records each step reached so a capture that stops
        early says exactly where — the portals redesign often, and a silent partial walk that then
        reports OON would be the worst possible failure mode.
        """
        trail: list[str] = []
        self._dismiss_overlays(page)
        trail.append("overlays dismissed")

        sel, label = _COVERAGE[self._coverage_type(q)]
        if not self._click_any(page, sel, label):
            return False, trail + [f"coverage type {label!r} NOT clickable"]
        trail.append(f"coverage: {label}")

        # "Type of care" — always Medical for a physician network check.
        if not self._click_any(page, "[data-testid*='medical']", "Medical"):
            return False, trail + ["type of care 'Medical' NOT clickable"]
        trail.append("care: Medical")

        if q.zip_code and self._commit_location(page, q.zip_code):
            trail.append(f"county via ZIP {q.zip_code}")
        # "Plans will populate upon location selection" — the plan list does not exist until the
        # location is *committed* with the Select button. Filling the ZIP is not enough.
        if self._click_any(page, "[data-testid*='location-select']", "Select", timeout_ms=6_000):
            trail.append("location committed (Select)")
        self._dismiss_overlays(page)  # a fresh coachmark appears on the plan-selection step

        picked = self._pick_plan(page, q.plan)
        if picked:
            trail.append(f"plan pinned: {picked}")
            return True, trail
        return False, trail + [f"no plan matched {q.plan!r} in the plan list"]

    def _coverage_type(self, q: PortalQuery) -> str:
        """Map the 271's plan string to UHC's coverage-type card. Reuses the domain's own LOB rules so
        this driver and the network resolver can never disagree about what line a plan is."""
        from network_probe.domain.line_of_business import line_of_business

        lob = line_of_business(q.plan or "", None)
        if lob in ("medicare", "dual"):
            return "medicare"
        if lob == "medicaid":
            return "medicaid"
        text = (q.plan or "").lower()
        if any(k in text for k in ("marketplace", "exchange", "aca", "individual & family")):
            return "aca"
        return "commercial"

    def _dismiss_overlays(self, page: Page) -> None:
        for sel in _OVERLAYS:
            try:
                b = page.locator(sel).first
                if b.is_visible():
                    b.click()
                    page.wait_for_timeout(1_000)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _click_any(self, page: Page, testid_sel: str, label: str, timeout_ms: int = 8_000) -> bool:
        """Click by testid, falling back to the visible label. UHC renames testids between releases,
        so the human-readable label is the more durable handle."""
        # Order matters: testid, then the *button* with this accessible name, then loose text. Loose text
        # is last because it is genuinely dangerous — get_by_text("Select") matched the heading "Select
        # the area where you live…" and clicked that instead of the Select button, so the plan list never
        # populated while the walk reported success.
        for kind in ("testid", "role", "text"):
            try:
                if kind == "testid":
                    loc = page.locator(testid_sel).first
                elif kind == "role":
                    loc = page.get_by_role("button", name=label, exact=True).first
                else:
                    loc = page.get_by_text(label, exact=False).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            # The click succeeded. Settling is best-effort and MUST NOT invalidate it — treating a
            # networkidle timeout as a failed click is what made the first walk report
            # "coverage type 'Medicare' NOT clickable" after it had already navigated.
            self._settle(page)
            return True
        return False

    def _settle(self, page: Page, pause_ms: int = 2_500) -> None:
        """Best-effort wait for the next step to render. Never raises. See `browser.settle` for why
        this is a DOM-quiescence poll and not networkidle (measured: 7/7 timeouts at 10s here)."""
        pb.settle(page, pause_ms)

    def _commit_location(self, page: Page, zip_code: str) -> bool:
        """Enter the ZIP wherever the current step asks for it and commit the county suggestion."""
        try:
            inp = page.locator("input:visible").first
            inp.wait_for(state="visible", timeout=8_000)
            inp.click()
            inp.fill(zip_code)
            page.wait_for_timeout(2_500)
            opts = page.locator("[role=option]:visible")
            if opts.count():
                opts.first.click()
            else:
                inp.press("Enter")
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page)
        return True

    def _pick_plan(self, page: Page, plan: str | None) -> str | None:
        """Choose the plan whose label shares the most distinctive tokens with the 271 plan string.
        Returns the chosen label, or None — we never pick arbitrarily just to proceed."""
        if not plan:
            return None
        for sel in _PLAN_SURFACES:
            try:
                loc = page.locator(f"{sel}:visible")
                n = loc.count()
                if not n:
                    continue
                idx = self._best_option(loc, plan)
                if idx is None:
                    continue
                label = (loc.nth(idx).inner_text() or "").strip().splitlines()[0][:120]
                loc.nth(idx).click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            self._settle(page, 3_000)  # post-click, so a slow settle cannot discard a pinned plan
            return label
        return None

    def _best_option(self, opts, plan: str) -> int | None:
        """Pick the autocomplete option sharing the most distinctive tokens with the 271 plan string.
        Never guess: with no token overlap we return None and the network stays unconfirmed."""
        want = {t for t in re.split(r"[^A-Za-z0-9]+", plan.upper()) if len(t) >= 4}
        best, best_score = None, 0
        for i in range(min(opts.count(), 12)):
            try:
                text = (opts.nth(i).inner_text() or "").upper()
            except PlaywrightError:
                continue
            score = len({t for t in re.split(r"[^A-Za-z0-9]+", text) if len(t) >= 4} & want)
            if score > best_score:
                best, best_score = i, score
        return best

    def _set_location(self, page: Page, zip_code: str) -> None:
        try:
            loc = page.locator(_LOCATION).first
            loc.wait_for(state="visible", timeout=6_000)
            loc.click()
            loc.fill(zip_code)
            page.wait_for_timeout(2_000)
            opts = page.locator("[role=option]")
            if opts.count():
                opts.first.click()
            else:
                loc.press("Enter")
            page.wait_for_timeout(2_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass  # a failed location narrows nothing; the search still runs



    def identify(self, pool, q: PortalQuery):
        """(ours, namesake_seen) — which listing is OUR provider, and whether a same-surname
        stranger was present.

        The old test was `surname in normalized(text)`, a substring match that ignored the first
        name, so any Bui matched any other Bui. Live, with the plan correctly pinned, that read
        "Tony BUI, Pain Management" as Stephanie Bui and "Benjamin L NAAR" as David Naar — two
        confident IN_NETWORKs against staff determinations of OON. Same defect class as the Oscar
        adapter's P1 namesake fix.

        `namesake_seen` matters as much as the match: a listing that shares the surname but not the
        first name means the result set is not evidence our provider is ABSENT — the portal may
        carry them under a form we did not match — so the caller must not read it as OON.
        """
        last = _norm(q.provider_last_name)
        first = _norm(q.provider_first_name)
        if not last:
            return None, False
        ambiguous = False
        for text in pool:
            n = _norm(text)
            if last not in n:
                continue
            if first and first in n:
                return text.strip().splitlines()[0][:120], False
            # Same surname, not ours. Whether that BLOCKS an absence finding depends on how much
            # the listing printed. UHC renders "FirstName [M] LASTNAME" + specialty, so whatever
            # sits before the surname is the given-name region:
            #   * non-empty -> a fully named stranger ("Benjamin L NAAR" is plainly not David
            #     Naar). It does not hide our provider, so the set still establishes absence.
            #   * empty -> only a surname or an initial was printed; it might be ours, so the
            #     caller must not read the set as an absence.
            #   * we supplied no first name at all -> we cannot tell either way.
            if not first or not n.split(last, 1)[0].strip():
                ambiguous = True
        return None, ambiguous

    def presence_verdict(self, *, plan_confirmed: bool, kind: str, term: str, matched, npi,
                         zip_code, plan):
        """Presence in the directory -> (status, note). Decisive ONLY when the plan is pinned.

        The un-pinned directory is a union of every network UHC sells, so being listed in it proves
        the provider is contracted with UHC — not that they are in THIS member's network. Caught
        live: Test 2 row 2 (Dual Complete) returned a confident IN_NETWORK from the un-pinned
        directory, reached via the "Employer and Individual" commercial path because no plan was
        given, against a staff determination of OON. AZ Blue's driver already refuses this shape.
        """
        where = f"near {zip_code or 'the clinic'}"
        if plan_confirmed:
            return PortalStatus.IN_NETWORK, (
                f"NPI {npi} matched in UHC Find Care by {kind} ({term!r}) for plan "
                f"{plan or 'the selected guest plan'} {where} — listed as {matched!r}."
            )
        return PortalStatus.UNKNOWN, (
            f"NPI {npi} IS listed in UHC Find Care by {kind} ({term!r}) {where} — as {matched!r} — "
            f"but no plan was pinned, so this is the un-pinned directory: a union of every network "
            f"UHC sells. That proves the provider is contracted with UHC, not that they are in this "
            f"member's network. Supply the member's plan to settle it."
        )

    def _run_search(self, page: Page, search, term: str, q: PortalQuery) -> tuple[bool, int, str | None]:
        """Search one term against the pinned plan. Returns (matched_us, result_count, matched_name).

        The typeahead suggestion list IS the result set for a provider-name query — this portal answers
        "who in this plan matches what you typed" inline rather than on a results page. That is exactly
        the surface the Test 2 manual checks read: searching "Desir Hedson" returned other people, and
        that absence-among-present-results is what made the verdict OON. So we read the suggestions.

        The plan-selection step leaves the search page's own location field EMPTY (verified live), so it
        is refilled here — an unscoped search is not the search a staffer performs.
        """
        if q.zip_code:
            self._set_location(page, q.zip_code)
        try:
            search.click()
            search.fill(term)
        except (PlaywrightTimeout, PlaywrightError):
            return False, 0, None
        cands = self._await_suggestions(page)
        # Try to open a full results page too; when it works the cards are richer than the suggestions.
        cards = []
        try:
            search.press("Enter")
            self._settle(page, 3_000)
            cards, _ = self._cards(page)
        except (PlaywrightTimeout, PlaywrightError):
            pass

        pool = [t for t in (cands + cards) if t.strip()]
        count = len(cands) if cands else len(cards)
        page_text = self._page_text(page)

        if q.npi and q.npi in page_text:
            return True, count, self._matched_name(pool, q) or f"NPI {q.npi} present on page"
        ours, namesake = self.identify(pool, q)
        if ours:
            return True, count, ours
        # A same-surname stranger means this result set cannot show our provider is ABSENT, so
        # report zero results rather than a countable set the caller could read as an OON.
        return False, (0 if namesake else count), None

    def _await_suggestions(self, page: Page) -> list[str]:
        """Wait for the typeahead to answer, rather than sleeping a fixed 4s and reading whatever
        happens to be on screen.

        The blind sleep was a race, and it lost: two live runs of the SAME query (NPI 1245461292,
        "Bui", 85032, Dual Complete) returned 6 suggestions and then 0, i.e. OUT_OF_NETWORK on one run
        and UNKNOWN on the next. Returning as soon as suggestions render makes the fast path quicker
        than 4s and stops a slow response being misread as an empty result set — and an empty return
        after the full deadline now means the typeahead really had nothing, which is the distinction
        `_search` needs to tell "absent from this network" from "not loaded yet".

        The initial debounce wait is not optional: `_suggestions` falls back to `[role=option]`, which
        also matches the LOCATION autocomplete, so polling the instant we type can read the location
        options as if they were provider hits.
        """
        try:
            page.wait_for_timeout(_TYPEAHEAD_DEBOUNCE_MS)
        except PlaywrightError:
            return []
        deadline = time.monotonic() + _TYPEAHEAD_MAX_S
        while time.monotonic() < deadline:
            got = self._suggestions(page)
            if got:
                return got
            try:
                page.wait_for_timeout(_TYPEAHEAD_POLL_MS)
            except PlaywrightError:
                break
        return []

    def _suggestions(self, page: Page) -> list[str]:
        """The typeahead's provider suggestions. Read via UHC's own suggestion testid first, because
        `[role=option]` also matches the location autocomplete and the category chips."""
        for sel in (
            "[data-testid*='typeahead-suggestion-section']",
            "[data-testid*='typeahead-suggestion']",
            "[role=option]:visible",
        ):
            try:
                loc = page.locator(sel)
                n = loc.count()
                if n:
                    return [loc.nth(i).inner_text() or "" for i in range(min(n, 40))]
            except PlaywrightError:
                continue
        return []

    def _cards(self, page: Page) -> tuple[list[str], int]:
        for sel in _RESULT_CARDS:
            try:
                loc = page.locator(sel)
                n = loc.count()
                if n:
                    return [loc.nth(i).inner_text() or "" for i in range(min(n, 60))], n
            except PlaywrightError:
                continue
        # No recognisable cards. Fall back to the portal's own result copy — but NOT to the typeahead's
        # "N results available" live region, which counts autocomplete suggestions and produced a false
        # result_count=2 on the first live run. Only "N providers/results found|for" counts.
        text = self._page_text(page)
        m = re.search(r"([\d,]+)\s+(?:provider|result|doctor)s?\s+(?:found|for|match)", text, re.I)
        return [], int(m.group(1).replace(",", "")) if m else 0

    def _matched_name(self, cards: list[str], q: PortalQuery) -> str | None:
        want = _norm(q.provider_last_name)
        for text in cards:
            if q.npi in text or (want and want in _norm(text)):
                return text.strip().splitlines()[0][:120] if text.strip() else None
        return None

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
