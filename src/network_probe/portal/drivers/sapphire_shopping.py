"""Zelis Sapphire "Shopping for Care" — the guest directory BlueCross BlueShield of South Carolina
and its Publix book both run on.

WHY THIS IS ONE DRIVER AND NOT TWO. `shoppingforcare.sapphirethreesixtyfive.com` is a single Zelis
instance. Both tenants are served the SAME 96-network catalogue (`/api/networks.json`); a tenant is
nothing but the `ci` query parameter plus an allow-list
(`/api/configuration_profile/settings/allowable_networks.json`). Measured 2026-08-12:

    ci=BCBSSC   41 of 96 networks allowable, alpha_prefix.network_enabled = true  (38 carry prefixes)
    ci=Publix    3 of 96 networks allowable, alpha_prefix.network_enabled = false

Adding a payer here is a `SapphireTenant` entry, not a new module — the same shape `healthsparq.py`
already uses. `molina.sapphirecareselect.com` is the same Zelis product on its own host and keeps its
own driver for now, because its TX-Medicaid enrolment logic is not generic; the mechanics below were
read off Molina's proven selectors and the two should be consolidated once this has demo mileage.

⚠ THE NETWORK LABEL IS TENANT-SCOPED AND THE ID IS NOT PORTABLE. Network id 10 is catalogue name
"PPO", rendered to the member as "Preferred Blue" under ci=BCBSSC and "PBB - Blue Choice PPO" under
ci=Publix. Same id, same `network_id=10` in the URL, two different networks by name. Carrying an id
between tenants therefore produces a *confidently wrong* network name, which is the one failure this
layer refuses (see portal/plan_match.py).

⚠ A NETWORK CANNOT BE IDENTIFIED BY ITS LABEL. The catalogue contains "Preferred Blue" five times,
"Network Blue" three times, "PPO" three times and "BlueCard Traditional PAR" twice. Within BCBSSC's
own 41-entry dropdown, ids 9, 25 and 29 all render the string "Preferred Blue" — as does id 10. Only
the id identifies a network, so this driver pins by id and reports the label the portal itself shows.

TRAPS, ALL MEASURED LIVE ON 2026-08-12 AND ALL OF WHICH COST A WALK BEFORE THEY WERE UNDERSTOOD:

 1. The shell needs a real hydration poll. A fixed settle is not enough: a run that only waited 9s
    landed on `?ci=BCBSSC&network_id=` with an EMPTY network id and nothing ever rendered. This is
    the same failure the Molina driver documents as its trap 2, so the same 75s poll is used.
 2. Zelis renders desktop AND mobile copies of the location and search inputs. `locator(sel).first`
    grabs the hidden mobile one, and every click/fill against it times out while the visible control
    sits there working. Always take the first VISIBLE match — `_visible()` below.
 3. The gated entry is a wizard, not a single modal. Step 1 offers guest / log in / alpha-prefix;
    an intermittent step 2 ("Optimize Your Browse Experience") asks for a location before releasing
    the header controls.
 4. A search that returns neither result cards NOR the no-results header is an INCONCLUSIVE read,
    not an absence. New Port Richey did exactly this. Reporting it as OON would invent an OON out of
    a hydration timeout, so `_read_results` distinguishes the two and only a populated-but-missing
    result set is allowed to produce OUT_OF_NETWORK.

WHAT THIS PORTAL IS UNUSUALLY GOOD AT. Every result card states the pinned network inline — `In
"Preferred Blue" Network` — so the verdict carries the portal's own attestation of the network it was
answering about, next to the provider, in the screenshot.

BLUECARD. The directory answers for out-of-state providers: with the network pinned to a South
Carolina member's "Preferred Blue", a search at the Atlanta clinic ZIP returned ten Georgia providers
and one at the Lake Worth FL clinic returned the row's own physician at the row's own street address.
That is BlueCard working as designed, and it is why a clinic ZIP outside SC is not by itself a reason
to decline.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

HOST = "https://shoppingforcare.sapphirethreesixtyfive.com"

# data-cy is Zelis' own test hook and the durable handle here. Every selector was read off a live
# page on 2026-08-12; none is guessed.
GATE_CONTINUE = "[data-cy='gated-entry-home.continue']"
GATE_ALPHA = "[data-cy='gated-entry-home.alpha']"
NET_SELECT = "mat-select[data-cy='global-network.network-select']"
# The header's location field is READONLY and a `page-title-icon` div covers its centre, so it takes
# neither fill() (not editable) nor click() (fails the hit-target check). It is a display, not an
# input. The real chain is: wizard step 2 -> its location trigger -> a genuinely editable search box.
LOC_INPUT = "input[data-cy='location-input.selector']"
LOC_TRIGGER = "[data-cy='gated-entry-network-location.location-trigger']"
LOC_SEARCH = "input[data-cy='global-location-search.input']"
LOC_CONTINUE = "[data-cy='gated-entry-network-location.continue']"
LOC_DIALOG = "[data-cy='global-location-dialog']"
# The dialog's suggestion rows are plain divs, not mat-option — the earlier walks were waiting for a
# Material listbox that this control never renders. Zelis leaves a TRAILING SPACE in the data-cy
# ("list-item-primary-text "), so match by prefix or the selector silently finds nothing.
LOC_SUGGEST = "[data-cy^='list-item-primary-text']"
LOC_SUGGEST_SUB = "[data-cy^='list-item-secondary-text']"
# Footer buttons, in order: 0 = Reset, 1 = Apply. Nothing commits until Apply is clicked — the walks
# that typed a ZIP and moved on were reading a location the portal had never accepted.
LOC_APPLY = "[data-cy='dialog-footer-button-1']"
SEARCH = "input[data-cy='autosuggest.input']"
OPTION = "mat-option"
CARDS = "[data-cy^='search-results.card-']"
RESULTS_FOR = "[data-cy='search-results-header.results-for']"
NONE_HDR = "[data-cy='search-results-header.no-results-header']"
NONE_SUB = "[data-cy='search-results-header.search-results-none-subheader']"
PROFILE_NETWORK = "[data-cy='profile-networks-accepted.network']"
# The NPI lives behind a COLLAPSED accordion at the bottom of the profile ("More About This Provider"
# -> "Identifiers"). It is not lazy-loaded and no amount of scrolling or waiting reveals it: the panel
# renders its heading with an empty body until it is clicked open. A profile read without this click
# reports "no NPI" for a provider that has one, which downgrades a real match to UNKNOWN.
PROFILE_IDENTIFIERS = "mat-expansion-panel-header:has-text('Identifiers')"
# The provider-name link inside a card: `search-results.name-link-0.desktop`. It is INDEXED and
# ships a `.mobile` twin, so a flat page-wide nth(i) misaligns with card i — always scope the lookup
# inside the card and take the visible one (trap 2 again).
CARD_LINK = "a[data-cy^='search-results.name-link']"
# The banner the portal raises when it could not honour the radius it was given. Measured: a search
# from Palm Springs FL returned a provider 17.4 miles away in Boca Raton under this notice. It means
# absence "within the radius" is not absence, and a hit may be nowhere near the clinic — so it is
# recorded on the verdict rather than silently ignored.
EXPANDED_RADIUS_RE = re.compile(r"expanded\s+radius|auto-?expanded\s+the\s+search\s+radius", re.I)

_SHELL_TIMEOUT_S = 75.0  # trap 1 — the SPA genuinely takes this long sometimes
_SHELL_POLL_MS = 1_500
_MAX_CARDS = 40
# Every candidate costs a profile navigation, so the sweep is bounded. Exceeding it does not
# produce an OON — `checked < len(cards)` downgrades the verdict to UNKNOWN instead.
_MAX_PROFILES = 8
_NPI_RE = re.compile(r"\bNPI:?\s*(\d{10})\b")

# The portal's own search query string, copied from a real Enter-driven search rather than invented.
# An earlier hand-built variant (`sort=relevancy desc`) returned neither cards nor a no-results
# header on every geography — a silently invalid query, which is exactly trap 4's failure shape.
_SEARCH_QS = {
    "locale": "en",
    "limit": "10",
    "radius": "25",
    "sort": "incentive_amount desc, relevancy desc, random",
    "sort_translation": "app_global_sort_relevancy",
    "page": "1",
}

# DEFECT CLASS A, inherited from the Molina driver: `In "X" Network` is a SUBSTRING of
# `Not in "X" Network`, and \bIn\b does not save you because the word boundary still matches the "In"
# inside "Not In". Negations are enumerated and tested FIRST, against the text that PRECEDES the
# phrase.
_NEGATION_RE = re.compile(
    r"\b(not|non-?participating|no\s+longer|excluded|terminated|inactive|ineligible"
    r"|out\s*-?\s*of\s*-?\s*network)\b",
    re.I,
)
_NEGATION_WINDOW = 48

_CHALLENGE_PHRASES = (
    "press & hold", "press and hold", "verify you are a human", "verify you are human",
    "are you a robot", "complete the security check", "checking your browser", "i'm not a robot",
)


@dataclass(frozen=True)
class SapphireTenant:
    """One Zelis tenant. Adding a payer is a config entry, not a new driver."""

    portal_key: str
    portal_name: str
    ci: str  # the `ci` query parameter — the ONLY thing that distinguishes these tenants
    #: Whether the tenant's gated entry accepts a BlueCard member-ID alpha prefix to pin the network.
    #: BCBSSC: true (alpha_prefix.network_enabled). Publix: false — it has three networks and no
    #: prefix map at all.
    alpha_prefix_enabled: bool
    #: The network the guest flow lands on when nothing pins it. Recorded so a capture can say
    #: whether it answered about a network that was CHOSEN or merely defaulted to.
    default_network_label: str


TENANTS: dict[str, SapphireTenant] = {
    "bcbssc-sapphire": SapphireTenant(
        portal_key="bcbssc-sapphire",
        portal_name="BlueCross BlueShield of South Carolina (Zelis Sapphire, guest)",
        ci="BCBSSC",
        alpha_prefix_enabled=True,
        default_network_label="Preferred Blue",
    ),
    "publix-sapphire": SapphireTenant(
        portal_key="publix-sapphire",
        portal_name="Publix / BlueCross BlueShield of South Carolina (Zelis Sapphire, guest)",
        ci="Publix",
        alpha_prefix_enabled=False,
        default_network_label="PBB - Blue Choice PPO",
    ),
}


@dataclass
class Card:
    """One result card, as the portal rendered it."""

    text: str
    npis: tuple[str, ...] = ()

    #: The provider name as the portal titles it, when read from a profile page.
    name: str | None = None

    @property
    def headline(self) -> str:
        if self.name:
            return self.name[:120]
        return self.text.split("\n")[0][:120] if self.text else ""


@dataclass
class ResultSet:
    """What the results surface said. `surfaced` separates 'the portal answered' from 'it never
    rendered' — trap 4, and the difference between a sound OON and an invented one."""

    surfaced: bool
    cards: list[Card] = field(default_factory=list)
    none_header: str | None = None
    results_for: str | None = None
    #: The portal said it could not fill the page within the radius asked for and widened it itself.
    radius_expanded: bool = False

    @property
    def populated(self) -> bool:
        return bool(self.cards)


def network_attested(text: str | None, label: str | None) -> bool:
    """Whether `text` says the provider IS in `label`'s network, with no negation in front of it."""
    if not text or not label:
        return False
    for m in re.finditer(re.escape(f'"{label}"'), text, re.I):
        window = text[max(0, m.start() - _NEGATION_WINDOW):m.start()]
        if not _NEGATION_RE.search(window):
            return True
    return False


def network_negated(text: str | None, label: str | None) -> bool:
    """Whether `text` says the provider is NOT in `label`'s network. Checked BEFORE the positive."""
    if not text or not label:
        return False
    for m in re.finditer(re.escape(f'"{label}"'), text, re.I):
        window = text[max(0, m.start() - _NEGATION_WINDOW):m.start()]
        if _NEGATION_RE.search(window):
            return True
    return False


class SapphireShoppingDriver(PortalDriver):
    key = "bcbssc-sapphire"
    portal_name = "BlueCross BlueShield of South Carolina (Zelis Sapphire, guest)"
    # This portal geocodes a typed location and scopes the search radius around it; a state alone
    # never commits, so a state-only query would search the tenant's default city (Columbia SC) and
    # return an answer about the wrong geography.
    location_fields = ("zip_code",)
    # Trap 1's empty `network_id=` came back on a restored context; start clean, as Molina does.
    requires_fresh_context = True

    def __init__(self, portal_key: str = "bcbssc-sapphire") -> None:
        try:
            self.tenant = TENANTS[portal_key]
        except KeyError as e:  # a config gap must fail loudly, not silently drive another tenant
            raise KeyError(
                f"no Sapphire tenant configured for {portal_key!r}; known: {sorted(TENANTS)}"
            ) from e
        self.key = self.tenant.portal_key
        self.portal_name = self.tenant.portal_name

    # ---------------------------------------------------------------- capture

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        entry = f"{HOST}/?ci={self.tenant.ci}"
        try:
            page.goto(entry, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            pass
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation to {entry} failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))

        if not self._wait_shell(page):
            return result(
                PortalStatus.BLOCKED,
                f"the Zelis shell never hydrated within {_SHELL_TIMEOUT_S:.0f}s (no network select and "
                f"no results surface). The URL falls back to an empty '?network_id=' in this state, so "
                f"nothing could be pinned and nothing was searched.",
                screenshot=shot("no-hydrate"),
            )
        if (ch := self._challenge(page)):
            return result(PortalStatus.BLOCKED, f"the portal presented a challenge ({ch}); not solved.",
                          screenshot=shot("challenge"))

        # Step 1 of the gated-entry wizard. Guest only — the alpha-prefix and login routes are left
        # alone; nothing about the member is ever typed into a portal.
        if self._click_visible(page, GATE_CONTINUE, timeout_ms=10_000):
            trail.append("gated entry: guest Continue")
            self._settle(page, 4_000)

        # Location. Driven and then READ BACK — never assumed. An uncommitted location leaves the
        # tenant's own default city in place, and a search there is an answer about the wrong place.
        committed, geo = self._commit_location(page, q, trail)
        if geo is None:
            return result(
                PortalStatus.UNKNOWN,
                f"the clinic location could not be committed (the portal held {committed!r}). This "
                f"tenant defaults to its own home city, so searching without committing would report "
                f"on the wrong geography rather than the clinic's.",
                screenshot=shot("no-location"),
            )
        trail.append(f"location: {committed!r} → geo {geo}")

        network = self._pinned_network(page)
        if not network:
            return result(PortalStatus.UNKNOWN,
                          "no network was pinned, so neither presence nor absence would be evidence.",
                          screenshot=shot("no-network"))
        pinned_by = ("the tenant's guest default"
                     if network == self.tenant.default_network_label else "the portal")
        trail.append(f"network: {network!r} (pinned by {pinned_by})")

        rs, searched = self._search(page, q, geo, trail)
        shot_name = shot("results")

        if not rs.surfaced:
            return result(
                PortalStatus.UNKNOWN,
                f"the results surface never rendered for {searched!r} — neither result cards nor the "
                f"portal's own no-results header appeared. That is an inconclusive read, not an "
                f"absence, so it is not reported as out-of-network.",
                screenshot=shot_name, matched_name=None,
            )

        # THE NPI LIVES ON THE PROFILE, NOT THE CARD. Cards carry name, address, specialty and the
        # network badge — no identifier at all. Matching on the card therefore falls through to names,
        # and the sheet's "Desire Clarke" does not token-match the portal's "Desiree Amelia Clarke",
        # which turned a provider the portal DOES list into a confident OUT_OF_NETWORK. Every
        # candidate is opened and its NPI read before any verdict is issued.
        ours, checked, profile_note = self._identify(page, rs, q, trail)
        if ours is not None:
            if network_negated(ours.text, network):
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"the portal's own card for {ours.headline!r} says it is NOT in {network!r} — "
                    f"out-of-network for the network this walk pinned.",
                    screenshot=shot_name, matched_name=ours.headline,
                )
            shot_name = shot("profile") or shot_name
            nets = self._networks_accepted(page, ours)
            if network_attested(ours.text, network) or nets:
                return result(
                    PortalStatus.IN_NETWORK,
                    f"the portal listed {ours.headline!r} (NPI {q.npi}) at the clinic location and "
                    f"attests it is in {network!r}"
                    + (f"; the profile lists {len(nets)} accepted network(s)." if nets else "."),
                    screenshot=shot_name, matched_name=ours.headline,
                    networks_accepted=tuple(nets),
                )
            return result(
                PortalStatus.UNKNOWN,
                f"{ours.headline!r} was listed, but the card carried no in-network attestation for "
                f"{network!r}, so presence alone does not establish the member's network.",
                screenshot=shot_name, matched_name=ours.headline,
            )

        if rs.populated:
            # Absence is only evidence when every candidate was actually identified. If some profile
            # would not open, this provider may be one of the ones we could not read.
            if checked < len(rs.cards):
                return result(
                    PortalStatus.UNKNOWN,
                    f"the portal returned {len(rs.cards)} provider(s) for {searched!r} on {network!r}, "
                    f"but only {checked} profile(s) could be opened to read an NPI. NPI {q.npi} was "
                    f"not among those, and an unread profile is not an absence.{profile_note}",
                    screenshot=shot_name,
                )
            if rs.radius_expanded:
                return result(
                    PortalStatus.UNKNOWN,
                    f"the portal found nothing for {searched!r} in the clinic's own radius and "
                    f"auto-expanded the search to fill the page ({len(rs.cards)} provider(s), none "
                    f"NPI {q.npi}). Those results are not the clinic's area, so their contents say "
                    f"nothing about whether this provider serves it.",
                    screenshot=shot_name,
                )
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"the portal returned {len(rs.cards)} provider(s) for {searched!r} within the "
                f"clinic's area on the pinned network {network!r}; all {checked} profiles were read "
                f"and NPI {q.npi} is not among them.",
                screenshot=shot_name,
            )
        return result(
            PortalStatus.UNKNOWN,
            f"the portal's no-results header answered {searched!r} "
            f"({rs.none_header or 'no detail'}). An empty result set does not distinguish "
            f"out-of-network from a search that found nobody by that name, so this is not an OON.",
            screenshot=shot_name,
        )

    # ------------------------------------------------------------- mechanics

    def _visible(self, page: Page, sel: str, limit: int = 8) -> Locator | None:
        """First VISIBLE match. Trap 2: Zelis ships hidden mobile duplicates and `.first` picks them."""
        loc = page.locator(sel)
        try:
            n = min(loc.count(), limit)
        except PlaywrightError:
            return None
        for i in range(n):
            cand = loc.nth(i)
            try:
                if cand.is_visible():
                    return cand
            except PlaywrightError:
                continue
        return None

    def _click_visible(self, page: Page, sel: str, timeout_ms: int = 8_000) -> bool:
        el = self._visible(page, sel)
        if el is None:
            return False
        try:
            el.click(timeout=timeout_ms)
            return True
        except (PlaywrightTimeout, PlaywrightError):
            return False

    def _wait_shell(self, page: Page, timeout_s: float = _SHELL_TIMEOUT_S) -> bool:
        """Poll until the SPA has rendered something real. Trap 1 — a fixed settle is not enough."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for sel in (NET_SELECT, CARDS, RESULTS_FOR, NONE_HDR, GATE_CONTINUE):
                try:
                    if page.locator(sel).count():
                        return True
                except PlaywrightError:
                    pass
            try:
                page.wait_for_timeout(_SHELL_POLL_MS)
            except PlaywrightError:
                return False
        return False

    def _settle(self, page: Page, pause_ms: int = 2_500) -> None:
        """networkidle never fires on these analytics-heavy portals; wait a bounded beat instead."""
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass

    def _commit_location(
        self, page: Page, q: PortalQuery, trail: list[str]
    ) -> tuple[str | None, str | None]:
        """Type the clinic ZIP, take the portal's own typeahead option, then read the geo back.

        Returns (label the portal holds, "lat,lng") — geo is None when nothing was committed, which
        the caller must treat as unanswerable rather than searching the tenant's default city.
        """
        zip_code = (q.zip_code or "").strip()
        if not zip_code:
            return None, None

        # Open the wizard's location step. After the guest Continue the trigger is usually already
        # on screen; when it is not, force-clicking the readonly header field reopens the wizard
        # (force only skips Playwright's hit-target check — the click is a real one on a real button).
        if not page.locator(LOC_TRIGGER).count():
            try:
                page.locator(LOC_INPUT).first.click(timeout=8_000, force=True)
                self._settle(page, 4_500)
            except (PlaywrightTimeout, PlaywrightError):
                pass
        if not self._click_visible(page, LOC_TRIGGER, timeout_ms=10_000):
            trail.append("location trigger not reachable")
            return None, None
        self._settle(page, 4_000)

        # The editable box is the wizard's own, NOT the header's readonly display and NOT the
        # provider autosuggest — typing the ZIP into that one searches for a provider named "33461".
        box = self._visible(page, LOC_SEARCH)
        if box is None:
            trail.append("location search box never became visible")
            return None, None
        # focus() + real keystrokes. `fill()` sets .value without the per-key events the typeahead
        # listens for, and `locator.type()` never landed focus here — both left the box empty while
        # reporting success, which is why earlier walks saw "no suggestions" for a valid ZIP.
        try:
            box.focus()
            page.keyboard.type(zip_code, delay=180)
        except (PlaywrightTimeout, PlaywrightError) as e:
            trail.append(f"location search box not typeable: {type(e).__name__}")
            return None, None
        self._settle(page, 6_000)

        label = self._pick_suggestion(page, trail, zip_code)
        if label is None:
            return None, None
        # Nothing is committed until Apply. Skipping it leaves the tenant default in place.
        if not self._click_visible(page, LOC_APPLY, timeout_ms=10_000):
            trail.append("location Apply not clickable")
            return label, None
        trail.append("location: Apply")
        self._settle(page, 6_000)
        # The wizard's own Continue, when the overlay is still up.
        if self._click_visible(page, LOC_CONTINUE, timeout_ms=6_000):
            trail.append("location step: Continue")
            self._settle(page, 5_000)

        held = label
        try:
            el = self._visible(page, LOC_INPUT)
            if el is not None:
                held = el.input_value(timeout=3_000) or label
        except PlaywrightError:
            pass

        geo = self._geo_from_url(page.url)
        # The portal must actually be holding the ZIP we asked for. Without this check a failed
        # typeahead leaves the default city in place and the walk answers about Columbia SC.
        if geo and zip_code not in (held or "") and not self._same_place(held, q):
            trail.append(f"location did not take: portal holds {held!r}, asked {zip_code!r}")
            return held, None
        return held, geo

    def _pick_suggestion(self, page: Page, trail: list[str], zip_code: str) -> str | None:
        """Take the suggested location that IS the ZIP we typed.

        The dialog always lists "My Current Location" and the tenant's default city ("Columbia, SC —
        your plan's default location") alongside any real suggestion. Taking the first row would
        therefore commit Columbia on every ZIP that returns no match, and the walk would answer
        confidently about South Carolina for a Florida clinic. So the row must match the ZIP.
        """
        for attempt in range(3):
            try:
                rows = page.locator(LOC_SUGGEST)
                n = min(rows.count(), 12)
            except PlaywrightError:
                n = 0
            for i in range(n):
                try:
                    primary = " ".join((rows.nth(i).inner_text() or "").split())
                except PlaywrightError:
                    continue
                if primary != zip_code:
                    continue
                try:
                    rows.nth(i).click(timeout=8_000)
                except (PlaywrightTimeout, PlaywrightError):
                    continue
                self._settle(page, 4_000)
                # The portal names the place the ZIP resolves to, and it need not be the city on the
                # sheet: 33461 comes back "Palm Springs, FL 33461" for a clinic addressed Lake Worth.
                # Report the PORTAL's name for it, never the one we assumed.
                detail = primary
                try:
                    subs = page.locator(LOC_SUGGEST_SUB)
                    if i < subs.count():
                        detail = " ".join((subs.nth(i).inner_text() or "").split())[:80] or primary
                except PlaywrightError:
                    pass
                trail.append(f"location suggestion: {detail!r}")
                return detail
            if attempt < 2:
                self._settle(page, 4_000)
        trail.append(f"the portal suggested no location matching {zip_code!r}")
        return None

    def _same_place(self, held: str | None, q: PortalQuery) -> bool:
        """The portal echoes 'City, ST — ZIP'; accept a city/state match when the ZIP is not echoed."""
        if not held:
            return False
        text = held.lower()
        city_ok = bool(q.city) and q.city.lower() in text
        state_ok = bool(q.state) and re.search(rf"\b{re.escape(q.state)}\b", held, re.I) is not None
        return city_ok and state_ok

    @staticmethod
    def _geo_from_url(url: str) -> str | None:
        geo = parse_qs(urlparse(url).query).get("geo_location", [None])[0]
        return geo or None

    def _pinned_network(self, page: Page) -> str | None:
        el = self._visible(page, NET_SELECT)
        if el is None:
            return None
        try:
            return " ".join((el.inner_text() or "").split())[:90] or None
        except PlaywrightError:
            return None

    def _search(
        self, page: Page, q: PortalQuery, geo: str, trail: list[str]
    ) -> tuple[ResultSet, str]:
        """Navigate the portal's own /search/name/<term> URL with the committed geo.

        This is the same navigation Enter performs — the query string is copied from a real
        Enter-driven search, not invented. A hand-built one silently returned nothing at all.
        """
        last = (q.provider_last_name or "").strip()
        first = (q.provider_first_name or "").strip()
        if not last:
            trail.append("no provider surname to search")
            return ResultSet(surfaced=False), last
        # Full name first: it narrows the candidate list this driver then has to open one profile at
        # a time. The surname alone is the fallback, because the sheet's given name is not always the
        # directory's ("Desire" vs "Desiree Amelia").
        term = f"{first} {last}".strip() if first else last

        # Type into the portal's own box and press Enter, rather than navigating a hand-built URL.
        # A direct /search/name/... goto renders zero cards unless the session already carries the
        # committed location, and "zero cards" is exactly the reading that must never be wrong here.
        box = self._visible(page, SEARCH)
        if box is None:
            trail.append("search box never became visible")
            return ResultSet(surfaced=False), term
        try:
            box.focus()
            page.keyboard.type(term, delay=90)
            self._settle(page, 2_500)
            page.keyboard.press("Enter")
        except (PlaywrightTimeout, PlaywrightError) as e:
            trail.append(f"search box not driveable: {type(e).__name__}")
            return ResultSet(surfaced=False), term
        trail.append(f"searched {term!r} at geo {geo}")
        self._wait_results(page)
        self._settle(page, 3_000)
        return self._read_results(page), term

    def _wait_results(self, page: Page, timeout_s: float = 60.0) -> bool:
        """Wait for a TERMINAL results state: cards, or the portal's own no-results header.

        The "Results for: …" banner paints BEFORE the cards do, so treating it as done reads a
        half-rendered page — the same search returned 1 card on one run and 0 on the next purely on
        timing, and a 0 there is indistinguishable from an absence. Only cards or an explicit
        no-results header end the wait; the banner alone keeps it going.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for sel in (CARDS, NONE_HDR):
                try:
                    if page.locator(sel).count():
                        return True
                except PlaywrightError:
                    pass
            try:
                page.wait_for_timeout(_SHELL_POLL_MS)
            except PlaywrightError:
                return False
        return False

    def _read_results(self, page: Page) -> ResultSet:
        def text_of(sel: str) -> str | None:
            try:
                if page.locator(sel).count():
                    return " ".join((page.locator(sel).first.inner_text() or "").split())[:240]
            except PlaywrightError:
                pass
            return None

        cards: list[Card] = []
        try:
            loc = page.locator(CARDS)
            for i in range(min(loc.count(), _MAX_CARDS)):
                raw = loc.nth(i).inner_text() or ""
                cards.append(Card(text=raw, npis=tuple(_NPI_RE.findall(raw))))
        except PlaywrightError:
            pass

        none_header = text_of(NONE_HDR)
        results_for = text_of(RESULTS_FOR)
        try:
            expanded = bool(EXPANDED_RADIUS_RE.search(page.inner_text("body") or ""))
        except PlaywrightError:
            expanded = False
        # Trap 4: surfaced means the portal ANSWERED — cards, or its own no-results header. Neither
        # one means the page never rendered, and that must not read as absence.
        return ResultSet(surfaced=bool(cards or none_header or results_for), cards=cards,
                         none_header=none_header, results_for=results_for,
                         radius_expanded=expanded)

    def _identify(
        self, page: Page, rs: ResultSet, q: PortalQuery, trail: list[str]
    ) -> tuple[Card | None, int, str]:
        """Open each result's profile and read its NPI. Returns (our provider, profiles read, note).

        Identification is by NPI ONLY. The name on the sheet is not the name in the directory —
        "Desire Clarke" is listed as "Desiree Amelia Clarke, MD" — so a name test either invents an
        absence (as it did here) or, with a looser rule, matches a stranger: this portal's sibling
        answered the surname "Orem" with "Shoaf, Noremi D".

        The returned Card carries the PROFILE text, because that is where both the NPI and the
        network attestation live.
        """
        results_url = page.url
        checked = 0
        failures = 0
        for i in range(min(len(rs.cards), _MAX_PROFILES)):
            link = self._card_link(page, i)
            if link is None:
                failures += 1
                continue
            try:
                link.click(timeout=10_000)
            except (PlaywrightTimeout, PlaywrightError):
                failures += 1
                self._back_to(page, results_url)
                continue
            self._settle(page, 4_000)
            self._wait_profile(page)
            try:
                text = page.inner_text("body") or ""
            except PlaywrightError:
                text = ""
            npis = tuple(_NPI_RE.findall(text))
            if npis:
                checked += 1
            else:
                failures += 1
            if q.npi and q.npi in npis:
                trail.append(f"profile {i}: NPI {q.npi} matched")
                # Name it from the profile's own heading. `text` starts with the skip-to-content link,
                # so a first-line headline would report "Skip to main content" as the provider.
                return Card(text=text, npis=npis, name=self._profile_name(page)), checked, ""
            self._back_to(page, results_url)
        note = (f" {failures} profile(s) did not render an NPI." if failures else "")
        trail.append(f"profiles read: {checked} of {len(rs.cards)}")
        return None, checked, note

    def _profile_name(self, page: Page) -> str | None:
        """The provider name as the PORTAL holds it.

        Read from the profile URL, whose last path segment is the portal's own JSON state and carries
        `"name":"Randal Orem"`. The visible headings are unreliable here: `h1` is the page title
        ("Find a Provider - Provider Profile") and the card's ProviderName node does not exist on the
        profile route, so both name the page rather than the provider.
        """
        m = re.search(r'"name"\s*:\s*"([^"]{1,80})"', unquote(page.url))
        if m:
            return m.group(1)
        for sel in ("[data-cy='search-result-ProviderName']", "h2"):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    txt = " ".join((loc.inner_text() or "").split())[:120]
                    if txt and "provider profile" not in txt.lower():
                        return txt
            except PlaywrightError:
                continue
        return None

    def _card_link(self, page: Page, index: int) -> Locator | None:
        """The visible provider-name link of card `index`, scoped inside that card."""
        try:
            card = page.locator(CARDS).nth(index)
            links = card.locator(CARD_LINK)
            for j in range(min(links.count(), 4)):
                cand = links.nth(j)
                if cand.is_visible():
                    return cand
        except PlaywrightError:
            return None
        return None

    def _back_to(self, page: Page, url: str) -> None:
        """Return to the result set. go_back() is unreliable on this SPA, so re-navigate."""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError):
            return
        self._wait_results(page, timeout_s=30.0)
        self._settle(page, 2_500)

    def _expand_identifiers(self, page: Page) -> bool:
        """Open the profile's Identifiers accordion, where the NPI is."""
        for sel in (PROFILE_IDENTIFIERS,
                    "[data-cy*='identifier']",
                    "mat-expansion-panel-header",
                    "button:has-text('Identifiers')"):
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 6)):
                    cand = loc.nth(i)
                    if not cand.is_visible():
                        continue
                    if "identifier" not in (cand.inner_text() or "").lower():
                        continue
                    cand.click(timeout=6_000)
                    self._settle(page, 2_500)
                    return True
            except (PlaywrightTimeout, PlaywrightError):
                continue
        return False

    def _wait_profile(self, page: Page, timeout_s: float = 40.0) -> bool:
        """The profile is its own SPA route and hydrates on its own schedule.

        The NPI is NOT in the part of the page that paints first — it sits at the bottom under
        "More About This Provider" → "Identifiers", and the sections below the fold render lazily.
        Reading immediately therefore finds no NPI on a profile that has one, which downgrades a
        provider the portal does list. So scroll the page to the end before believing the absence.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if _NPI_RE.search(page.inner_text("body") or ""):
                    return True
            except PlaywrightError:
                pass
            try:
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_500)
            except PlaywrightError:
                return False
            self._expand_identifiers(page)
        return False

    def _networks_accepted(self, page: Page, card: Card) -> list[str]:
        """The profile page behind a card lists every network the provider is in. Best-effort: a
        profile that will not open must not downgrade a verdict the card already supports."""
        try:
            loc = page.locator(PROFILE_NETWORK)
            return [" ".join((loc.nth(i).inner_text() or "").split())[:90]
                    for i in range(min(loc.count(), 40))]
        except PlaywrightError:
            return []

    def _challenge(self, page: Page) -> str | None:
        try:
            body = (page.inner_text("body") or "").lower()
        except PlaywrightError:
            return None
        return next((p for p in _CHALLENGE_PHRASES if p in body), None)


class PublixSapphireDriver(SapphireShoppingDriver):
    """The Publix book on the same Zelis instance. Three networks, no alpha-prefix route."""

    key = "publix-sapphire"
    portal_name = TENANTS["publix-sapphire"].portal_name

    def __init__(self) -> None:
        super().__init__("publix-sapphire")
