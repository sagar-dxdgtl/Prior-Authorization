"""Cigna Health Care Provider Directory — hcpdirectory.cigna.com.

Covers the Ins Test 3 "Cigna Commercial" row (Orem, Randall C, NPI 1497741409, Port St. Lucie FL
34986). Cigna's commercial line is also TiC-eligible and has a public PDEX Plan-Net endpoint wired in
`payers/adapters/fhir_pdex.py`, so this portal is the *accurate* source rather than the only one —
which matters, because the walk below has two places where the honest answer is UNKNOWN.

Observed live 2026-07-28 (Angular SPA; note it uses `data-test-id`, hyphenated, NOT `data-testid` —
the whole page looks selector-less if you probe for the latter):

    /web/public/consumer/directory/search          "Find a Doctor, Dentist, or Facility in"
                                                   -> geolocation input, then one of three category
                                                      cards: Doctor by Type | Doctor by Name |
                                                      Health Facilities and Group Practices
    (same page)                                    -> `input[name=category-search-form]` appears,
                                                      placeholder "Enter a doctor's name"
    (submit)                                       -> "Login/Register" modal -> Continue as guest
    /plan-selection                                "Please Select a Plan" + "I Live in" -> Continue
    /plan-selection/options                        the plan list, grouped by network family
                                                   (Cigna SureFit / HMO / LocalPlus / OAP / PPO)
    /doctors | /cbh-providers | /dentists | …      results, with the pinned network in the query
                                                   (medicalProductCode=OAP&medicalEcnCode=OA001)

Five traps, each of which cost a live run:

1. The 2026-07-28 probe called this portal SEARCHABLE on `input[type=search]`. That control is the
   site-wide "Search Cigna.com" box in the masthead — nothing to do with the directory. The provider
   search is a plain `input[type=text]`, and it does not exist until a location is committed and a
   category card is clicked.

2. There are two buttons whose accessible name is "Search". `get_by_role("button", name="Search")`
   picks the masthead one and navigates off to www.cigna.com/search?query= — the driver silently
   leaves the directory. The directory's own submit is `button[aria-label='Submit search']`.

3. Committing the location makes the app refetch /directory-plan/v1/plans and re-render; clicking the
   category card during that window loses the card and the name input never appears. Settle first.

4. Cigna picks the results route from *which provider groups matched the name*, before any plan is
   chosen. Only-behavioral matches -> /plan-selection then /cbh-providers with providerGroupCode=B.
   Medical matches -> /guided-search (the medical/behavioral disambiguation step) with
   multiPathIndicator=true. A behavioral result set can never settle a physician's medical-network
   status, so this driver refuses to call OON off any route but /doctors. That refusal is the whole
   point of the group check below: searching "Orem" near 34986 returns "3 In-Network results" — Orin,
   Oren, Oren — under the banner "Medical Plan: Open Access Plus", and reporting that as an OON for a
   physician would be a fabricated verdict dressed in a confirmed plan.

5. "Maintenance Notice" is the app's single error page for everything: a deep-linked results route, a
   failed directory API call, an edge refusal. It is never an answer about a provider, so it maps to
   UNKNOWN — or to BLOCKED when a data XHR was seen to fail (see `_edge_refused`). Every attempt to
   deep-link a results route rendered it, including /cbh-providers minutes after the same route had
   served real results through the UI, so the walk is mandatory; whether that is missing in-memory
   state or the edge refusal below was not separable once the edge started refusing everything.

One route worth knowing about but deliberately not used here. Entering directly at /plan-selection with
the search context Cigna itself puts in that URL — searchTerm + providerAttributesName +
searchCategoryType=doctor-name + searchCategoryCode=HSC03 + providerGroupCode=P + the geo block —
reaches the real plan list, and clicking a plan routes to /doctors carrying the pinned network. It is
the only path that never calls the typeahead, so it is the obvious way to reach a MEDICAL result set
while the free-text path is being sent to /guided-search. It is not wired in because the edge began
refusing /directory-provider before its results page could be seen, and an unverified navigation that
could return a whole unfiltered doctor list is a false-OON risk (`_term_echoed` is the guard for it).

Two things this portal cannot do, verified live. It does not index NPI: typing 1497741409 returns only
the "Search '…' in Doctor Names" catch-all, so the lookup is by name only, which is why `_match` and
`_pick_suggestion` compare name tokens and why a substring compare here is a false-IN generator.
And its data plane — BOTH /directory-typeahead/v1 and /directory-provider/v1, on
`p-hcpdirectory-waf.hcpdirectory.cigna.com` — is an F5 BIG-IP ASM edge that begins refusing a client
that has been going too fast: "The requested URL was rejected. Please consult with your administrator.
Your support ID is …", HTTP 200, no CORS header, so in-page it surfaces only as a dropped request.
It served this walk happily for the first dozen lookups of 2026-07-28 and then refused everything from
the same IP, and was still refusing 15 and 35 minutes later. A hard refusal with no challenge offered
is detected, screenshotted and reported — never defeated, and never routed around by changing IP. It is
also exactly why this driver performs ONE lookup per provider and never enumerates a network.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

ENTRY = "https://hcpdirectory.cigna.com/"

# The provider-location box. Cigna gives it no id or name, only an aria-label, and the same
# placeholder exists on a second, hidden copy — hence :visible everywhere.
_GEO = (
    "input[aria-label='Geolocation search']",
    "[data-test-id='input-geolocation-search']",
    "input[placeholder*='Enter Address']",
)
_NAME_INPUT = "input[name='category-search-form']"
# NOT get_by_role("button", name="Search") — that is the masthead Cigna.com search (trap 2).
_SUBMIT = "button[aria-label='Submit search']"

_PLAN_BUTTON = "[data-test-id='button-plan-name']"
_PLAN_FAMILY = "[data-test-id='list-plan-container']"
_CONTINUE_NO_PLAN = "[data-test-id='button-continue-without-plan']"  # never clicked: unpinned directory

_RESULT_CARD = "[data-test-id='listings-provider-container']"
_RESULT_NAME = "[data-test-id='link-provider-name']"
_RESULT_HEADER = "[data-test-id='header-search-result']"

# The app's own error page. Shown for a failed directory API call *and* for any deep-linked results
# route, so it means "the portal did not answer", not "the provider is absent".
_APP_ERROR = "maintenance notice"
# F5 BIG-IP ASM refusal on the typeahead edge. Hard refusal, no challenge — report, never defeat.
_WAF_REFUSAL = "the requested url was rejected"

# Which results route the walk landed on -> what that route's result set can actually prove.
# Only Cigna's medical directory can settle a physician's network status.
_MEDICAL_ROUTE = "doctors"
_ROUTE_LABELS = {
    "doctors": "medical",
    "cbh-providers": "behavioral-health",
    "cbh-hospitals": "behavioral-health facility",
    "dentists": "dental",
    "hospitals": "hospital",
    "facilities": "facility",
    "pharmacies": "pharmacy",
    "guided-search": "medical/behavioral disambiguation",
}

# Cigna never labels a plan "Commercial", so token overlap with the 271 plan string usually scores
# zero. These are the product families the portal offers, in the order a member on each line of
# business would be looked up — a fallback only, and always recorded in the trail so the note can say
# the plan was matched by family rather than by name. A keyword is only used when it identifies exactly
# ONE plan in the list: "surefit" is deliberately absent from `commercial` because FL offers three
# SureFit variants (Florida, South Florida, "with AdventHealth | Orlando") and choosing among narrow
# regional networks by guesswork is how a wrong network gets reported as confirmed.
_FAMILY_PREFERENCE: dict[str, tuple[str, ...]] = {
    "commercial": ("open access plus", "oap", "ppo", "localplus", "hmo"),
    # Cigna's exchange network is "Cigna Connect", and it lives behind a different consumerCode
    # (HDC003) than this directory's default HDC001 — if no Connect plan is listed we confirm nothing.
    "aca": ("connect",),
    "medicare": ("medicare", "healthspring", "true choice", "preferred savings"),
    "medicaid": ("medicaid",),
}

# Lines of business whose networks are NOT sold on this default (HDC001) directory. For these, a token
# overlap against the commercial plan list is worse than no match at all — "Cigna Medicaid STAR+PLUS"
# shares the token PLUS with "Open Access Plus", which would pin a commercial PPO network and then
# report it as the member's confirmed plan. Guarded rather than scored.
_OFF_DIRECTORY_LOB = ("medicare", "medicaid")

# Tokens that carry no discriminating power in a Cigna plan label. Everything else is scored at length
# >= 3, because the most discriminating tokens this portal uses are exactly three characters — OAP,
# PPO, HMO, EPO — and a length >= 4 filter drops all of them.
_PLAN_STOPWORDS = frozenset({
    "cigna", "plan", "plans", "health", "healthcare", "care", "inc", "the", "and", "for", "of",
    "network", "networks", "medical",
})


def _tokens(s: str | None) -> set[str]:
    """Alphabetic word tokens of a name. Matching MUST be per-token, never substring: this portal's
    own typeahead answered the surname "Orem" with "Shoaf, Noremi D" — and "noremi" contains "orem",
    so a substring test reports a stranger as our provider and manufactures a false IN_NETWORK."""
    return {t for t in re.split(r"[^a-z]+", (s or "").lower()) if t}


class CignaHcpDriver(PortalDriver):
    key = "cigna-hcp"
    portal_name = "Cigna Health Care Provider Directory"

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        # Both the typeahead and the provider search live on Cigna's F5 edge host, and when that edge
        # refuses a request the browser sees a bare network error (the refusal page carries no CORS
        # header) while the Angular app just shows "Maintenance Notice". Watching for the failed XHR is
        # what separates "the portal refused us" (BLOCKED) from "the portal's own code broke" (UNKNOWN);
        # without it every block is misfiled as an application bug.
        refused: list[str] = []

        def _note_failure(req) -> None:
            if any(k in req.url for k in ("directory-provider", "directory-typeahead",
                                          "hcpdirectory-waf")):
                refused.append(req.url.split("?")[0])

        try:
            page.on("requestfailed", _note_failure)
        except PlaywrightError:  # pragma: no cover - listener registration is not expected to fail
            pass

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The walk trail rides on every verdict: a capture that stopped early must say where.
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            return result(PortalStatus.BLOCKED, "navigation to the directory timed out.",
                          screenshot=shot("nav-timeout"))
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))
        self._settle(page, 4_000)  # Angular hydrates well after networkidle
        trail.append("entry: /directory/search")

        blocked = self._refusal(page)
        if blocked:
            return result(PortalStatus.BLOCKED, blocked, screenshot=shot("waf-refusal"))
        if self._app_error(page):
            return result(PortalStatus.UNKNOWN, "the directory answered with its own 'Maintenance "
                          "Notice' error page at the entry URL — the portal did not run a search.",
                          screenshot=shot("app-error-entry"))

        if not q.zip_code:
            return result(PortalStatus.UNKNOWN, "no clinic ZIP to scope the search with; this "
                          "directory gates its provider search behind a committed location.",
                          screenshot=shot("no-zip"))
        if not self._commit_location(page, q.zip_code):
            return result(PortalStatus.UNKNOWN, f"could not commit the clinic location {q.zip_code} — "
                          "the location autocomplete offered nothing, so the category cards stayed "
                          "disabled and no provider search could run.", screenshot=shot("no-location"))
        trail.append(f"location committed: {q.zip_code}")

        if not self._open_name_search(page):
            return result(PortalStatus.UNKNOWN, "the 'Doctor by Name' card never produced its name "
                          "input — the search form did not open.", screenshot=shot("no-name-input"))
        trail.append("category: Doctor by Name")

        # NPI is deliberately absent from the term list: verified live, this portal's name index does
        # not contain NPIs, so an NPI term burns a lookup and returns only the catch-all suggestion.
        term, kind = self._search_term(q)
        if not term:
            return result(PortalStatus.UNKNOWN, "no provider name to search; this portal cannot be "
                          f"queried by NPI ({q.npi} is not indexed by its name search).",
                          screenshot=shot("no-name"))

        suggestions = self._type_term(page, term)
        if suggestions is None:
            return result(
                PortalStatus.BLOCKED,
                self._refusal(page) or self._edge_refused(refused) or (
                    f"typed {kind} {term!r} but the name-search typeahead never answered and the app "
                    "fell into its 'Maintenance Notice' error page, leaving no usable search control."
                ),
                screenshot=shot("typeahead-refused"),
            )
        trail.append(f"typed {kind} {term!r} ({len(suggestions)} typeahead suggestion(s))")

        # If Cigna's own typeahead names our provider, take that suggestion: it pins the exact person
        # and makes the app route the search to the medical directory rather than guessing a group.
        picked = self._pick_suggestion(page, suggestions, q)
        if picked:
            trail.append(f"typeahead suggestion selected: {picked}")

        if not self._click(page, _SUBMIT):
            # Selecting a typeahead suggestion can submit the search itself, taking the button with it.
            # Only treat this as a dead end if we are still sitting on the search page having achieved
            # nothing — otherwise we would abandon a search that had in fact already run.
            self._settle(page, 3_000)
            if "/directory/search" in page.url and not self._app_error(page):
                return result(PortalStatus.UNKNOWN, "the directory's own submit button disappeared "
                              "before the search could be run.", screenshot=shot("no-submit"))
            trail.append("search submitted by the typeahead selection")
        self._settle(page, 5_000)
        self._continue_as_guest(page, trail)

        blocked = self._refusal(page)
        if blocked:
            return result(PortalStatus.BLOCKED, blocked, screenshot=shot("waf-refusal-postsearch"))
        if self._app_error(page):
            route = self._route(page)
            label = _ROUTE_LABELS.get(route, route)
            edge = self._edge_refused(refused)
            if edge:
                return result(PortalStatus.BLOCKED, edge, screenshot=shot(f"edge-refused-{route}"))
            return result(
                PortalStatus.UNKNOWN,
                f"Cigna routed the search to its {label} step (/{route}) and that page rendered the "
                f"app's 'Maintenance Notice' error instead of results — a portal-side failure, not an "
                f"answer about NPI {q.npi}.",
                screenshot=shot(f"app-error-{route}"),
            )

        plan_label = self._walk_to_plan(page, q, trail)
        shot("plan-walk")

        route = self._route(page)
        trail.append(f"results route: /{route}")
        if route != _MEDICAL_ROUTE and self._force_medical(page):
            route = self._route(page)
            trail.append(f"forced medical route: /{route}")
        if self._app_error(page):
            edge = self._edge_refused(refused)
            if edge:
                return result(PortalStatus.BLOCKED, edge,
                              screenshot=shot(f"edge-refused-results-{route}"))
            return result(PortalStatus.UNKNOWN, f"the /{route} results page rendered the app's "
                          f"'Maintenance Notice' error after the plan was selected.",
                          screenshot=shot(f"app-error-results-{route}"))

        banner = self._plan_banner(page)
        codes = self._plan_codes(page)
        # Confirmation needs the pinned label AND independent corroboration that THIS page is scoped to
        # it. Two forms count, and a bare "some banner exists" does not: a banner naming a different
        # plan would otherwise confirm, which is the defect this replaced.
        plan_confirmed = bool(plan_label) and (
            (bool(banner) and self._same_plan(banner, plan_label)) or bool(codes)
        )
        if banner:
            trail.append(f"plan banner on results: {banner}")
        if codes:
            trail.append(f"plan codes in request: {codes}")
        if banner and not self._same_plan(banner, plan_label) and not codes:
            trail.append(f"BANNER MISMATCH: page says {banner!r}, we pinned {plan_label!r}")

        names = self._result_names(page)
        count = self._result_count(page, names)
        matched, why = self._match(names, q)

        if matched:
            return result(
                PortalStatus.IN_NETWORK,
                f"NPI {q.npi} matched in the Cigna directory as {matched!r} on the "
                f"{_ROUTE_LABELS.get(route, route)} results page for "
                f"{banner or plan_label or 'the selected plan'} near {q.zip_code}.",
                result_count=count, matched_name=matched, screenshot=shot("match"),
            )

        if why == "ambiguous":
            return result(
                PortalStatus.UNKNOWN,
                f"the Cigna directory returned {count} result(s) for {kind} {term!r} near "
                f"{q.zip_code} including a listing that shares the surname but carries only an "
                f"initial where a first name would settle it, so it cannot be shown to be NPI "
                f"{q.npi} rather than a namesake. Neither in- nor out-of-network follows.",
                result_count=count, screenshot=shot("ambiguous-namesake"),
            )

        if not plan_confirmed:
            return result(
                PortalStatus.UNKNOWN,
                f"the Cigna directory returned {count} result(s) for {kind} {term!r} near "
                f"{q.zip_code} without NPI {q.npi}, but the plan network could not be confirmed"
                + (f" (wanted {q.plan!r})" if q.plan else "")
                + " — absence from an unconfirmed network is not evidence of out-of-network.",
                result_count=count, screenshot=shot("absent-noplan"),
            )

        if count == 0:
            return result(
                PortalStatus.UNKNOWN,
                f"the Cigna directory returned no results for {kind} {term!r} near {q.zip_code} in "
                f"{banner or plan_label} — an empty result set does not distinguish out-of-network "
                f"from a search that simply found nothing."
                + (f" The portal itself reports {self._nearest_hint(page)}, so this is an absence "
                   f"within the search radius, not an absence from the network." if
                   self._nearest_hint(page) else ""),
                result_count=0, screenshot=shot("no-results"),
            )

        # Cigna's results heading echoes the term it filtered on ("3 In-Network results for Orem").
        # If it does not name our term, we are looking at some broader listing — a whole specialty or
        # every doctor near the ZIP — and "ours is not in it" would be a page-one artefact, not an
        # absence. Refuse to call OON off a result set we cannot prove was filtered to this name.
        if not self._term_echoed(page, term):
            return result(
                PortalStatus.UNKNOWN,
                f"the Cigna directory showed {count} result(s) under {banner or plan_label}, but its "
                f"results heading does not echo the searched name {term!r}, so this list cannot be "
                f"shown to be a search for NPI {q.npi} rather than a broader listing — absence from it "
                f"is not evidence of out-of-network.",
                result_count=count, screenshot=shot("unfiltered-results"),
            )

        if route != _MEDICAL_ROUTE:
            label = _ROUTE_LABELS.get(route, route)
            return result(
                PortalStatus.UNKNOWN,
                f"Cigna pinned {banner or plan_label} and returned {count} result(s) for {kind} "
                f"{term!r} near {q.zip_code}, none of them NPI {q.npi} — but it answered from its "
                f"{label} directory (/{route}), which it picks when its medical name index has no "
                f"match for the term. A {label} result set cannot establish whether a physician "
                f"participates in the medical network, so this is not an out-of-network finding. "
                f"Settle this NPI against Cigna's PDEX Plan-Net FHIR directory.",
                result_count=count, screenshot=shot(f"absent-wrong-directory-{route}"),
            )

        return result(
            PortalStatus.OUT_OF_NETWORK,
            f"Cigna's medical directory returned {count} in-network result(s) for {kind} {term!r} "
            f"near {q.zip_code} with {banner or plan_label} pinned, and NPI {q.npi} is not among "
            f"them — out-of-network for this plan.",
            result_count=count, screenshot=shot("absent-medical"),
        )

    # --- steps -----------------------------------------------------------------------------------

    def _search_term(self, q: PortalQuery) -> tuple[str | None, str]:
        """The one name term to search. Surname first: this typeahead matches substrings, so the bare
        surname finds strictly more than "First Last" does (verified live — "Orem" matched
        "Noremi"/"Oren", while a full name narrows to nothing). One lookup per provider, so one term."""
        if q.provider_last_name:
            return q.provider_last_name, "surname"
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        return (full or None), "full name"

    def _commit_location(self, page: Page, zip_code: str) -> bool:
        """Type the clinic ZIP and accept the Google Places suggestion. Until a location is committed
        the three category cards are inert, so this gates everything that follows."""
        box = self._first_visible(page, _GEO)
        if box is None:
            return False
        try:
            box.click()
            box.type(zip_code, delay=130)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        # [role=option] is shared by the location autocomplete and the provider typeahead, so it is
        # only safe to read here, before any name has been typed.
        for _ in range(8):
            page.wait_for_timeout(1_200)
            try:
                opts = page.locator("[role=option]:visible")
                if opts.count():
                    opts.first.click()
                    break
            except PlaywrightError:
                continue
        else:
            return False
        # Committing the location refetches the plan list and re-renders the card row (trap 3).
        self._settle(page, 5_000)
        return True

    def _open_name_search(self, page: Page) -> bool:
        """Click the "Doctor by Name" card and wait for its text input to exist."""
        if not self._click(page, None, role_name="Doctor by Name"):
            return False
        page.wait_for_timeout(3_000)
        try:
            page.locator(f"{_NAME_INPUT}:visible").first.wait_for(state="visible", timeout=20_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        return True

    def _type_term(self, page: Page, term: str) -> list[str] | None:
        """Type the term and read the typeahead. Returns the suggestions, or None if the portal broke.

        The suggestions are evidence but not the answer: at this point no plan is pinned (the app is
        still on consumerCode=HDC001 / medicalEcnCode=AMP01), so absence here proves nothing. They are
        read to find our provider's own entry, which routes the search to the medical directory.
        """
        try:
            field = page.locator(f"{_NAME_INPUT}:visible").first
            field.click()
            field.type(term, delay=140)
        except (PlaywrightTimeout, PlaywrightError):
            return None
        for _ in range(8):
            page.wait_for_timeout(1_500)
            if self._app_error(page):
                return None  # a failed typeahead call takes the whole app to its error page
            try:
                opts = page.locator("[role=option]:visible")
                n = opts.count()
                if n:
                    return [(opts.nth(i).inner_text() or "").strip() for i in range(min(n, 30))]
            except PlaywrightError:
                continue
        # No suggestions and no error page: the submit button still works, so carry on with an empty
        # list rather than giving up — the search itself does not depend on the typeahead.
        try:
            if page.locator(_SUBMIT).count():
                return []
        except PlaywrightError:
            pass
        return None

    def _pick_suggestion(self, page: Page, suggestions: list[str], q: PortalQuery) -> str | None:
        """Click the suggestion that is our provider, if Cigna offered one. Skips the first entry,
        which is always the "Search 'X' in Doctor Names" free-text catch-all rather than a person."""
        want = _tokens(q.provider_last_name)
        if not want:
            return None
        for i, text in enumerate(suggestions):
            if text.lower().startswith("search '"):
                continue
            if not want <= _tokens(text):
                continue
            try:
                opts = page.locator("[role=option]:visible")
                if i < opts.count():
                    opts.nth(i).click()
                    page.wait_for_timeout(2_500)
                    return text[:120]
            except (PlaywrightTimeout, PlaywrightError):
                return None
        return None

    def _continue_as_guest(self, page: Page, trail: list[str]) -> None:
        """Dismiss the Login/Register gate. It is a full page load, which is why the app loses its
        in-memory search context and why the routes that depend on it can error afterwards."""
        try:
            gate = page.get_by_text("Continue as guest", exact=True).first
            if gate.is_visible():
                gate.click()
                trail.append("login gate: Continue as guest")
                self._settle(page, 6_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass

    def _walk_to_plan(self, page: Page, q: PortalQuery, trail: list[str]) -> str | None:
        """/plan-selection -> Continue -> /plan-selection/options -> the member's plan.

        Returns the plan label that was clicked, or None. Never clicks "Continue without a plan":
        that yields the unpinned directory, from which absence can never mean out-of-network.
        """
        if "plan-selection" not in page.url:
            trail.append("plan-selection step NOT offered")
            return None
        # The plan list does not exist on /plan-selection — filling the "I Live in" box changes
        # nothing (its placeholder already shows the committed location while its value stays empty).
        # Only Continue reveals it.
        # Short CSS timeout: the testid is the durable handle if Cigna ships one, but the accessible
        # name is what was verified live, so do not spend the full budget waiting for a maybe.
        if not self._click(page, "[data-test-id='button-continue']", role_name="Continue",
                           timeout_ms=5_000):
            trail.append("plan-selection 'Continue' NOT clickable")
            return None
        self._settle(page, 5_000)
        trail.append("plan-selection: Continue")

        try:
            buttons = page.locator(f"{_PLAN_BUTTON}:visible")
            labels = [(buttons.nth(i).inner_text() or "").strip() for i in range(buttons.count())]
        except PlaywrightError:
            labels = []
        if not labels:
            trail.append("no plan list rendered")
            return None
        trail.append(f"{len(labels)} plan(s) offered")

        idx, how = self._best_plan(labels, q, page)
        if idx is None:
            trail.append(f"no plan matched {q.plan!r}")
            return None
        try:
            page.locator(f"{_PLAN_BUTTON}:visible").nth(idx).click()
        except (PlaywrightTimeout, PlaywrightError):
            trail.append(f"plan {labels[idx]!r} not clickable")
            return None
        self._settle(page, 8_000)  # post-click, so a slow settle cannot discard the pinned plan
        trail.append(f"plan pinned ({how}): {labels[idx]}")
        return labels[idx]

    def _best_plan(self, labels: list[str], q: PortalQuery, page: Page) -> tuple[int | None, str]:
        """Choose the plan, or nothing. Distinctive-token overlap with the 271 plan string first, then
        the product family the member's line of business is sold under — Cigna labels networks by
        product (OAP / PPO / LocalPlus / SureFit), never "Commercial", so a plan string like "Cigna
        Commercial" scores zero on tokens and must fall through to the family preference.

        Returning None is a first-class outcome: an unpinned network yields UNKNOWN, which is correct,
        whereas a plausible-looking wrong network yields a confidently wrong verdict.
        """
        lob = self._line_of_business(q)
        families = self._families(page, labels)
        # A Medicare or Medicaid network is not sold on this directory, so scoring its plan string
        # against a commercial plan list can only mislead — those lines go straight to the guarded
        # family keywords and confirm nothing if none of them is present.
        if lob not in _OFF_DIRECTORY_LOB:
            want = self._plan_tokens(q.plan)
            best, best_score = None, 0
            for i, text in enumerate(labels):
                score = len(self._plan_tokens(f"{text} {families.get(i, '')}") & want)
                if score > best_score:
                    best, best_score = i, score
            if best is not None:
                return best, "plan name"

        for keyword in _FAMILY_PREFERENCE.get(lob, ()):
            hits = [i for i, text in enumerate(labels)
                    if keyword in text.lower() or keyword in families.get(i, "").lower()]
            # Exactly one, or we do not know which of several regional networks the member is on.
            if len(hits) == 1:
                return hits[0], f"product family {keyword!r}"
        return None, "none"

    def _plan_tokens(self, text: str | None) -> set[str]:
        """Scoring tokens of a plan label: length >= 3 so OAP/PPO/HMO survive, minus the words every
        Cigna label shares."""
        return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
                if len(t) >= 3 and t not in _PLAN_STOPWORDS}

    def _families(self, page: Page, labels: list[str]) -> dict[int, str]:
        """Map each plan button to its family heading (OAP / PPO / HMO / LocalPlus / Cigna SureFit).
        The family, not the plan label, is what carries "OAP" for "Open Access Plus, OA plus, …"."""
        out: dict[int, str] = {}
        try:
            groups = page.locator(f"{_PLAN_FAMILY}:visible")
            seen = 0
            for g in range(groups.count()):
                block = groups.nth(g)
                header = (block.locator("[data-test-id='header-plan']").first.inner_text() or "").strip()
                # :visible must match how `labels` was collected, or the flat indices desync and every
                # plan gets attributed to the wrong product family.
                inner = block.locator(f"{_PLAN_BUTTON}:visible")
                for _ in range(inner.count()):
                    if seen < len(labels):
                        out[seen] = header
                    seen += 1
        except PlaywrightError:
            pass
        return out

    def _line_of_business(self, q: PortalQuery) -> str:
        """Reuse the domain's own LOB rules so this driver and the network resolver cannot disagree."""
        from network_probe.domain.line_of_business import line_of_business

        lob = line_of_business(q.plan or "", None)
        if lob in ("medicare", "dual"):
            return "medicare"
        if lob == "medicaid":
            return "medicaid"
        text = (q.plan or "").lower()
        if any(k in text for k in ("marketplace", "exchange", "aca", "individual & family", "connect")):
            return "aca"
        return "commercial"

    # --- reading the results page ----------------------------------------------------------------

    def _force_medical(self, page: Page, timeout_ms: int = 45_000) -> bool:
        """Re-ask the same question of the MEDICAL directory. Returns whether the route changed.

        Cigna answers a name search from whichever provider group its index matched, and signals the
        choice in the URL's `providerGroupCode`. Verified live 2026-07-28 for Randall Orem / 34986: the
        search landed on `/cbh-providers` with `providerGroupCode=B` (behavioral) even though the SAME
        URL still carried the pinned medical plan as `medicalProductCode=OAP&medicalEcnCode=OA001`. So
        the plan was right and only the group was wrong, and the medical answer was one navigation away.

        `P` is the medical group code, established by trying the alternatives against the live app:
        `providerGroupCode=P` renders a real medical result set, `M` renders the app's own
        "Maintenance Notice" error page. Every other parameter — location, term, plan codes — is carried
        over untouched, so this re-asks the identical question rather than a broader one.

        Without this the driver could only ever return UNKNOWN for a provider absent from Cigna's
        behavioral index, which is most physicians.
        """
        url = page.url
        if "/directory/" not in url or "providerGroupCode" not in url:
            return False
        before = self._route(page)
        target = re.sub(r"/directory/[a-z0-9\-]+", f"/directory/{_MEDICAL_ROUTE}", url)
        target = re.sub(r"providerGroupCode=[A-Z]", "providerGroupCode=P", target)
        target = re.sub(r"providerGroupCodes=[A-Z]", "providerGroupCodes=P", target)
        if target == url:
            return False
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page)
        # An app error here means the medical group rejected the request; the caller keeps the original
        # non-medical reading and its UNKNOWN rather than inventing a verdict from an error page.
        return not self._app_error(page) and self._route(page) != before

    def _route(self, page: Page) -> str:
        m = re.search(r"/directory/([a-z0-9\-]+)", page.url)
        return m.group(1) if m else "unknown"

    def _plan_codes(self, page: Page) -> str | None:
        """Cigna's own medical plan identifiers, read back off the request URL.

        The results URL carries `medicalProductCode` and `medicalEcnCode` (e.g. OAP / OA001) — the
        payer's identifiers for the network it is answering from. That is corroboration a rendered
        banner cannot beat, and it survives the forced-medical re-navigation, which drops the banner
        (verified live: the /doctors page rendered no "Medical Plan:" line, and without this the driver
        reported "the plan network could not be confirmed" about a plan it had just successfully pinned).
        """
        m = re.search(r"medicalProductCode=([A-Za-z0-9]+)", page.url or "")
        e = re.search(r"medicalEcnCode=([A-Za-z0-9]+)", page.url or "")
        if not m:
            return None
        return f"{m.group(1)}/{e.group(1)}" if e else m.group(1)

    def _same_plan(self, banner: str | None, label: str | None) -> bool:
        """Does the page's plan banner actually name the plan we pinned? Distinctive-token overlap, so
        "Medical Plan: Open Access Plus, OA plus..." corroborates "Open Access Plus, OA plus...".
        Without this, `bool(banner)` accepted ANY banner — including one naming a different network."""
        if not banner or not label:
            return False
        stop = {"plan", "medical", "dental", "behavioral", "pharmacy", "change", "network", "and"}
        bt = {t for t in re.split(r"[^a-z0-9]+", banner.lower()) if len(t) >= 3 and t not in stop}
        lt = {t for t in re.split(r"[^a-z0-9]+", label.lower()) if len(t) >= 3 and t not in stop}
        return bool(bt & lt)

    def _nearest_hint(self, page: Page) -> str | None:
        """Cigna's "The nearest location is 37.6mi away in Palm Beach Gardens, FL" line. Verified live
        for Orem / 34986: a match for the searched name exists outside the radius, which means an empty
        local result set is a radius artefact and must not read as absence from the network."""
        m = re.search(r"nearest location is\s+([\d.]+\s*mi[^.]{0,60})", self._page_text(page), re.I)
        return f"the nearest match is {m.group(1).strip()}" if m else None

    def _plan_banner(self, page: Page) -> str | None:
        """The results page states the pinned network in a "Medical Plan: <name>" line next to a
        "Change Plan" link. Reading it back off the page is the plan-confirmed proof — the click alone
        is not, because a re-render could have dropped it."""
        for line in self._page_text(page).splitlines():
            if re.match(r"\s*(medical|dental|behavioral|pharmacy)\s+plan\s*:", line, re.I):
                return re.sub(r"\s*Change Plan\s*$", "", line.strip())[:160]
        return None

    def _result_names(self, page: Page) -> list[str]:
        for sel in (_RESULT_NAME, _RESULT_CARD):
            try:
                loc = page.locator(f"{sel}:visible")
                n = loc.count()
                if n:
                    return [(loc.nth(i).inner_text() or "").strip() for i in range(min(n, 60))]
            except PlaywrightError:
                continue
        return []

    def _result_count(self, page: Page, names: list[str]) -> int:
        """Prefer Cigna's own "N In-Network results for X" header — it is the total across pages, and
        it is a static <h1> rather than an aria-live region, so it cannot be inflated by the typeahead
        announcing its own suggestion count."""
        try:
            head = page.locator(_RESULT_HEADER)
            if head.count():
                text = head.first.inner_text() or ""
                m = re.search(r"([\d,]+)\s+(?:in-network\s+)?results?", text, re.I)
                if m:
                    return int(m.group(1).replace(",", ""))
        except PlaywrightError:
            pass
        return len(names)

    def _term_echoed(self, page: Page, term: str) -> bool:
        """Does the results heading name the term we searched? Proves the list is *this* lookup."""
        try:
            head = page.locator(_RESULT_HEADER)
            if not head.count():
                return False
            return _tokens(term) <= _tokens(head.first.inner_text() or "")
        except PlaywrightError:
            return False

    def _match(self, names: list[str], q: PortalQuery) -> tuple[str | None, str]:
        """Our provider among the results. The cards carry names, not NPIs, so the name is the only
        handle — matched per token (see `_tokens`), and a known first name MUST agree.

        The previous version kept a `surname_only` fallback and returned it when no better candidate
        turned up, so a same-surname stranger was reported as our provider: verified, a query for
        Randall Orem matched "Orem, Jessica L, DO" and the caller turned that into IN_NETWORK with a
        note claiming NPI 1497741409 had matched. The docstring already promised the guard the code
        skipped. Agreement is prefix-compatible in either direction so abbreviated cards ("Orem, R C")
        still match, which is the case the old fallback was really carrying."""
        want = _tokens(q.provider_last_name)
        if not want:
            return None, "no surname to match"
        first = _tokens(q.provider_first_name)
        ambiguous = False
        for text in names:
            toks = _tokens(text)
            if not want <= toks:
                continue
            if not first:
                ambiguous = True   # surname agrees, nothing to separate namesakes by
                continue
            others = toks - want
            if first & others:
                return text[:120], "surname + first name"
            # An initial compatible with our first name ("Orem, R C" for Randall) decides NOTHING:
            # it could equally be Robert Orem. Returning it would be a false IN; treating it as
            # absent would be a false OON while our provider may be the very row we skipped. So it
            # is reported as ambiguous and the caller answers UNKNOWN.
            if {t for t in others if len(t) == 1} & {f[0] for f in first}:
                ambiguous = True
        return (None, "ambiguous" if ambiguous else "no card matched")

    # --- plumbing ---------------------------------------------------------------------------------

    def _click(self, page: Page, selector: str | None, role_name: str | None = None,
               timeout_ms: int = 12_000) -> bool:
        """Click by data-test-id, then by exact accessible name. Loose text is deliberately not a
        fallback here: this page has two "Search" buttons and a "Continue" plus a "Continue without a
        plan", so a loose match picks the wrong control and the walk carries on believing it worked."""
        for kind, value in (("css", selector), ("role", role_name)):
            if not value:
                continue
            try:
                if kind == "css":
                    loc = page.locator(f"{value}:visible").first
                else:
                    loc = page.get_by_role("button", name=value, exact=True).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            # The click landed. Settling is best-effort and must never invalidate it.
            self._settle(page)
            return True
        return False

    def _first_visible(self, page: Page, selectors: tuple[str, ...], timeout_ms: int = 12_000):
        for sel in selectors:
            try:
                loc = page.locator(f"{sel}:visible").first
                loc.wait_for(state="visible", timeout=timeout_ms)
                return loc
            except (PlaywrightTimeout, PlaywrightError):
                continue
        return None

    def _settle(self, page: Page, pause_ms: int = 3_000) -> None:
        """Best-effort wait for the next step. Never raises: this portal streams analytics and may
        never reach networkidle, and a settle timeout must not be mistaken for a failed step."""
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass

    def _app_error(self, page: Page) -> bool:
        return _APP_ERROR in self._page_text(page).lower()

    def _edge_refused(self, refused: list[str]) -> str | None:
        """A directory data call was dropped at the network layer. Both the typeahead and the provider
        search sit behind p-hcpdirectory-waf.hcpdirectory.cigna.com; when that F5 edge rejects a
        request it answers a no-CORS 'The requested URL was rejected' page, which the browser can only
        report as a bare network failure while the Angular app shows its generic maintenance notice.
        Verified 2026-07-28 by navigating straight to the endpoint and reading the refusal.
        """
        if not refused:
            return None
        endpoints = sorted({u.rsplit("/", 1)[-1] or u for u in refused})
        return (
            "Cigna's directory data plane refused the request: the "
            f"{', '.join(endpoints)} call(s) to p-hcpdirectory-waf.hcpdirectory.cigna.com were dropped "
            "at the network layer and the app fell back to its 'Maintenance Notice' page. Navigating "
            "to that endpoint directly returns the F5 BIG-IP ASM refusal 'The requested URL was "
            "rejected. Please consult with your administrator.' — a hard refusal with no challenge "
            "offered, so there is nothing to satisfy and nothing was bypassed. It is rate-related: the "
            "same host served this walk earlier in the session. Retry at human pace, or settle this "
            "NPI against Cigna's PDEX Plan-Net FHIR directory."
        )

    def _refusal(self, page: Page) -> str | None:
        """F5 BIG-IP ASM hard refusal, quoting the support ID so the block is auditable. Detected and
        reported, never worked around; there is no challenge here for anyone to satisfy."""
        text = self._page_text(page)
        if _WAF_REFUSAL not in text.lower():
            return None
        m = re.search(r"support ID is:\s*([0-9a-f\-]+)", text, re.I)
        return (
            "Cigna's edge (F5 BIG-IP ASM) refused the request: 'The requested URL was rejected'"
            + (f", support ID {m.group(1)}" if m else "")
            + ". A hard refusal with no challenge offered — reported, not bypassed. Use Cigna's PDEX "
            "Plan-Net FHIR directory for this lookup."
        )

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
