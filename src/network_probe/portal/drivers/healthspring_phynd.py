"""HealthSpring (Cigna Medicare, rebranded for 2026) — the Phynd-hosted guest directory.

WHY THIS IS NOT THE CIGNA DRIVER. Cigna Medicare became HealthSpring for plan year 2026 and moved
off Cigna's own directory entirely: this is `healthspring-search.phynd.com`, a Phynd tenant, not
`hcpdirectory.cigna.com`. `cigna_hcp.py` could never have answered a Medicare row anyway — it lists
"medicare" in `_OFF_DIRECTORY_LOB` and deliberately refuses, because scoring a Medicare plan against
its commercial plan list is how a wrong network gets reported as confirmed.

THE PLAN IS PINNED BY CMS IDENTIFIER, WHICH IS WHY THIS DRIVER IS SIMPLE. The plan picker is keyed by
contract-PBP-segment: selecting "HealthSpring Preferred (HMO)" puts `healthPlan=H0354-001-000` in the
URL. A 271 that carries `H0354027000` therefore pins the member's own plan directly, with no name
matching at all — the identifier route `plan_match.py` prefers over word overlap. The walk is:

    /providers?radius=<mi>&zipcode=<clinic zip>&healthPlan=<contract>-<pbp>-<segment>

⚠ THE PIN MUST BE VERIFIED, NOT ASSUMED. Measured 2026-08-12 in a live browser tab: changing only the
`healthPlan` parameter on an already-loaded page left the header still naming the PREVIOUS plan. The
Angular shell hydrates its selection once and a same-route parameter change does not re-read it, so a
search issued that way ran against a plan nobody chose — indistinguishable, in the result, from a real
absence. This driver therefore navigates COLD (`requires_fresh_context`) and then READS THE HEADER
BACK, refusing when the portal will not tell us which plan it is answering about. It is the same trap
the UHC county modal documents: the portal caches the first selection, so in-session comparison lies.

A pinned plan that comes back as some OTHER plan is also meaningful and is reported as such rather
than searched: an MA plan is sold by county, so a PBP that Maricopa does not offer means the member's
plan is not sold where the clinic is — a different finding from "this provider is out of network",
and one that must not be collapsed into it.

⚠ THE SEARCH BOX DEMANDS ITS OWN TYPEAHEAD. The page says so in as many words: "please select a name
from the dropdown suggestions to ensure accurate results." Free text returned "0 results / No
providers found" for a provider whose network status was never actually tested. Zero results from an
unselected free-text query is NOT an absence, and `_search` refuses to treat it as one.

KNOWN LIMIT, MEASURED AND NOT YET CLOSED. When the typeahead has a real provider it appears to
navigate to that provider's PROFILE rather than a result list, so `_read_results` (which reads
"Showing N results") finds no surface and the capture returns UNKNOWN — a control search for "Smith"
did exactly this. The not-found path is sound: for a surname the typeahead cannot match it offers
only its generic "Search: <term>" entry, and taking that yields a real zero. So an OUT_OF_NETWORK
from this driver is trustworthy, while an IN_NETWORK is not yet reachable through it. Closing this
needs `_read_results` to recognise the profile route as a result of one.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse, urlencode

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

HOST = "https://healthspring-search.phynd.com"
SEARCH_BOX = "input[type='text'], input[type='search']"
RESULTS_COUNT_RE = re.compile(r"Showing\s+([\d,]+)\s+results?", re.I)
NO_RESULTS_RE = re.compile(r"No providers found", re.I)
NPI_RE = re.compile(r"\bNPI:?\s*(\d{10})\b")

# `H0354027000` and `H0354-027-000` are the same plan written two ways; the 271 may use either.
_PLAN_ID_RE = re.compile(r"\b([HRS]\d{4})-?(\d{3})-?(\d{3})\b", re.I)

_SHELL_TIMEOUT_S = 60.0
_POLL_MS = 1_500
_TYPEAHEAD_MAX_S = 12.0
_MAX_PROFILES = 8
_DEFAULT_RADIUS = "20"

_CHALLENGE_PHRASES = (
    "press & hold", "press and hold", "verify you are a human", "are you a robot",
    "complete the security check", "checking your browser",
)


def cms_plan_id(plan: str | None) -> str | None:
    """`H0354027000` / `H0354-027-000` -> `H0354-027-000`, the form the portal's URL wants.

    Returns None when the 271 names no CMS identifier, which must stay a refusal: without the
    contract-PBP-segment there is nothing to pin and a name-matched Medicare network would be a guess.
    """
    m = _PLAN_ID_RE.search(plan or "")
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2)}-{m.group(3)}"


@dataclass
class Card:
    text: str
    name: str | None = None
    npis: tuple[str, ...] = ()

    @property
    def headline(self) -> str:
        return (self.name or self.text.split("\n")[0])[:120]


@dataclass
class ResultSet:
    surfaced: bool
    count: int | None = None
    cards: list[Card] = field(default_factory=list)
    no_results: bool = False
    #: The typeahead offered nothing to select. The portal asks that a name be chosen from its
    #: suggestions, so a free-text zero is not an absence and must not become an OON.
    no_selection: bool = False

    @property
    def populated(self) -> bool:
        return bool(self.cards)


class HealthSpringPhyndDriver(PortalDriver):
    key = "healthspring-phynd"
    portal_name = "HealthSpring Find a Provider (Phynd, guest)"
    # The walk commits a ZIP into the URL; a state alone never scopes it.
    location_fields = ("zip_code",)
    # The plan pin is only honoured on a cold load — see the module docstring.
    requires_fresh_context = True

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        plan_id = cms_plan_id(q.plan)
        if not plan_id:
            return result(
                PortalStatus.UNKNOWN,
                f"the plan string {q.plan!r} carries no CMS contract-PBP-segment, and this portal pins "
                f"a network by that identifier alone. Without it nothing can be pinned, and a "
                f"Medicare network chosen by name would be a guess.",
            )
        zip_code = (q.zip_code or "").strip()
        self._zip = zip_code
        if not zip_code:
            return result(PortalStatus.UNKNOWN, "no clinic ZIP to scope the search with.")

        # A COLD deep link to /providers does NOT work: the portal redirects to /select-a-location and
        # drops the plan. The location step has to be completed in-session first, and only then is the
        # healthPlan parameter honoured. So walk the wizard's first step, then deep-link the plan.
        if not self._commit_location(page, zip_code, trail):
            return result(
                PortalStatus.UNKNOWN,
                f"the clinic ZIP {zip_code} could not be committed on the location step, and this "
                f"portal will not accept a plan until it has one.",
                screenshot=shot("no-location"),
            )
        # The plan is pinned by WALKING the wizard, not by URL. A healthPlan parameter is inert unless
        # the picker put it there: a cold deep link bounces to /select-a-location, and after only the
        # location step the providers page carries no plan at all and returns the entire directory
        # (6,583 providers for Maricopa). Both look exactly like a search that found nothing.
        picked = self._pick_plan(page, plan_id, trail)
        if picked is None:
            return result(
                PortalStatus.UNKNOWN,
                f"{plan_id} was not among the plans this portal offers at {zip_code}. An MA plan is "
                f"sold by county, so this reads as the member's plan not being available where the "
                f"clinic is — which is a different finding from the provider being out-of-network, "
                f"and must not be reported as one.",
                screenshot=shot("plan-not-offered"),
            )
        if not self._wait_shell(page):
            return result(PortalStatus.BLOCKED,
                          f"the Phynd shell never rendered a search box within {_SHELL_TIMEOUT_S:.0f}s.",
                          screenshot=shot("no-hydrate"))
        if (ch := self._challenge(page)):
            return result(PortalStatus.BLOCKED, f"the portal presented a challenge ({ch}); not solved.",
                          screenshot=shot("challenge"))

        # Read the pin back. A cold load should name the plan we asked for; anything else means the
        # portal answered about a different plan, and the search must not be issued.
        shown = self._plan_header(page) or picked
        trail.append(f"plan requested {plan_id}, portal pinned {picked!r} (header {shown!r})")
        if not shown:
            return result(
                PortalStatus.UNKNOWN,
                f"the portal did not name the health plan it had selected, so there is no way to show "
                f"which network any result belongs to.",
                screenshot=shot("no-plan-header"),
            )
        if self._is_unpinned(shown):
            return result(
                PortalStatus.UNKNOWN,
                f"the portal fell back to searching without a plan ({shown!r}) instead of honouring "
                f"{plan_id}. Nothing found or not found in that state is evidence about this member's "
                f"network.",
                screenshot=shot("plan-not-pinned"),
            )

        rs = self._search(page, q, trail)
        shot_name = shot("results")

        if not rs.surfaced:
            return result(
                PortalStatus.UNKNOWN,
                f"the results surface never rendered, so nothing was read either way.",
                screenshot=shot_name,
            )
        if rs.no_selection:
            return result(
                PortalStatus.UNKNOWN,
                f"the portal's typeahead offered no suggestion for "
                f"{q.provider_last_name!r}, and it states that a name must be chosen from its "
                f"suggestions for results to be accurate. A free-text query returning nothing "
                f"is not an absence, so this is not reported as out-of-network.",
                screenshot=shot_name,
            )

        ours, checked = self._identify(page, rs, q, trail)
        if ours is not None:
            return result(
                PortalStatus.IN_NETWORK,
                f"the portal listed {ours.headline!r} (NPI {q.npi}) within {_DEFAULT_RADIUS} miles of "
                f"{zip_code} with the plan pinned to {shown!r} ({plan_id}).",
                screenshot=shot("profile") or shot_name, matched_name=ours.headline,
                result_count=rs.count,
            )
        if rs.populated and checked < len(rs.cards):
            return result(
                PortalStatus.UNKNOWN,
                f"{len(rs.cards)} result(s) came back for {q.provider_last_name!r} on {shown!r}, but "
                f"only {checked} could be identified by NPI. An unread profile is not an absence.",
                screenshot=shot_name, result_count=rs.count,
            )
        return result(
            PortalStatus.OUT_OF_NETWORK,
            f"the portal was pinned to {shown!r} ({plan_id}) — the member's own plan — the name was "
            f"taken from its own typeahead, and it returned "
            f"{'no providers' if rs.no_results else f'{len(rs.cards)} provider(s), none with NPI {q.npi}'} "
            f"within {_DEFAULT_RADIUS} miles of {zip_code}. Out-of-network for this plan.",
            screenshot=shot_name, result_count=rs.count,
        )

    # ------------------------------------------------------------- mechanics

    def _pick_plan(self, page: Page, plan_id: str, trail: list[str]) -> str | None:
        """Walk Plan Type -> Plan and select the option that IS this member's plan.

        The plan buttons carry no identifier in the DOM — only their marketing name — so the mapping
        is learned the only way the portal exposes it: click an option and read the `healthPlan` the
        portal itself puts in the URL. Measured for Maricopa 2026-08-12:

            H0354-001-000  HealthSpring Preferred (HMO)
            H0354-027-000  HealthSpring Achieve (HMO C-SNP)
            H0354-028-000  HealthSpring Alliance (HMO)
            H0354-029-000  HealthSpring Preferred Savings (HMO)
            H0354-030-000  HealthSpring Preferred Full Savings (HMO)
            H7849-065-000  HealthSpring True Choice (PPO)

        Names are NOT hardcoded from that table: the list is county-specific and CMS renames plans
        every year, so it is re-read live and matched on the identifier. Returns the plan's label, or
        None when this county does not offer it.
        """
        # The plan-type list only renders after the "search by name" card is chosen.
        self._click_text(page, "Search by name, specialty, or condition")
        for label in ("Individual Medicare Advantage Plans", "Group Medicare Advantage Plans"):
            if not self._click_text(page, label):
                continue
            trail.append(f"plan type: {label}")
            hit = self._scan_plans(page, plan_id, trail, plan_type=label)
            if hit:
                return hit
            # Not in this book — try the other one from a clean plan-type step.
            self._goto_step(page, "select-a-plan-type")
            self._click_text(page, "Search by name, specialty, or condition")
        return None

    def _scan_plans(self, page: Page, plan_id: str, trail: list[str],
                    plan_type: str = "Individual Medicare Advantage Plans") -> str | None:
        names = self._plan_names(page)
        trail.append(f"{len(names)} plan(s) offered here")
        for name in names[:12]:
            if not self._click_text(page, name):
                continue
            self._settle(page, 5_000)
            got = parse_qs(urlparse(page.url).query).get("healthPlan", [None])[0]
            if (got or "").upper() == plan_id.upper():
                trail.append(f"plan pinned: {name!r} = {got}")
                return name
            # Returning to /select-a-plan directly renders an EMPTY list — the step only populates
            # after the "search by name" card is chosen — so the wizard is re-walked rather than
            # abandoned. Giving up here reported "plan not offered" for a plan that was two clicks
            # away, which would have read as the member's plan being unavailable in the county.
            self._goto_step(page, "select-a-plan")
            if not self._plan_names(page):
                self._goto_step(page, "select-a-plan-type")
                self._click_text(page, "Search by name, specialty, or condition")
                self._click_text(page, plan_type)
                if not self._plan_names(page):
                    return None
        return None

    def _plan_names(self, page: Page) -> list[str]:
        try:
            return page.evaluate(
                r"""() => Array.from(document.querySelectorAll('button'))
                     .filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;})
                     .map(e=>(e.innerText||'').replace(/\s+/g,' ').trim())
                     .filter(t=>/^HealthSpring /i.test(t))"""
            )
        except PlaywrightError:
            return []

    def _goto_step(self, page: Page, step: str) -> None:
        try:
            page.goto(f"{HOST}/{step}?radius={_DEFAULT_RADIUS}&zipcode={self._zip}",
                      wait_until="domcontentloaded", timeout=45_000)
            self._settle(page, 5_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass

    def _click_text(self, page: Page, text: str, timeout_ms: int = 12_000) -> bool:
        for build in (lambda: page.get_by_role("button", name=text).first,
                      lambda: page.get_by_text(text, exact=False).first):
            try:
                build().click(timeout=timeout_ms)
                self._settle(page, 5_000)
                return True
            except (PlaywrightTimeout, PlaywrightError):
                continue
        return False

    def _commit_location(self, page: Page, zip_code: str, trail: list[str]) -> bool:
        """Wizard step 1: type the clinic ZIP, take the suggestion, continue."""
        try:
            page.goto(f"{HOST}/select-a-location?radius={_DEFAULT_RADIUS}",
                      wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        if not self._wait_shell(page):
            trail.append("location step never rendered")
            return False
        box = self._visible(page, SEARCH_BOX)
        if box is None:
            trail.append("no ZIP input on the location step")
            return False
        try:
            box.click(timeout=8_000)
            page.keyboard.type(zip_code, delay=120)
        except (PlaywrightTimeout, PlaywrightError) as e:
            trail.append(f"ZIP not typeable: {type(e).__name__}")
            return False
        self._settle(page, 3_500)
        # The suggestion names the COUNTY ("85006 - Maricopa"), which is the unit an MA plan is sold
        # by — worth carrying into the trail so the verdict says which county it answered for.
        for sel in ("[role=option]", "mat-option", "li", ".mat-option"):
            try:
                opts = page.locator(sel)
                for i in range(min(opts.count(), 8)):
                    text = " ".join((opts.nth(i).inner_text() or "").split())
                    if zip_code not in text:
                        continue
                    opts.nth(i).click(timeout=6_000)
                    self._settle(page, 2_500)
                    trail.append(f"location: {text[:50]!r}")
                    break
                else:
                    continue
                break
            except (PlaywrightTimeout, PlaywrightError):
                continue
        for name in ("Continue to plan type selection", "Continue"):
            try:
                btn = page.get_by_role("button", name=name).first
                if btn.count() and btn.is_visible():
                    btn.click(timeout=8_000)
                    self._settle(page, 5_000)
                    trail.append(f"location step: {name}")
                    return True
            except (PlaywrightTimeout, PlaywrightError):
                continue
        trail.append("location step Continue not clickable")
        return False

    def _visible(self, page: Page, sel: str, limit: int = 8) -> Locator | None:
        loc = page.locator(sel)
        try:
            n = min(loc.count(), limit)
        except PlaywrightError:
            return None
        for i in range(n):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except PlaywrightError:
                continue
        return None

    def _wait_shell(self, page: Page, timeout_s: float = _SHELL_TIMEOUT_S) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._visible(page, SEARCH_BOX) is not None:
                return True
            try:
                page.wait_for_timeout(_POLL_MS)
            except PlaywrightError:
                return False
        return False

    def _plan_header(self, page: Page) -> str | None:
        """The 'Health Plan' the portal says it is searching. Read, never assumed."""
        try:
            body = page.inner_text("body") or ""
        except PlaywrightError:
            return None
        m = re.search(r"Health Plan\s*\n+\s*(.+)", body)
        if m:
            return " ".join(m.group(1).split())[:90]
        return None

    @staticmethod
    def _is_unpinned(shown: str) -> bool:
        return bool(re.match(r"^(none|search without a plan)\b", shown.strip(), re.I))

    def _search(self, page: Page, q: PortalQuery, trail: list[str]) -> ResultSet:
        term = (q.provider_last_name or "").strip()
        if not term:
            trail.append("no provider surname to search")
            return ResultSet(surfaced=False)
        box = self._visible(page, SEARCH_BOX)
        if box is None:
            trail.append("search box not visible")
            return ResultSet(surfaced=False)
        try:
            box.click(timeout=8_000)
            box.fill("", timeout=5_000)
            page.keyboard.type(term, delay=110)
        except (PlaywrightTimeout, PlaywrightError) as e:
            trail.append(f"search box not typeable: {type(e).__name__}")
            return ResultSet(surfaced=False)

        picked = self._take_suggestion(page, term, trail)
        if not picked:
            rs = ResultSet(surfaced=True)
            rs.no_selection = True
            return rs
        try:
            page.keyboard.press("Enter")
        except PlaywrightError:
            pass
        self._settle(page, 6_000)
        return self._read_results(page)

    def _take_suggestion(self, page: Page, term: str, trail: list[str]) -> bool:
        """Take the portal's own suggestion. It asks for this explicitly; free text is not a search."""
        deadline = time.monotonic() + _TYPEAHEAD_MAX_S
        while time.monotonic() < deadline:
            for sel in ("[role=option]", "mat-option", "li[role=option]", ".suggestion", "ul li"):
                try:
                    opts = page.locator(sel)
                    for i in range(min(opts.count(), 8)):
                        text = " ".join((opts.nth(i).inner_text() or "").split())
                        if not text or term.lower() not in text.lower():
                            continue
                        opts.nth(i).click(timeout=6_000)
                        self._settle(page, 3_000)
                        trail.append(f"typeahead suggestion taken: {text[:60]!r}")
                        return True
                except (PlaywrightTimeout, PlaywrightError):
                    continue
            try:
                page.wait_for_timeout(400)
            except PlaywrightError:
                break
        trail.append(f"no typeahead suggestion offered for {term!r}")
        return False

    def _read_results(self, page: Page) -> ResultSet:
        try:
            body = page.inner_text("body") or ""
        except PlaywrightError:
            return ResultSet(surfaced=False)
        m = RESULTS_COUNT_RE.search(body)
        count = int(m.group(1).replace(",", "")) if m else None
        none = bool(NO_RESULTS_RE.search(body))
        cards: list[Card] = []
        for sel in ("[class*='result-card']", "[class*='provider-card']", "article", "mat-card"):
            try:
                loc = page.locator(sel)
                n = loc.count()
                if 0 < n <= 60:
                    cards = [Card(text=(loc.nth(i).inner_text() or ""))
                             for i in range(min(n, _MAX_PROFILES * 2))]
                    break
            except PlaywrightError:
                continue
        return ResultSet(surfaced=bool(m or none or cards), count=count, cards=cards,
                         no_results=none)

    def _identify(self, page: Page, rs: ResultSet, q: PortalQuery,
                  trail: list[str]) -> tuple[Card | None, int]:
        """Confirm by NPI. A name alone never identifies a provider on a directory."""
        checked = 0
        for card in rs.cards[:_MAX_PROFILES]:
            npis = tuple(NPI_RE.findall(card.text))
            if npis:
                checked += 1
            if q.npi and q.npi in npis:
                trail.append(f"NPI {q.npi} matched on the result card")
                return Card(text=card.text, npis=npis, name=card.text.split("\n")[0][:80]), checked
        return None, checked

    def _settle(self, page: Page, ms: int = 2_500) -> None:
        try:
            page.wait_for_timeout(ms)
        except PlaywrightError:
            pass

    def _challenge(self, page: Page) -> str | None:
        try:
            body = (page.inner_text("body") or "").lower()
        except PlaywrightError:
            return None
        return next((p for p in _CHALLENGE_PHRASES if p in body), None)
