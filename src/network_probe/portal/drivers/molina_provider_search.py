"""Molina provider search — molina.sapphirecareselect.com (Zelis "Sapphire" guest directory).

Covers the Ins Test 3 row "Molina Medicaid TX" (Clinton Twaddell, NPI 1437131901, Fort Worth). That row
matters more than most: Managed Medicaid is TiC-exempt, so this portal and Molina's PDEX Plan-Net FHIR
are the ONLY provider-network sources for it — there is no MRF to fall back on. Separately, a provider
not enrolled in Texas Medicaid cannot be in-network for it (`domain.provider_network.enrollment_negative`
is the decisive negative filter for this line); this driver answers the directory question only.

FINDING THE PORTAL. Candidates tried on 2026-07-28, each in a real Chromium:

    https://molina.sapphirecareselect.com/                       WORKS — guest directory, no auth  <=
    molinahealthcare.sapphirecareselect.com                      NXDOMAIN (no such host)
    https://providersearch.molinahealthcare.com/                 retired host; 302s to an error page
    .../members/tx/en-US/mem/provider-directories.aspx           HTTP 200, 313 chars, title "Error Page"

The live tool is vendor-hosted and carries no molinahealthcare.com in its URL at all. `targets.py` still
points at the dead .aspx hub — see the report, not fixed here. DNS puts the Zelis host behind Imperva
(`d7va234.ng.impervadns.net`), but no challenge, no interstitial and no `_incapsula_resource` marker was
ever served to us across four runs: it is fronted, not gated.

VERIFIED GUEST FLOW (2026-07-28, four live runs) — three modal steps, each gating the next:

    landing            "Just browsing?"                    -> Continue          (guest mode)
    step 2             "Where do you first want to look…"   -> location + Continue
    step 3             "Plan/Program: Select a network"     -> network + Continue   <- pins the network
    home               header search box                    -> type NPI / surname + Enter
    /search/name/<t>   result cards                         -> read the verdict

The landing URL arrives pre-pinned to Molina's own default — `?network_id=12&geo_location=32.8154472,
-117.1277267` is AZ Molina Complete Care scoped to San Diego (Molina's HQ). Every step must therefore be
driven and then READ BACK, never assumed. After a correct walk the URL becomes `network_id=32` with the
Fort Worth geo, and the header reads `TX - Texas STAR`.

WHY THIS PORTAL IS UNUSUALLY GOOD EVIDENCE. It re-states the pinned network next to the answer, so the
verdict is self-documenting rather than inferred from our own walk. Both strings are captured verbatim:
  * every result card carries `In "TX - Texas STAR" Plan/Program` plus `Matched on:PROVIDER IDENTIFIER`
    or `Matched on:NAME`
  * the empty state carries `No results for <term>` and
    `For TX - Texas STAR in 76132 or within 3500 miles max radius`

TRAPS, each observed live:

1. THE SHELL HYDRATES ON ITS OWN SCHEDULE. `_wait_shell` polls for the controls instead of sleeping a
   fixed amount; a fixed settle reports a dead portal that is merely slow.
2. STORAGE_STATE REUSE BREAKS THIS SPA (restores to `network_id=` empty and never hydrates). Capture it
   with a fresh context.
3. HEADER AND MODAL SHARE data-cy VALUES. While the modal is open, `input[data-cy='location-input
   .selector']` resolves TWICE. Verified: index 0 is the header copy and is `readonly`; index 1 is the
   modal's. So modal controls are scoped to `.cdk-overlay-popover` and read-backs use index 0.
4. THE LOCATION TYPEAHEAD REJECTS PO-BOX ZIPS. The sheet's clinic ZIP 76152 returns ONLY "Use Current
   Location" — no geocode, ever (verified twice). 76132 returns "Fort Worth, TX — 76132". So the clinic
   ZIP alone is not a usable handle and `_location_terms` falls back.
5. NEVER TYPE THE STATE NAME. "Texas" suggests "Texas County, MO". A suggestion is accepted only when it
   names the state we are searching.
6. NEVER CLICK "Use Current Location" — it is the first suggestion for every term and resets the geo to
   the portal's San Diego default, silently scoping the search to California.
7. THE AUTOSUGGEST PANEL IS NOT THE RESULT SET. `autosuggest.autocomplete-option.*` matches the seven
   "Browse by Category" tiles, present on every keystroke. Counting them reports 7 results for an empty
   search. Read the cards on the results page instead.
8. DO NOT PRESS Escape BEFORE SEARCHING — it leaves the box in a state where Enter does not navigate.
9. `search-results-header` DOES NOT EXIST ON THE EMPTY STATE, and the literal string "No results found."
   is present in the DOM of *every* results page (a hidden template node). Neither may be used as the
   empty-state test; `search-results-header.no-results-header` is the real one.
10. THE RESULT SET IS RADIUS-LIMITED AND PAGED: the results URL carries `limit=10&radius=10&page=1`. The
    portal only widens the radius when the in-radius search came back EMPTY, and then stamps
    `radiusExpanded=true` on the URL. Verified: "Garcia" returned `11 Providers:` with 10 cards at
    radius=10 and no expansion — so absence from a populated page-1 set proves nothing about the network.
    This is why `_oon_blockers` demands `radiusExpanded=true` AND total == cards read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus
from network_probe.portal.plan_match import match_plan_with_fallback as match_plan

ENTRY = "https://molina.sapphirecareselect.com/"

# Zelis ships no data-testid; `data-cy` is its own test hook and is the durable handle here. Every
# selector below was read off a live page on 2026-07-28 — none is guessed.
POPOVER = ".cdk-overlay-popover"  # the guest modal lives in a top-layer popover, not a mat-dialog
NET_SELECT = "mat-select[data-cy='global-network.network-select']"
LOC_INPUT = "input[data-cy='location-input.selector']"
SEARCH = "input[data-cy='autosuggest.input']"
OPTION = "mat-option"

CARDS = "[data-cy^='search-results.card-']"
RESULTS_HDR = "[data-cy='search-results-header']"
RESULTS_FOR = "[data-cy='search-results-header.results-for']"
NONE_HDR = "[data-cy='search-results-header.no-results-header']"
NONE_SUB = "[data-cy='search-results-header.search-results-none-subheader']"

# "1 Provider:" / "11 Providers:" — the portal's own total, which exceeds the 10 cards on page 1.
_TOTAL_RE = re.compile(r"([\d,]+)\s+Providers?\b", re.I)
_NPI_LINE_RE = re.compile(r"\bNPI:\s*(\d{10})\b")
_PLACEHOLDER_OPTION = "select a network"
_MAX_CARDS = 40  # page 1 holds 10; anything past this is a redesign, and a partial read blocks an OON

# A challenge is DETECTED and reported, never solved. Matched against visible text only, so a dormant
# bot-protection script never counts as a gate.
_CHALLENGE_PHRASES = (
    "press & hold", "press and hold", "verify you are a human", "verify you are human",
    "are you a robot", "complete the security check", "checking your browser", "i'm not a robot",
)

# DEFECT CLASS A — badge inversion. `In "X" Plan/Program` is a SUBSTRING of `Not in "X" Plan/Program`,
# and `\bIn\b` does NOT save you: the word boundary still matches the "In" inside "Not In". The only
# correct test is to read what comes BEFORE the phrase, so negatives are enumerated and checked FIRST.
_NEGATION_RE = re.compile(
    r"\b(not|non-?participating|no\s+longer|excluded|terminated|inactive|ineligible"
    r"|out\s*-?\s*of\s*-?\s*network)\b",
    re.I,
)
_NEGATION_WINDOW = 48  # chars before the phrase that a negation can live in


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str | None) -> list[str]:
    """Lowercased word tokens. Used for WHOLE-TOKEN matching, never substring matching."""
    return [t for t in re.split(r"[^A-Za-z0-9]+", (s or "").lower()) if t]


def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    """Whole-token contiguous containment.

    Substring containment is defect class B's cousin: `_norm("Texas STAR")` is a substring of
    `_norm("Texas STAR+PLUS")`, so a naive `in` test cannot tell the two Molina TX Medicaid networks
    apart — and they are different networks with different provider sets.
    """
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def _plan_phrase_re(label: str) -> re.Pattern[str]:
    return re.compile(rf'\bin\s+"{re.escape(label)}"\s+plan\s*/\s*program', re.I)


def plan_attested(text: str | None, label: str | None) -> bool:
    """Whether `text` says our provider IS in `label`'s network, with no negation in front of it."""
    if not label or not text:
        return False
    for m in _plan_phrase_re(label).finditer(text):
        if not _NEGATION_RE.search(text[max(0, m.start() - _NEGATION_WINDOW):m.start()]):
            return True
    return False


def plan_negated(text: str | None, label: str | None) -> bool:
    """Whether `text` says our provider is NOT in `label`'s network. Checked BEFORE the positive."""
    if not label or not text:
        return False
    return any(
        _NEGATION_RE.search(text[max(0, m.start() - _NEGATION_WINDOW):m.start()])
        for m in _plan_phrase_re(label).finditer(text)
    )


@dataclass
class NetworkPin:
    """The Plan/Program option we selected, and how strong that identification is.

    `strong` is what licenses an OUT_OF_NETWORK. A weak pin still scopes the *search* (a hit inside it
    is real), but absence from a network we only guessed at is not evidence — that is the CareFlex
    mis-pin `plan_match` was written to stop.
    """

    label: str | None
    basis: str
    strong: bool = False


@dataclass
class Card:
    """One result row, parsed. `npi` is read only from the card's own `NPI: …` line."""

    text: str
    headline: str
    npi: str | None
    tokens: list[str] = field(default_factory=list)

    @property
    def is_provider_row(self) -> bool:
        """A provider row has both a name and an NPI. Anything else is UI chrome or a half-hydrated
        skeleton (defect class G) and must not be counted as a provider we checked."""
        return bool(self.headline) and self.npi is not None


@dataclass
class ResultSet:
    cards: list[Card] = field(default_factory=list)
    total: int | None = None  # the portal's own "N Providers:" count
    none_sub: str = ""
    results_for: str = ""
    dom_count: int = 0
    read_complete: bool = True  # False when a card read raised, or the cap truncated the read
    url: str = ""

    @property
    def providers(self) -> list[Card]:
        return [c for c in self.cards if c.is_provider_row]


class MolinaProviderSearchDriver(PortalDriver):
    # Restoring storage_state wedges this Sapphire SPA — established live during the review.
    requires_fresh_context = True
    key = "molina-provider-search"
    portal_name = "Molina Provider Search (Zelis / Sapphire)"

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The walk trail always travels with the verdict: a silently-partial walk that then reports
            # OON is the worst failure this layer can have, and the trail makes it impossible to hide.
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw,
            )

        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            pass
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation to {ENTRY} failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))

        challenge = self._challenge(page)
        if challenge:
            # Detected, screenshotted, reported. Never solved — the payer routes to its FHIR directory.
            return result(PortalStatus.BLOCKED,
                          f"an interactive bot challenge is being shown (visible text matched "
                          f"{challenge!r}). Not solved by design — route NPI {q.npi} to Molina's PDEX "
                          f"Plan-Net FHIR directory instead.", screenshot=shot("challenge"))

        if not self._wait_shell(page):
            chars = len(self._body(page).strip())
            return result(
                PortalStatus.BLOCKED,
                f"the Zelis shell never hydrated (no guest modal and no header network select within "
                f"75s; body held {chars} readable chars). Either the SPA is still booting or a restored "
                f"storage_state wedged it on an empty '?network_id=' (see trap 2). Retry with a fresh "
                f"browser context.",
                screenshot=shot("no-shell"),
            )
        trail.append("shell hydrated")

        pin, loc_label, walk = self._walk_to_plan(page, q)
        trail.extend(walk)
        shot("plan-walk")

        # Both gates are read back off the PERSISTENT HEADER (index 0), not off our own bookkeeping —
        # the header is the portal telling us what it will actually search.
        header_plan = self._header_text(page, NET_SELECT)
        header_loc = self._header_value(page, LOC_INPUT)
        plan_pinned = bool(pin.label) and _norm(header_plan) == _norm(pin.label)
        geo, geo_why = self._location_granularity(header_loc, q)
        trail.append(f"header confirms plan={header_plan!r} location={header_loc!r} (geo: {geo})")
        if pin.label and not plan_pinned:
            trail.append(f"WARNING: header plan {header_plan!r} != picked {pin.label!r}")

        if not plan_pinned:
            # Do not search at all. Every option in this list is a single state's Medicaid/Marketplace
            # network and the portal's default is AZ — so a hit in an unpinned network would be evidence
            # about a network this member is not in, and issuing the query would only manufacture a
            # misleading screenshot.
            return result(
                PortalStatus.UNKNOWN,
                f"could not pin plan {q.plan!r} in Molina's Plan/Program list, so no provider search was "
                f"issued: the portal was still on {header_plan or 'its default network'} ({pin.basis}). "
                f"Every option in that list is one state's own network, so neither presence nor absence "
                f"in an unpinned Molina network is evidence about this member's plan.",
                screenshot=shot("plan-unconfirmed"),
            )

        # NPI first (exact, and the portal reports "Matched on:PROVIDER IDENTIFIER"), then the name —
        # which is what a clinic staffer actually types.
        empties: list[str] = []
        unusable: list[tuple[str, int | None, str | None]] = []
        for term, kind in self._search_terms(q):
            outcome = self._search(page, term)
            if outcome == "challenge":
                return result(PortalStatus.BLOCKED,
                              f"a bot challenge appeared while searching {kind} {term!r}. Not solved by "
                              f"design — route NPI {q.npi} to Molina's PDEX Plan-Net FHIR directory.",
                              screenshot=shot("challenge-search"))
            if outcome == "error":
                trail.append(f"search {kind} {term!r}: could not be issued")
                continue

            rs = self._read_results(page)
            trail.append(
                f"search {kind} {term!r}: {len(rs.providers)} provider row(s) of {rs.dom_count} card "
                f"element(s), portal total {rs.total}, complete-read={rs.read_complete}"
            )

            ours = next((c for c in rs.providers if self._is_ours(c, q)), None)
            if ours:
                if plan_negated(ours.text, header_plan):
                    # Class A, in the live phrasing: the card names the pinned network in a NEGATIVE
                    # sentence. Read positively that is a false IN, so it must not be one.
                    return result(
                        PortalStatus.OUT_OF_NETWORK,
                        f"Molina's Zelis directory listed NPI {q.npi} as {ours.headline!r} but states it "
                        f"is NOT in {header_plan!r} — out-of-network for this plan.",
                        result_count=rs.total if rs.total is not None else len(rs.providers),
                        matched_name=ours.headline, screenshot=shot(f"negated-{kind}"),
                    )
                attested = plan_attested(ours.text, header_plan)
                return result(
                    PortalStatus.IN_NETWORK,
                    f"Molina's Zelis directory returned NPI {q.npi} for {kind} {term!r} in "
                    f"{header_plan!r} near {header_loc or q.zip_code or 'the clinic'} — listed as "
                    f"{ours.headline!r}. Identity is the card's own 'NPI: {ours.npi}' line, not the "
                    f"search term. " + self._attestation(page, ours, header_plan, attested),
                    result_count=rs.total if rs.total is not None else len(rs.providers),
                    matched_name=ours.headline, screenshot=shot(f"match-{kind}"),
                )

            if not rs.providers:
                empties.append(
                    f"{kind} {term!r}" + (f" — portal said: {rs.none_sub!r}" if rs.none_sub else "")
                )
                continue
            if kind == "NPI":
                # An exact-identifier query that returns rows which are not ours means the portal fell
                # back to keyword matching; that set is not "everyone with this NPI", so it can never
                # support an absence reading. Fall through to the name term.
                trail.append(
                    f"NPI query returned {len(rs.providers)} non-matching row(s) — keyword fallback, "
                    f"not evidence of absence"
                )
                continue

            blockers = self._oon_blockers(q, rs, term, pin, header_plan, geo, geo_why, header_loc)
            if not blockers:
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"Molina's Zelis directory returned all {rs.total} provider(s) matching {kind} "
                    f"{term!r} in {header_plan!r} near {header_loc!r} — the portal had already widened "
                    f"to its maximum radius (radiusExpanded), the page held the complete set, every row "
                    f"rendered an NPI line, and NPI {q.npi} is not among them. Out-of-network for this "
                    f"plan. " + self._attestation(page, None, header_plan, False),
                    result_count=rs.total, screenshot=shot(f"absent-{kind}"),
                )
            trail.append(f"{kind} {term!r} cannot support an OON: {'; '.join(blockers)}")
            unusable.append((
                f"for {kind} {term!r} the portal returned "
                f"{rs.total if rs.total is not None else len(rs.providers)} row(s) without NPI "
                f"{q.npi}, but " + "; ".join(blockers),
                rs.total if rs.total is not None else len(rs.providers),
                shot(f"absent-unconfirmed-{kind}"),
            ))

        if unusable:
            note, count, snap = unusable[-1]
            return result(
                PortalStatus.UNKNOWN,
                f"Molina's Zelis directory answered for NPI {q.npi} but the answer cannot be read as a "
                f"verdict: {note}. Absence is only evidence when the right network was demonstrably and "
                f"completely searched — route this NPI to Molina's PDEX Plan-Net FHIR directory.",
                result_count=count, screenshot=snap,
            )

        detail = "; ".join(empties) if empties else "no search term could be issued"
        return result(
            PortalStatus.UNKNOWN,
            f"Molina's Zelis directory returned no provider rows for {detail}. The network was pinned to "
            f"{header_plan!r} and the search was scoped to {header_loc or 'an unconfirmed location'}. An "
            f"empty result set does not distinguish out-of-network from a failed search, so this is "
            f"UNKNOWN — route NPI {q.npi} to Molina's PDEX Plan-Net FHIR directory instead.",
            result_count=0, screenshot=shot("no-results"),
        )

    # --- the walk ----------------------------------------------------------------------------------

    def _walk_to_plan(self, page: Page, q: PortalQuery) -> tuple[NetworkPin, str | None, list[str]]:
        """Guest modal → location → network. Returns (network pin, committed location, trail)."""
        trail: list[str] = []

        # Step 1: "Just browsing? … Continue". Only present on a fresh context.
        if self._popover_open(page):
            if self._click_continue(page):
                trail.append("guest gate: Continue")
            else:
                return (NetworkPin(None, "the guest gate's Continue was not clickable"), None,
                        trail + ["guest gate 'Continue' NOT clickable"])

        # Step 2: location. Inside the modal while the walk runs, in the header otherwise.
        root = POPOVER if self._popover_open(page) else ""
        loc_label = self._commit_location(page, root, q)
        trail.append(f"location: {loc_label!r}" if loc_label else "location NOT committed")
        if self._popover_open(page):
            if self._click_continue(page):
                trail.append("location step: Continue")
            else:
                return (NetworkPin(None, "the location step's Continue was not clickable"), loc_label,
                        trail + ["location step 'Continue' NOT clickable"])

        # Step 3: the network. Load-bearing — everything after is only as valid as this pin, so
        # `_pick_network` declines rather than guesses.
        root = POPOVER if self._popover_open(page) else ""
        pin = self._pick_network(page, root, q)
        if not pin.label:
            return pin, loc_label, trail + [f"no network matched plan {q.plan!r} ({pin.basis})"]
        trail.append(f"network pinned: {pin.label!r} ({pin.basis}, strong={pin.strong})")
        if self._popover_open(page):
            if self._click_continue(page):
                trail.append("network step: Continue")
            else:
                return pin, loc_label, trail + ["network step 'Continue' NOT clickable"]
        self._settle(page, 3_000)

        # The location field can still hold the portal's San Diego default if step 2's typeahead came
        # up empty; the header is authoritative, so retry there before searching an unintended geography.
        if self._location_granularity(self._header_value(page, LOC_INPUT), q)[0] == "none":
            retry = self._commit_location(page, "", q)
            trail.append(f"location retried via header: {retry!r}")
        return pin, loc_label, trail

    def _click_continue(self, page: Page) -> bool:
        """Click the modal's Continue.

        Role-based by design and verified live: at the network step the popover exposes NO
        `data-cy*='continue'` node at all (only `button-flat`), so a data-cy-only click times out there
        while the accessible name works at every step. The settle is deliberately AFTER the click —
        treating a slow settle as a failed click is how a walk reports 'not clickable' about a step it
        had already completed.
        """
        for scope in (POPOVER, ""):
            try:
                base = page.locator(scope) if scope else page
                btn = base.get_by_role("button", name="Continue", exact=True).first
                btn.wait_for(state="visible", timeout=8_000)
                btn.click(timeout=10_000)
            except (PlaywrightTimeout, PlaywrightError):
                continue
            self._settle(page, 3_000)
            return True
        return False

    def _commit_location(self, page: Page, root: str, q: PortalQuery) -> str | None:
        """Type a location and commit a suggestion that is in the state we are searching.

        Never accepts an out-of-state suggestion and never clicks "Use Current Location" — both would
        silently move the search to another geography, and this portal scopes results by distance.
        """
        state = self._state_code(q)
        sel = f"{root} {LOC_INPUT}" if root else f"{LOC_INPUT}:not([readonly])"
        try:
            inp = page.locator(sel).first
            inp.wait_for(state="visible", timeout=8_000)
        except (PlaywrightTimeout, PlaywrightError):
            if root:
                return None
            try:  # header path: the field is readonly until clicked, which opens an editable copy
                page.locator(LOC_INPUT).first.click(timeout=8_000)
                self._settle(page, 2_000)
                inp = page.locator(f"{POPOVER} {LOC_INPUT}").first
                inp.wait_for(state="visible", timeout=8_000)
            except (PlaywrightTimeout, PlaywrightError):
                return None

        for term in self._location_terms(q):
            try:
                inp.click()
                inp.fill("")
                inp.type(term, delay=110)
            except (PlaywrightTimeout, PlaywrightError):
                continue
            # The suggestion service is slow and occasionally answers only on a later keystroke, so give
            # it a generous debounce rather than concluding a valid term does not geocode.
            page.wait_for_timeout(5_000)
            label = self._pick_location_option(page, state)
            if label:
                self._settle(page, 2_000)
                return label
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
            except PlaywrightError:
                pass
        return None

    def _pick_location_option(self, page: Page, state: str | None) -> str | None:
        try:
            opts = page.locator(f"{OPTION}:visible")
            n = opts.count()
        except PlaywrightError:
            return None
        for i in range(min(n, 12)):
            try:
                text = (opts.nth(i).inner_text() or "").strip()
            except PlaywrightError:
                continue
            low = text.lower()
            if not text or "current location" in low or low.startswith("no results"):
                continue
            if state and not re.search(rf",\s*{state}\b", text, re.I):
                continue  # wrong state — "Texas" offers Texas County, MO; "Tarrant" offers Tarrant, AL
            try:
                opts.nth(i).click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            return text
        return None

    def _location_terms(self, q: PortalQuery) -> list[str]:
        """ZIP, then "City, ST", then the city, then the bare state CODE.

        The ZIP is tried first because it is the tightest scope, but it genuinely fails for PO-box-only
        ZIPs (the sheet's 76152 — verified). The state CODE is a last resort and is safe to TYPE ("TX"
        suggests Texas cities) but it does not prove a geography: `_location_granularity` grades what was
        actually committed, and a state-only commit cannot license an OON.
        """
        state = self._state_code(q)
        terms: list[str] = []
        for candidate in (
            q.zip_code,
            f"{q.city}, {state}" if q.city and state else None,
            q.city,
            state,
        ):
            if candidate and candidate not in terms:
                terms.append(candidate)
        return terms

    def _state_code(self, q: PortalQuery) -> str | None:
        """Two-letter code from the query state. Roster states carry a market suffix ("TX-Dallas")."""
        raw = (q.state or "").strip().upper()
        code = raw.split("-")[0].strip()[:2]
        return code if len(code) == 2 and code.isalpha() else None

    def _pick_network(self, page: Page, root: str, q: PortalQuery) -> NetworkPin:
        """Choose the Plan/Program option for the member's plan, or decline.

        The list is 90 Molina networks nationwide, "<ST> - <product>" (verified: 90 options on
        2026-07-28), and is NOT filtered by the location just chosen — so it must be filtered by state.
        """
        sel = f"{root} {NET_SELECT}" if root else NET_SELECT
        try:
            box = page.locator(sel).first
            box.wait_for(state="visible", timeout=10_000)
            box.click(timeout=10_000)
        except (PlaywrightTimeout, PlaywrightError):
            return NetworkPin(None, "the Plan/Program select never opened")
        self._settle(page, 2_500)

        try:
            opts = page.locator(OPTION)
            labels = [(opts.nth(i).inner_text() or "").strip() for i in range(opts.count())]
        except PlaywrightError:
            return NetworkPin(None, "the Plan/Program options could not be read")
        if not labels:
            return NetworkPin(None, "the Plan/Program list was empty")

        state = self._state_code(q)
        pool = [
            (i, t) for i, t in enumerate(labels)
            if t and t.lower() != _PLACEHOLDER_OPTION
            and (not state or re.match(rf"^\s*{state}\s*-", t, re.I))
        ]
        if not pool:
            return NetworkPin(None, f"none of the {len(labels)} networks is in state {state!r}")

        pin, idx = self._best_network(pool, q.plan)
        if idx is None or not pin.label:
            return pin
        try:
            opts.nth(idx).click(timeout=10_000)
        except (PlaywrightTimeout, PlaywrightError):
            return NetworkPin(None, f"network {pin.label!r} was identified but not clickable")
        self._settle(page, 2_500)
        return pin

    def _best_network(self, pool: list[tuple[int, str]], plan: str | None) -> tuple[NetworkPin, int | None]:
        """Whole-token product-name identification first, then the shared token matcher.

        Tier 0 exists because Molina's Medicaid networks carry no CMS contract id, so
        `plan_match.match_plan` can never reach its decisive identifier tier for this payer — and its
        token tier deadlocks on the pair that matters most: "Texas STAR" and "Texas STAR+PLUS" share
        exactly the same distinctive tokens, so it correctly refuses to choose. What separates them is
        that the portal's whole product name appears as a WHOLE-TOKEN RUN inside the plan string.
        Taking the LONGEST unique run keeps STAR+PLUS winning whenever the plan really says STAR+PLUS.

        Token runs, not `in` on a normalised string: "texasstar" IS a substring of "texasstarplus", so
        substring containment cannot distinguish the two networks at all.

        DOCTRINE NOTE. `PlanMatch.confirms_network` (an identifier match) is the normal bar for licensing
        an OON. No Molina Medicaid network carries an identifier in either the 271 plan string or the
        portal label, so that bar is unreachable here and a strict reading makes OON impossible for this
        whole line of business. Tier 0 is treated as equivalent strength ONLY when the portal's full
        product name is identified uniquely, with no runner-up, out of the in-state pool — and the OON
        path additionally requires the portal to restate that same network label on the answer page
        itself. Tier 2 (word overlap) is never strong; `plan_match`'s own basis string says so.
        """
        if not plan:
            return NetworkPin(None, "no plan string was supplied, so no network could be pinned"), None
        want = _tokens(plan)
        hits: list[tuple[int, int, str]] = []
        for i, label in pool:
            product = _tokens(re.sub(r"^\s*[A-Za-z]{2}\s*-\s*", "", label))
            if product and _contains_tokens(want, product):
                hits.append((len(product), i, label))
        if hits:
            hits.sort(key=lambda h: -h[0])
            if len(hits) > 1 and hits[0][0] == hits[1][0]:
                return NetworkPin(None, (
                    f"two networks matched the plan string equally well ({hits[0][2]!r}, {hits[1][2]!r})"
                    f" — refusing to guess"
                )), None
            _, idx, label = hits[0]
            return NetworkPin(label, f"portal product name {label!r} appears as a whole-token run in the "
                                     f"plan string, uniquely among the {len(pool)} in-state networks",
                              strong=True), idx

        # Fall back to the shared matcher so this driver and the others agree what a plan match is.
        m = match_plan(plan, [t for _, t in pool])
        if m is None:
            return NetworkPin(None, (
                f"plan_match declined among the {len(pool)} in-state networks — the plan string carries "
                f"no identifier and no unique distinctive-token winner"
            )), None
        return (NetworkPin(pool[m.index][1], f"plan_match {m.confidence}: {m.basis}",
                           strong=m.confirms_network), pool[m.index][0])

    # --- searching ---------------------------------------------------------------------------------

    def _search_terms(self, q: PortalQuery) -> list[tuple[str, str]]:
        """NPI, then surname, then full name. The box accepts all three ("Search for Care by Specialty,
        Name, NPI or Keyword") and a surname finds more than "First Last" does."""
        terms: list[tuple[str, str]] = []
        if q.npi:
            terms.append((q.npi, "NPI"))
        if q.provider_last_name:
            terms.append((q.provider_last_name, "surname"))
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        if full and full != (q.provider_last_name or ""):
            terms.append((full, "full name"))
        return terms

    def _search(self, page: Page, term: str) -> str:
        """Issue one search. Returns "cards" | "none" | "error" | "challenge"."""
        before = page.url
        try:
            box = page.locator(SEARCH).first
            box.wait_for(state="visible", timeout=15_000)
            box.click()
            box.fill("")
            box.type(term, delay=70)
            page.wait_for_timeout(2_500)  # debounce; do NOT Escape the category panel first (trap 8)
            box.press("Enter")
        except (PlaywrightTimeout, PlaywrightError):
            return "error"

        state = self._wait_results(page)
        if state != "timeout":
            return state
        if self._challenge(page):
            return "challenge"
        # Re-issue the portal's OWN results route, carrying the pinned network_id and geo_location
        # forward untouched. This is the same navigation Enter performs, not a private API. The term is
        # percent-encoded: a full name contains a space, which otherwise yields a broken URL.
        qs = before.split("?", 1)[1] if "?" in before else ""
        try:
            page.goto(f"{ENTRY}search/name/{quote(term, safe='')}?{qs}",
                      wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError):
            return "error"
        state = self._wait_results(page)
        return "challenge" if state == "timeout" and self._challenge(page) else state

    def _wait_results(self, page: Page, timeout_ms: int = 45_000) -> str:
        """Poll until the results page commits to an answer. Polling, not sleeping: the empty state can
        take 30s+ while the portal walks its radius ladder (10 → 25 → … → 3500 miles)."""
        waited = 0
        while waited < timeout_ms:
            page.wait_for_timeout(1_000)
            waited += 1_000
            try:
                if page.locator(NONE_HDR).count():
                    return "none"
                if page.locator(CARDS).count():
                    # Cards render their name/address before the "NPI: …" line (a grey skeleton bar).
                    # Reading too early makes an in-network provider look unmatched.
                    for _ in range(12):
                        if _NPI_LINE_RE.search(page.locator(CARDS).first.inner_text() or ""):
                            break
                        page.wait_for_timeout(1_000)
                    return "cards"
            except PlaywrightError:
                continue
        return "timeout"

    def _read_results(self, page: Page) -> ResultSet:
        """Parse the results page.

        DEFECT CLASS D: the previous shape wrapped the whole card loop in one
        `except PlaywrightError: pass`, so a read that failed on card 3 of 12 returned three cards and
        the caller counted them as the complete set — a false OON generator. Each card is now read
        independently and any failure clears `read_complete`, which `_oon_blockers` treats as fatal.
        """
        rs = ResultSet(url=page.url)
        try:
            loc = page.locator(CARDS)
            rs.dom_count = loc.count()
        except PlaywrightError:
            rs.read_complete = False
            return rs
        if rs.dom_count > _MAX_CARDS:
            rs.read_complete = False
        for i in range(min(rs.dom_count, _MAX_CARDS)):
            try:
                text = loc.nth(i).inner_text() or ""
            except PlaywrightError:
                rs.read_complete = False
                continue
            rs.cards.append(self._parse_card(text))

        m = _TOTAL_RE.search(self._text(page, RESULTS_HDR))
        if m:
            rs.total = int(m.group(1).replace(",", ""))
        rs.none_sub = self._text(page, NONE_SUB)
        rs.results_for = self._text(page, RESULTS_FOR) or self._text(page, NONE_HDR)
        return rs

    def _parse_card(self, text: str) -> Card:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        headline = lines[0][:120] if lines else ""
        m = _NPI_LINE_RE.search(text or "")
        # The name is everything before the credential comma: "Clinton W Twaddell, MD" -> 3 tokens.
        return Card(text=text or "", headline=headline, npi=m.group(1) if m else None,
                    tokens=_tokens(headline.split(",")[0]))

    def _is_ours(self, card: Card, q: PortalQuery) -> bool:
        r"""Identity comes from the QUERY's NPI matched against the CARD's own `NPI:` line.

        DEFECT CLASS B. Two things are deliberately absent:
          * no page-body test. The first cut used `q.npi in page.inner_text("body")` and produced a false
            IN on a live negative control, because the empty state renders "No results for 1346866332" —
            the page contains the NPI precisely because the portal could NOT find it. A search-term echo
            is not evidence.
          * no name-only match, and no "the portal said Matched on:PROVIDER IDENTIFIER so cards[0] is
            ours" shortcut. That shortcut named an arbitrary first row as our provider on evidence that
            was not about identity at all; "Garcia" returns 11 different Garcias in this network.
        Substring matching is avoided too: the NPI is captured from `NPI: (\d{10})` so a 10-digit run
        inside a phone number or a longer identifier can never match.
        """
        return bool(q.npi) and card.npi is not None and card.npi == q.npi

    def _oon_blockers(
        self, q: PortalQuery, rs: ResultSet, term: str, pin: NetworkPin,
        header_plan: str, geo: str, geo_why: str, loc_label: str,
    ) -> list[str]:
        """Everything that stops a populated, non-matching name result set from being an OON.

        Empty means the absence is real evidence. Each entry is phrased to drop straight into the note,
        so a reader of the capture sees exactly which guard fired rather than a bare UNKNOWN.
        """
        shown = len(rs.providers)
        blockers: list[str] = []

        if not pin.strong:
            blockers.append(
                f"the network was pinned only weakly ({pin.basis}), and absence from a network we did "
                f"not identify decisively is not evidence"
            )
        if not plan_attested(self._page_plan_text(rs), header_plan):
            # DEFECT CLASS E: `bool(banner)` proves only that something rendered. The answer page must
            # name the network we pinned, and `plan_attested` checks for a negation in front of it first.
            blockers.append(
                f"the results page does not restate the pinned network {header_plan!r}, so we cannot show "
                f"this result set came from that network"
            )
        if geo not in ("zip", "metro", "city"):
            blockers.append(
                f"the search location {loc_label!r} is not demonstrably the clinic's ({geo_why}), and "
                f"this portal scopes results by distance from it"
            )
        # DEFECT CLASS C, live-proven: "Garcia" returned `11 Providers:` with 10 cards at radius=10 and
        # no expansion. Page 1 of a radius-limited list cannot support absence, and paging through a
        # payer's network is exactly the systematic downloading Molina's terms forbid.
        if not self._radius_expanded(rs.url):
            blockers.append(
                "the portal had not widened past its default radius (no radiusExpanded on the results "
                "URL), so this set covers only providers near the clinic, not the whole network"
            )
        if rs.total is None:
            blockers.append(
                "the portal stated no result total, so the set cannot be shown to be complete rather "
                "than the first page"
            )
        elif rs.total > shown:
            blockers.append(f"the portal reported {rs.total} results but only {shown} were read")
        if not rs.read_complete:
            blockers.append(
                f"the result list could not be read completely ({shown} of {rs.dom_count} card elements)"
            )
        chrome = len(rs.cards) - shown
        if chrome:
            # DEFECT CLASS G: a row with no name or no NPI is UI chrome or a half-hydrated skeleton. It
            # must not be counted as a provider we checked and ruled out.
            blockers.append(
                f"{chrome} of {len(rs.cards)} rows had no readable name+NPI, so they were not providers "
                f"we could rule out"
            )
        if not self._filtered_to_provider(rs, term, q):
            blockers.append(
                f"the result set was not shown to be filtered to {term!r} (the portal echoed "
                f"{rs.results_for!r}), so it may be a keyword or specialty fallback rather than a name "
                f"search"
            )
        blockers.extend(self._ambiguous(rs.providers, q))
        return blockers

    def _filtered_to_provider(self, rs: ResultSet, term: str, q: PortalQuery) -> bool:
        """Proof the list is a name search for OUR provider, not an unfiltered or keyword result set.

        Either the portal's own echo of the term, or every provider row carrying our surname as a WHOLE
        TOKEN. The surname must be a whole token: Cigna's typeahead answered "Orem" with "Shoaf, Noremi
        D", and "noremi" contains "orem".
        """
        if rs.results_for and _norm(term) in _norm(rs.results_for):
            return True
        surname = _norm(q.provider_last_name)
        if not surname or not rs.providers:
            return False
        return all(surname in [_norm(t) for t in c.tokens] for c in rs.providers)

    def _ambiguous(self, cards: list[Card], q: PortalQuery) -> list[str]:
        """Rows that share our surname but cannot be told apart from our provider.

        DEFECT CLASS B, the other half: an initial-only card must decide NOTHING. Calling it absent is a
        false OON while our provider may be the very row we skipped over.
        """
        surname = _norm(q.provider_last_name)
        first = _norm(q.provider_first_name)
        out: list[str] = []
        for c in cards:
            if not surname or surname not in [_norm(t) for t in c.tokens]:
                continue
            if c.npi == q.npi:
                continue
            given = c.tokens[0] if c.tokens else ""
            if first and len(given) == 1 and first.startswith(given):
                out.append(
                    f"the row headlined {c.headline!r} gives only an initial and could be our provider, "
                    f"so absence cannot be concluded"
                )
        return out

    # --- helpers -----------------------------------------------------------------------------------

    def _radius_expanded(self, url: str) -> bool:
        """Whether the portal itself widened to its maximum radius for this query."""
        return "radiusexpanded=true" in (url or "").lower()

    def _page_plan_text(self, rs: ResultSet) -> str:
        """Where the answer page names the network it searched: on every card, and on the empty state's
        subheader. Read from what we parsed, not from the whole body — the header's own Plan/Program
        chip would otherwise "confirm" any page at all."""
        return "\n".join([c.text for c in rs.cards] + [rs.none_sub])

    def _attestation(self, page: Page, card: Card | None, plan_label: str, attested: bool) -> str:
        """Quote the portal's own restatement of the network it searched — the strongest evidence in the
        capture, because it is the portal's claim rather than our inference.

        The quoted string must NAME the pinned plan. A bare `For … in 12345` match would happily quote
        the empty state of a different network and present it as this plan's attestation (defect F).
        """
        haystack = (card.text if card else "") or self._body(page)
        if attested or plan_attested(haystack, plan_label):
            m = _plan_phrase_re(plan_label).search(haystack)
            if m:
                return f"Portal's own attestation on the row: {m.group(0).strip()!r}."
        m = re.search(rf"\bFor\s+{re.escape(plan_label)}\s+in\s+[^\n]{{0,60}}", haystack, re.I)
        if m:
            return f"Portal's own attestation on the page: {m.group(0).strip()!r}."
        return (
            f"Portal header showed Plan/Program {plan_label!r}, but the row itself did not restate the "
            f"network, so that header is the only plan evidence here."
        )

    def _challenge(self, page: Page) -> str | None:
        """The visible-text phrase of an interactive challenge, if one is being shown. Never solved."""
        text = self._body(page).lower()
        return next((p for p in _CHALLENGE_PHRASES if p in text), None)

    def _wait_shell(self, page: Page, timeout_ms: int = 75_000) -> bool:
        """Poll for the guest modal or the header network select. See trap 1 — a fixed settle is wrong."""
        waited = 0
        while waited < timeout_ms:
            try:
                if page.locator(NET_SELECT).count() or page.get_by_role(
                    "button", name="Continue", exact=True
                ).count():
                    return True
            except PlaywrightError:
                pass
            page.wait_for_timeout(2_500)
            waited += 2_500
        return False

    def _popover_open(self, page: Page) -> bool:
        try:
            return page.locator(f"{POPOVER}:visible").count() > 0
        except PlaywrightError:
            return False

    def _location_granularity(self, committed: str, q: PortalQuery) -> tuple[str, str]:
        """Grade how well the committed location proves we searched the clinic's geography.

        Returns one of "zip" | "metro" | "city" | "state" | "none", plus a human reason. Only the first
        three may license an OON. "state" is deliberately NOT enough: the clinic ZIP 76152 does not
        geocode here, so `_location_terms` can fall back to the bare state code and land on any Texas
        city — and absence 300 miles from the clinic is not absence near the clinic.

        "metro" is an exact ZIP3 match (76152 vs 76132 -> "761"): a USPS sectional centre, i.e. the same
        metro area. That is a documented geographic fact, not a guess.
        """
        if not committed:
            return "none", "no location was committed"
        state = self._state_code(q)
        if state and not re.search(rf",\s*{state}\b", committed, re.I):
            return "none", f"the committed location is not in {state}"
        if q.zip_code and re.search(rf"\b{re.escape(q.zip_code)}\b", committed):
            return "zip", f"committed location carries the clinic ZIP {q.zip_code}"
        if q.zip_code and len(q.zip_code) >= 3:
            m = re.search(r"\b(\d{5})\b", committed)
            if m and m.group(1)[:3] == q.zip_code[:3]:
                return "metro", (f"committed ZIP {m.group(1)} shares the clinic ZIP's sectional centre "
                                 f"{q.zip_code[:3]}xx")
        if q.city and _contains_tokens(_tokens(committed), _tokens(q.city)):
            return "city", f"committed location names the clinic city {q.city!r}"
        return "state", f"only the state matched — committed {committed!r}, clinic ZIP {q.zip_code}"

    def _settle(self, page: Page, pause_ms: int = 2_500) -> None:
        """Best-effort wait. Never raises: this SPA streams analytics and may never reach networkidle."""
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass

    def _header_text(self, page: Page, sel: str) -> str:
        """Read the PERSISTENT header copy of a shared-data-cy control.

        Index 0 is the header: verified live — with the modal open the location input resolves twice and
        `hasAttribute('readonly')` is [True, False], the readonly header copy first.
        """
        try:
            loc = page.locator(sel)
            return (loc.first.inner_text() or "").strip() if loc.count() else ""
        except PlaywrightError:
            return ""

    def _header_value(self, page: Page, sel: str) -> str:
        try:
            loc = page.locator(sel)
            return (loc.first.input_value() or "").strip() if loc.count() else ""
        except PlaywrightError:
            return ""

    def _text(self, page: Page, sel: str) -> str:
        try:
            loc = page.locator(sel)
            return (loc.first.inner_text() or "").strip() if loc.count() else ""
        except PlaywrightError:
            return ""

    def _body(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
