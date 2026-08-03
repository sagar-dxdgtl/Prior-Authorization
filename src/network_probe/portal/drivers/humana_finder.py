"""Humana Provider Finder — finder.humana.com (redirects to findcare.humana.com).

Covers the Ins Test 3 row "Humana Medicare FL" (roster payer key `humana-fl`) and the Test 2 row
"Humana Medicare CO". Humana also publishes a no-auth PDEX Plan-Net FHIR directory (wired in
payers/adapters/fhir_pdex.py); that stays the sanctioned fallback whenever this portal cannot answer,
and this driver is deliberately quick to hand back UNKNOWN and let it.

WHAT WAS VERIFIED, AND WHEN
---------------------------
Everything in the "verified" list below was observed in this review's own live headless runs on
2026-07-28 (Thornton CO 80229 / network "Medicare PPO" and "Humana Honor PPO"; Tampa FL 33618 /
"Humana Honor PPO"), and nothing else in this docstring is presented as observed. The file's first
author left no run behind, so every earlier claim was re-checked from scratch; the ones that survived
are marked verified, the ones that could not be re-checked are listed as unverified and the code never
depends on them being true.

Live outcomes of the two client rows, for the record:

  * Test 2 "Humana Medicare CO", Roulhac / NPI 1801837109 / 80229, network "Humana Honor PPO" pinned:
    "Roulhac" and "Maurice Roulhac" both returned ZERO results out to a 100-mile radius. That is
    UNKNOWN, not out-of-network. It is also consistent with the staff's manual INN: their reasoning was
    Original Medicare + a Humana Medigap plan, where there is no provider network at all and a
    Medicare-participating provider is in-network — an MA directory has no opinion to give, and this
    driver does not invent one.
  * Ins Test 3 "Humana Medicare FL", Shah / NPI 1710304746 / 33618, "Humana Honor PPO" pinned: "Shah"
    returned 26 results, all 26 read (widened to 30 per page), Humana's header echoed the term — a
    complete, attributable result set. None of the 26 is Kush Shah (NPPES: KUSH SHAH MD): they are
    Sailesh, Chirag, Jayesh, Shalin, Anna K, Aman, Anjan, Suketu, Shivan, Ami, Aashka, Sagar, Nikesh,
    Sejal Shah Desai, and non-matches like Shaha/Shahwar/Shahenaz. The verdict is still UNKNOWN, because
    the plan string pinned that network on NAMES only — see the doctrine below. The stated total moved
    between sessions (39, then 26), which is why the total is re-read from the page every time and never
    cached or inferred.

Verified live (2026-07-28):

  * finder.humana.com 302s to findcare.humana.com and serves a fully driveable guest flow. No bot
    challenge, no WAF, no protection interstitial at any step of the walk. (The older note that
    "Humana times out at the WAF" was about www.humana.com — a different host.)
  * The walk, each step gated on the previous:
        /                        "Search as a guest" → Places location box + Continue
        /type-of-care            Medical | Dental | Vision | Pharmacy   (nucleus-button[title='Medical'])
        /medical?view=networks   "Choose your medical network" + Select      <- pins the network
        /medical?view=search     "Search by: All | Name | Specialty | More" + the search box
        /medical?view=results&page=1&results=20   the result cards
  * Every selector named in the constants below resolved on the view it is used on, EXCEPT the two
    result-view ones that only exist in one state (see the next two bullets).
  * `#findcare-medical__results-header-search-results-summary` exists ONLY when results came back, and
    reads exactly `59 in network results for “Smith”` — the portal states its own total, and echoes the
    term. On an empty search it is absent and `#results-not-found-container` reads
    `No results in network for “Roulhac” within 15 miles`. Both are used; neither is guessed at.
  * A populated page renders only ONE page of the total: 59 results for "Smith", 20 cards in the DOM,
    URL `…&results=20`, pager "Show 10 20 30 Results 1 2 3". Absence from that page proves nothing.
  * Cards are `#findcare-domain__results-list-container` descendants whose id is all digits (20 nodes
    for 20 cards, none nested inside another). The results HEADER is also a child of that container,
    which is why the card read is id-filtered rather than taken from the container's children.
  * Each card carries Humana's own per-card label — "In network" — plus name, specialty, group,
    address, distance and phone. No NPI appears on a card, so identity here is name-only.
  * `sessionStorage.FCMEDICALSESSIONSTORE` appears once the medical module loads and carries
    `networks.current` (12 rows for Thornton CO) and `selectedNetwork`. The DOM list agreed with it at
    12 rows for this market.
  * The network list interleaves group headers as rows: `{"networkId":0,"networkName":"Medicare
    networks"}`, rendered with `.v-list-item--disabled`. `selectedNetwork` DEFAULTS to that header, so
    pressing Select without picking a row searches nothing — the pin is therefore verified against the
    store afterwards, not assumed from the click.
  * Network display names are NOT unique and NOT 1:1 with networkId: "Medicare PPO", "Group Medicare
    PPO+" and "Humana Honor PPO" all share networkId 3912, and "Natl Med HMO Colorado-Home" appears
    TWICE with ids 3922 and 4037. A plan that matches a duplicated name cannot identify which network
    the member is in, so that case is refused an absence reading.
  * The results view re-searches through its own "Update" button, which really is
    `#search-container-search-btn` — but a click on it can be intercepted (the search box's suggestion
    panel opens over it and cost a 45s timeout in this review's run 3). It is clicked with Escape
    first, force=True and a short timeout.
  * The 15-mile default radius is real (`#filter-bar-distance-btn` reads "Distance: 15 mi") and
    "Expand distance to 50 miles" (`#results-not-found-expand-search-btn`) appears on an empty search.
  * "Roulhac" in network "Medicare PPO" within 15 miles of 80229 returned ZERO results — which under
    the doctrine below is UNKNOWN, not out-of-network.
  * `/medical?view=sessionError` is real and is Humana's own failure, not a block on us. Captured from
    the wire: `POST https://findcare.humana.com/apim-gateway/api/v1/medical/guest` returned **HTTP 502**
    on both attempts of two consecutive sessions, with no challenge text, no WAF text and no bot
    protection anywhere on the page. It struck three of this review's later sessions after five clean
    ones. The walk therefore retries once and a persistent 502 is reported as UNKNOWN naming the
    endpoint — never as a verdict, and never as BLOCKED, because nothing refused us.
  * The group header labels the rows that follow, in a market that has more than one: Tampa returned 14
    networks under "Medicaid networks" (FL Healthy Horizons LTC, Humana Healthy Horizons in FL) then
    "Medicare networks" (Medicare PPO, Humana Honor PPO, CarePlus, FL Medicare HMO, …), and the
    line-of-business narrowing correctly kept only the Medicare group for a Medicare plan.
  * Clicking "30" in the per-page control works: the Tampa search re-read 26 of 26 cards after it.

NOT verified by this review, and therefore never load-bearing:

  * The 502's root cause and hit-rate. The status code and endpoint are confirmed; the earlier claims
    about an nginx banner and "4 of 5 attempts" are not, and nothing depends on them.
  * The "Continue without a network" (all plans & networks) path. Humana's session endpoint started
    502ing before it could be exercised end to end, so the fallback is coded but unproven. It can only
    ever yield UNKNOWN, so an unproven path there cannot produce a wrong verdict.
  * That Google Places mis-resolves a bare ZIP to another state. "Thornton, CO 80229" produced a
    correct first suggestion here. The ZIP-bearing-suggestion rule is kept because it is cheap and the
    failure it guards against (searching the wrong market) is silent — but the code now also refuses to
    call anything out-of-network unless the portal's own address bar echoes the clinic ZIP.
  * That the NPI is not indexed by the search box. Untested, so the NPI is simply never searched and
    never used to conclude anything; it is matched inside a card's text only as a bonus confirmation.
  * That the network list virtualises in larger markets (all 12 CO rows were in the DOM at once). The
    scroll-then-click fallback is kept for markets that do.
  * That typing into the network combobox filters the list — it did NOT filter for CO. Exact-row
    clicking is tried first and does not depend on filtering.
  * That per-page "30" is clickable (`#findcare-medical__results-per-page-options-container` exists on
    a populated page; the widen click was not confirmed to take effect). Widening is best-effort and
    completeness is decided by comparing Humana's stated total to the cards actually read, never by
    whether the widen worked.

VERDICT DOCTRINE (base.py, no Humana-specific extension)
--------------------------------------------------------
    matched in a pinned network                                        → IN_NETWORK
    matched card carries a NEGATIVE network label + absence-licensed    → OUT_OF_NETWORK
    absence-licensed AND populated AND term-echoed AND every result
      card read AND no ambiguous namesake                              → OUT_OF_NETWORK
    anything else                                                      → UNKNOWN
    interactive challenge or hard refusal                              → BLOCKED (→ use the FHIR path)

"Absence-licensed" means ALL of: a network was pinned; `plan_match.match_plan()` returned an
identifier-grade match (`PlanMatch.confirms_network`) so we know it is the member's network; that
network's display name was unique in Humana's list; and the portal's own address bar echoed the clinic
ZIP. Nothing weaker may produce an out-of-network reading:

    ⚠ CONSEQUENCE THE CALLER MUST KNOW: none of the 25 network labels observed live (11 in CO, 14 in
    FL) carries a contract/PBP/market identifier — they are marketing names like "Humana Honor PPO",
    "CarePlus", "FL Medicare HMO". `confirms_network` therefore cannot be satisfied from these labels,
    so in practice this driver answers IN_NETWORK or UNKNOWN and will only reach OUT_OF_NETWORK when
    Humana labels our provider's own card out-of-network, or in a market whose labels do carry an
    identifier the 271 also carries. That is the doctrine working as intended, not a gap to route
    around: this portal's own plan list does not identify plans well enough to disprove membership,
    and the honest response is to answer UNKNOWN and let the PDEX FHIR directory or a manual check
    settle those rows.

  * A names-only (MEDIUM) plan match pins the search but licenses no absence — this is exactly the
    CareFlex mis-pin that plan_match.py exists to prevent.
  * "Continue without a network" is used only to get a screenshot and a possible presence signal. An
    earlier version of this file argued that the all-plans-and-networks directory is a superset, so
    absence from it is absence from the member's network, and returned OUT_OF_NETWORK with no plan
    confirmed at all. That superset claim was never verified and it inverts the doctrine's whole point,
    so it is gone: unpinned means UNKNOWN, in both directions.
  * An empty result set is UNKNOWN. The removed "positive control" (full name 0 while surname N ⇒
    absent) is unsound on this class of portal: the UHC driver's own verified note records "Randall
    Orem" returning nothing while "Orem" returned two, i.e. a full-name zero can just mean the search
    does not answer full-name queries. A zero says nothing here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal import browser as pb
from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus, Reachability
from network_probe.portal.plan_match import match_plan_with_fallback as match_plan

ENTRY = "https://finder.humana.com/"

# --- step 1: location (Google Places combobox on the guest landing view) -----------------------------
_LOC_INPUT = "#findcare-domain__google-autocomplete-input"
_LOC_ITEM = ".findcare-domain__google-autocomplete-list-item"
_LOC_CONTINUE = "#findcare-domain__guest-search-get-started-btn"
_LOC_SHOWN = "#findcare-medical__utility-bar-address, #findcare-domain__utility-bar-address"

# --- step 2: type of care (custom <nucleus-button> elements: no button role, so get_by_role misses) ---
_MEDICAL = ("nucleus-button[title='Medical']", "nucleus-button[label='medical']")

# --- step 3: network selection ----------------------------------------------------------------------
_NET_INPUT = "#findcare-domain__network-select-input"
_NET_ROW = ".network-select-list-item"
_NET_PICKABLE = ".network-select-list-item:not(.v-list-item--disabled)"
_NET_SELECT_BTN = "#findcare-medical__network-select-list-container-search-btn"
_NET_SKIP_BTN = "#findcare-medical__continue-without-network"
_NET_SHOWN = "#findcare-medical__utility-bar-network"

# --- step 4/5: search + results ---------------------------------------------------------------------
_SEARCH_INPUT = "#findcare-domain__search-input"
# The search view's submit button and the results view's "Update" button. Both ids verified live; only
# one exists at a time.
_SEARCH_BTNS = ("#search-view-search-btn", "#search-container-search-btn")
_SEARCH_TABS = "#findcare-domain__tabs-grid-container"
_RESULTS_LIST = "#findcare-domain__results-list-container"
_RESULTS_SUMMARY = "#findcare-medical__results-header-search-results-summary"
_RESULTS_EMPTY = "#results-not-found-container"
_RESULTS_EXPAND = "#results-not-found-expand-search-btn"  # "Expand distance to 50 miles"
_RESULTS_DISTANCE = "#filter-bar-distance-btn"            # "Distance: 15 mi"
_PER_PAGE = "#findcare-medical__results-per-page-options-container"
_PER_PAGE_MAX = "30"  # Humana offers 10 / 20 / 30 per page; 30 is the widest single-page view

_MAX_CARDS = 120  # a read that would exceed this is reported as incomplete, never silently truncated

# Dismissable overlays, re-checked after every navigation because a layer that appears late is exactly
# what silently breaks a later step. `#findcare__search-results-verint-survey-container` really is in
# the results markup (verified), so the Qualtrics/Verint close buttons are worth trying.
_OVERLAYS = (
    "#onetrust-accept-btn-handler",
    "#onetrust-reject-all-handler",
    "[id^='QSIWebResponsiveDialog'] [aria-label*='Close' i]",
    "[class*='QSIWebResponsive'] [aria-label*='Close' i]",
    "[class*='QSIWebResponsive'] [title*='Close' i]",
)

# Product-type tokens. A plan and a network naming *different* product types are not the same network,
# however many other words they share. Used only to VETO a match that plan_match already made — never
# to create one, because filtering the candidate list first could turn a tie into a false unique winner.
# HMOPOS is listed before HMO/POS so the compound name is read as one product, not as both.
_PRODUCT_TYPES = ("HMOPOS", "PFFS", "SNP", "HMO", "PPO", "EPO", "POS")

_STORE_KEY = "FCMEDICALSESSIONSTORE"  # verified: present once /medical loads, not on the landing view

_WALK_ATTEMPTS = 2  # bounded retries for a Humana-side guest-session error

# Humana states its own total, and echoes the term: "59 in network results for “Smith”". Anchored at
# the start of the summary so a stray number elsewhere in the header can never be read as the total.
_TOTAL_RE = re.compile(r"^\s*([\d,]+)\s+(?:in[\s-]?network\s+)?results?\b", re.I)
_NO_RESULTS_RE = re.compile(r"\bno\s+results\b", re.I)

# Every NEGATIVE network phrasing must be enumerated, and tested BEFORE the positive one, because the
# positive test is a substring test and "not in network" CONTAINS "in network". Two sibling drivers
# shipped that inversion and turned cards the payer had explicitly labelled out-of-network into
# IN_NETWORK verdicts. Humana's own observed label is the positive "In network"; the negatives are here
# because the results page can legitimately carry them (Humana runs a "retry search in all networks"
# feature flag, so a network-scoped search is not a guarantee that every card on the page is in it).
_NEGATIVE_BADGES = (
    "not in network", "not in-network", "not-in-network",
    "out of network", "out-of-network", "out of the network",
    "non-network", "nonnetwork", "non participating", "non-participating", "nonparticipating",
)
_POSITIVE_BADGES = ("in network", "in-network")

# Interactive challenges and hard refusals, matched against VISIBLE TEXT only (a dormant script is not
# a gate). Neither is ever solved or worked around: it is screenshotted, reported BLOCKED, and the
# payer routes to its public FHIR directory instead.
_CHALLENGE_TEXT = (
    "press & hold", "press and hold", "verify you are a human", "verify you are human",
    "are you a robot", "complete the security check", "checking your browser", "unusual traffic",
    "i'm not a robot",
)
_WAF_TEXT = (
    "access denied", "request unsuccessful", "incident id", "you don't have permission to access",
    "has been blocked", "error 1020",
)


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str | None) -> set[str]:
    """Upper-case alphanumeric words. Whole-token matching is the point: substring matching lets a
    query for Kush Shah match the card "Shah, Kushal" ("kush" is a substring of "kushal") and report a
    different person as our provider."""
    return {t for t in re.split(r"[^A-Za-z0-9]+", (s or "").upper()) if t}


def _product_types(s: str | None) -> set[str]:
    up = (s or "").upper()
    return {p for p in _PRODUCT_TYPES if p in up}


def _badge(text: str | None) -> str | None:
    """A card's own network label: "out" | "in" | None.

    Negatives are tested first and win outright. See `_NEGATIVE_BADGES`: the positive phrase is a
    substring of the negative ones, so testing "in network" first inverts the verdict.
    """
    low = (text or "").lower()
    if any(neg in low for neg in _NEGATIVE_BADGES):
        return "out"
    return "in" if any(pos in low for pos in _POSITIVE_BADGES) else None


def _parse_total(summary: str | None) -> int | None:
    """Humana's own stated result total, or None when it did not state one.

    None must never be replaced by the number of cards rendered. That substitution is what makes a
    truncated page look complete: with total == cards_read, "we have seen every result" is trivially
    true and the first search that renders cards without our provider becomes a false OUT_OF_NETWORK.
    """
    m = _TOTAL_RE.match(summary or "")
    return int(m.group(1).replace(",", "")) if m else None


def _echoes_term(text: str | None, term: str) -> bool:
    """Does the portal's own results copy name the term we just searched?

    Proves the list on screen belongs to THIS lookup. Without it, a submit click that silently failed
    leaves the previous search's results (or an unfiltered default list) on screen, and reading absence
    from that is reading absence from the wrong list.
    """
    hay = _norm(text)
    return bool(hay) and all(_norm(t) in hay for t in _tokens(term))


@dataclass
class _Read:
    """One search's result page, and exactly how much of it we can prove we saw."""

    term: str
    total: int | None  # Humana's own stated total; None = it did not say
    cards: list[str] = field(default_factory=list)
    cards_ok: bool = False  # the card read completed without error and without hitting the cap
    echoed: bool = False  # the results copy named our term
    radius: str = ""

    @property
    def populated(self) -> bool:
        return bool(self.total) and bool(self.cards)

    @property
    def complete(self) -> bool:
        """Every result Humana said it had for THIS term is on screen and was read.

        All four conditions are load-bearing for an absence: a stated total (never the rendered count),
        an error-free card read, enough cards to account for the total, and the portal's own copy naming
        our term — without the last one the list may belong to a previous or unfiltered search.
        """
        return (self.populated and self.cards_ok and self.echoed
                and len(self.cards) >= (self.total or 0))


@dataclass
class _Pin:
    """The network we pinned, and whether it is strong enough to license an absence reading."""

    name: str | None
    why: str
    confirms: bool = False  # identifier-grade plan match — the only thing that may license an OON
    duplicate: bool = False  # the display name occurs more than once in Humana's own list


@dataclass
class _Walk:
    reached: bool = False
    steps: list[str] = field(default_factory=list)
    blocked: str = ""  # non-empty → an interactive challenge or a hard refusal was shown
    session_error: bool = False
    geo_echo: str = ""  # what the portal's address bar says it scoped the search to
    geo_ok: bool = False  # ...and whether that echo contains the clinic ZIP


class HumanaFinderDriver(PortalDriver):
    key = "humana-finder"
    portal_name = "Humana Provider Finder"

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The trail is not decoration: a walk that stopped early and then reported OON is the worst
            # failure this layer can produce, and recording every step makes that impossible to hide.
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        walk = _Walk()
        for attempt in range(1, _WALK_ATTEMPTS + 1):
            walk = self._walk_to_networks(page, q)
            trail.extend(walk.steps if attempt == 1 else [f"retry {attempt}: {s}" for s in walk.steps])
            if walk.reached or walk.blocked or not walk.session_error:
                break
            self._settle(page, 6_000)

        if walk.blocked:
            return result(
                PortalStatus.BLOCKED,
                f"Humana Find Care refused automated access: {walk.blocked}. Not solved or worked "
                f"around by design — screenshotted and reported. Use Humana's no-auth PDEX Plan-Net "
                f"FHIR directory for this provider instead.",
                screenshot=shot("blocked"),
                reachability=Reachability.CHALLENGE if "challenge" in walk.blocked
                else Reachability.WAF_BLOCK,
            )
        if not walk.reached:
            if walk.session_error:
                return result(
                    PortalStatus.UNKNOWN,
                    f"Humana Find Care could not open a guest medical session after {_WALK_ATTEMPTS} "
                    f"attempts — it landed on /medical?view=sessionError ('Error loading member "
                    f"information'), which is Humana's own guest-session failure and not a refusal of "
                    f"us: no challenge or WAF text was present. It is not a network verdict either. "
                    f"Retry later, or use Humana's no-auth PDEX Plan-Net FHIR directory.",
                    screenshot=shot("session-error"),
                )
            return result(
                PortalStatus.UNKNOWN,
                "Humana Find Care did not reach the network-selection step; the guest flow "
                "(location → Medical → choose network) changed or did not render.",
                screenshot=shot("no-network-step"),
            )

        networks = self._network_list(page)
        trail.append(f"{len(networks)} networks offered near {q.zip_code or 'the clinic'}")
        pin = self._pick_network(networks, q)

        if pin.name:
            if not self._commit_network(page, pin.name):
                return result(
                    PortalStatus.UNKNOWN,
                    f"Humana Find Care offered network {pin.name!r} for plan {q.plan!r} but it could "
                    f"not be confirmed as selected (the portal's own utility bar and session store did "
                    f"not come back with it), so we cannot say which network was searched.",
                    screenshot=shot("network-not-selectable"),
                )
            trail.append(f"network pinned: {pin.name} ({pin.why})")
        else:
            trail.append(f"no network pinned for plan {q.plan!r} ({pin.why})")
            if not self._continue_without_network(page):
                return result(
                    PortalStatus.UNKNOWN,
                    f"Plan {q.plan!r} did not identify one of Humana's {len(networks)} networks near "
                    f"{q.zip_code or 'the clinic'} ({pin.why}), and the 'Continue without a network' "
                    f"fallback did not open either, so nothing was searched.",
                    screenshot=shot("no-plan-no-fallback"),
                )
            trail.append("searched all plans & networks (no network pinned)")

        scope = f"network {pin.name!r}" if pin.name else "its all-plans-and-networks directory"
        blockers = self._absence_blockers(pin, walk)
        if blockers:
            trail.append("absence cannot be decisive here: " + "; ".join(blockers))

        terms = self._search_terms(q)
        if not terms:
            return result(
                PortalStatus.UNKNOWN,
                f"No provider name was available to search for NPI {q.npi}, and Humana's search box "
                f"takes names (the NPI is not used as a search term here), so nothing was searched in "
                f"{scope}.",
                screenshot=shot("no-search-term"),
            )

        reads: list[_Read] = []
        for term, kind in terms:
            read = self._run_search(page, term, q, trail)
            if read is None:
                trail.append(f"{kind} {term!r}: the search control never came back — not searched")
                continue
            reads.append(read)
            trail.append(
                f"searched {kind} {term!r} → "
                + (f"{read.total} result(s)" if read.total is not None else "no stated total")
                + f", {len(read.cards)} card(s) read"
                + ("" if read.echoed else ", RESULTS COPY DID NOT ECHO THE TERM")
            )

            matched, why = self._match(read.cards, q)
            if matched:
                return self._matched_verdict(matched, term, kind, read, pin, blockers, q, result, shot)
            if why == "ambiguous":
                # A same-surname card carrying only an initial (or a first name that merely starts with
                # ours) cannot be resolved. Returning it would be a false IN; treating it as absent
                # would be a false OON while our provider may be that very row. It decides nothing.
                return result(
                    PortalStatus.UNKNOWN,
                    f"Humana Find Care returned a card in {scope} that agrees with "
                    f"{self._who(q)} on the surname but carries only an initial or a longer first name "
                    f"where a first name would settle it, so it cannot be shown to be — or not to be — "
                    f"NPI {q.npi}. Humana prints no NPI on a card, so the name is the only identity "
                    f"handle and this row needs a manual check.",
                    result_count=read.total, screenshot=shot(f"ambiguous-{kind}"),
                )
            if read.complete and not blockers:
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"Humana Find Care does not list {self._who(q)} in {scope} near "
                    f"{q.zip_code or 'the clinic'}. Searching {kind} {term!r} returned "
                    f"{read.total} result(s), Humana's own results header echoed that term, all "
                    f"{len(read.cards)} of them are on screen, and no card names this provider "
                    f"(surname and first name as whole tokens; Humana prints no NPI on a card, so the "
                    f"check is by name){self._radius_note(read)}",
                    result_count=read.total, screenshot=shot(f"absent-{kind}"),
                )

        return self._undecided(reads, scope, blockers, pin, q, result, shot)

    # --- verdict assembly ---------------------------------------------------------------------------

    def _matched_verdict(self, matched: tuple[str, str], term: str, kind: str, read: _Read,
                         pin: _Pin, blockers: list[str], q: PortalQuery, result, shot):
        """Our provider's card is on screen. What that licenses depends on its own badge and the pin."""
        name, card = matched
        badge = _badge(card)
        npi_on_card = bool(q.npi and q.npi in card)
        how = ("surname + first name as whole tokens"
               + (f", and NPI {q.npi} appears in the card text" if npi_on_card else ""))

        if badge == "out":
            # Humana's own label contradicts presence. Negatives are read before positives (see
            # `_badge`) precisely so this branch is reachable at all.
            if blockers:
                return result(
                    PortalStatus.UNKNOWN,
                    f"Humana Find Care lists {name!r} for {kind} {term!r} and labels that card "
                    f"out-of-network, but the absence/label reading cannot be trusted for this "
                    f"member's network: {'; '.join(blockers)}.",
                    result_count=read.total, matched_name=name, screenshot=shot(f"badge-out-{kind}"),
                )
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"Humana Find Care lists {name!r} in {pin.name!r} but labels the card out-of-network "
                f"— the payer's own explicit label on this provider, matched by {how}.",
                result_count=read.total, matched_name=name, screenshot=shot(f"badge-out-{kind}"),
            )

        if not pin.name:
            return result(
                PortalStatus.UNKNOWN,
                f"Humana Find Care lists {name!r} for {kind} {term!r} in its all-plans-and-networks "
                f"directory, but plan {q.plan!r} did not identify one of the networks offered here "
                f"({pin.why}), so we cannot tell whether this listing is in the member's network. "
                f"Presence in some Humana network is not proof of this plan's network.",
                result_count=read.total, matched_name=name, screenshot=shot(f"match-unpinned-{kind}"),
            )

        caveat = "" if pin.confirms else (
            f" The pin is names-only, not identifier-grade ({pin.why}), so this says the provider is "
            f"in {pin.name!r} — the closest match to plan {q.plan!r} among Humana's own network list — "
            f"rather than proving that is the member's exact network.")
        label = self._badge_words(card)
        return result(
            PortalStatus.IN_NETWORK,
            f"Humana Find Care lists {name!r} in network {pin.name!r} near "
            f"{q.zip_code or 'the clinic'}, found by searching {kind} {term!r} among "
            f"{read.total if read.total is not None else len(read.cards)} result(s) and matched on "
            f"{how}."
            + (f" The card carries Humana's own {label!r} label." if label else "")
            + caveat,
            result_count=read.total, matched_name=name, screenshot=shot(f"match-{kind}"),
        )

    def _undecided(self, reads: list[_Read], scope: str, blockers: list[str], pin: _Pin,
                   q: PortalQuery, result, shot) -> PortalCapture:
        """No card matched and nothing licensed an absence. Say exactly which requirement failed."""
        if not reads:
            return result(
                PortalStatus.UNKNOWN,
                f"Humana Find Care never accepted a search for {self._who(q)} in {scope} — the search "
                f"box or its submit control did not respond, so NO search was performed. This is a "
                f"failure to search, not an empty result set, and says nothing about the network.",
                screenshot=shot("search-not-performed"),
            )
        widest = max((r.total or 0) for r in reads)
        seen = max(len(r.cards) for r in reads)
        no_echo = [r.term for r in reads if r.populated and not r.echoed]

        if no_echo:
            return result(
                PortalStatus.UNKNOWN,
                f"Humana Find Care showed results in {scope} but its own results header did not echo "
                f"the term(s) we searched ({', '.join(repr(t) for t in no_echo)}), so the list on "
                f"screen cannot be shown to be this lookup's rather than a previous or unfiltered one. "
                f"Absence from a list we cannot attribute to our query is not evidence.",
                result_count=widest or None, screenshot=shot("term-not-echoed"),
            )
        silent = [r for r in reads if r.cards and r.total is None]
        if silent:
            return result(
                PortalStatus.UNKNOWN,
                f"Humana Find Care rendered {max(len(r.cards) for r in silent)} card(s) in {scope} "
                f"without stating its own result total (the results header "
                f"'N in network results for “term”' was not readable), so there is no way to tell "
                f"whether that page is the whole result set. Completeness is never inferred from the "
                f"number of cards rendered — that inference is what makes a paginated list look "
                f"complete — so this is not a verdict.",
                result_count=None, screenshot=shot("total-unknown"),
            )
        if widest and seen < widest:
            return result(
                PortalStatus.UNKNOWN,
                f"Humana Find Care returned up to {widest} result(s) for {self._who(q)} in {scope} "
                f"near {q.zip_code or 'the clinic'} but renders one page at a time, and only {seen} "
                f"card(s) were on screen; this provider is not among those. Absence from part of a "
                f"{widest}-result list is not evidence of out-of-network, and we do not page through a "
                f"payer directory — this row needs a narrower search or a manual check.",
                result_count=widest, screenshot=shot("partial-page"),
            )
        if widest and blockers:
            return result(
                PortalStatus.UNKNOWN,
                f"Humana Find Care returned {widest} result(s) in {scope} near "
                f"{q.zip_code or 'the clinic'} and none of them is {self._who(q)}, but that absence "
                f"cannot be read as out-of-network: {'; '.join(blockers)}. Absence from a network we "
                f"cannot show is the member's own is not evidence about the member's network.",
                result_count=widest, screenshot=shot("absent-unlicensed"),
            )
        return result(
            PortalStatus.UNKNOWN,
            f"Humana Find Care returned no results at all for {self._who(q)} in {scope} near "
            f"{q.zip_code or 'the clinic'}"
            + (f" (searched {', '.join(repr(r.term) for r in reads)}"
               + (f", radius {reads[-1].radius}" if reads[-1].radius else "") + ")")
            + ". An empty result set cannot distinguish out-of-network from a search that does not "
              "answer for this name here, so it is not a verdict"
            + ("" if pin.name else f", and no network was pinned in any case ({pin.why})") + ".",
            result_count=0, screenshot=shot("no-results"),
        )

    def _absence_blockers(self, pin: _Pin, walk: _Walk) -> list[str]:
        """Everything that must hold before an absence may be called OUT_OF_NETWORK. Empty list = it may.

        This is the doctrine made executable, and it is deliberately conservative in exactly the places
        the earlier version was not: no pin, a names-only pin, a duplicated network name, or a portal
        scoped to somewhere other than the clinic's ZIP each make absence unreadable.
        """
        out: list[str] = []
        if not pin.name:
            out.append(f"no network was pinned ({pin.why}), so absence is absence from an unknown scope")
        elif not pin.confirms:
            out.append(
                f"the plan matched network {pin.name!r} on names only, not on a plan identifier "
                f"({pin.why}); names do not identify a plan, so this may not be the member's network")
        if pin.duplicate:
            out.append(
                f"Humana lists more than one network called {pin.name!r} (its own list repeats display "
                f"names across different networkIds), so which one was searched cannot be established")
        if not walk.geo_ok:
            out.append(
                f"the portal's own address bar says it searched {walk.geo_echo or 'an unknown location'}"
                f", which does not contain the clinic ZIP — an absence may just be the wrong market")
        return out

    # --- steps -------------------------------------------------------------------------------------

    def _who(self, q: PortalQuery) -> str:
        name = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        return f"{name} (NPI {q.npi})" if name else f"NPI {q.npi}"

    def _radius_note(self, read: _Read) -> str:
        return f" (Humana's own scope: {read.radius})." if read.radius else "."

    def _badge_words(self, card: str) -> str | None:
        low = (card or "").lower()
        for phrase in (*_NEGATIVE_BADGES, *_POSITIVE_BADGES):
            if phrase in low:
                return phrase
        return None

    def _search_terms(self, q: PortalQuery):
        """The bare surname first, then the full name.

        Surname first because it is the query whose result set can be *complete* (Humana states its own
        total, so a small surname set can be read in full) and therefore the only one that can license
        an absence. The full name follows as a narrowing attempt: when the surname set is too big to
        read in one page, a full-name search may still surface our provider and settle an IN.

        The NPI is deliberately never searched: this review did not establish whether Humana indexes
        it, and an unindexed identifier would manufacture an empty result set that means nothing.
        """
        terms: list[tuple[str, str]] = []
        if q.provider_last_name:
            terms.append((q.provider_last_name, "surname"))
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        if full and full != (q.provider_last_name or ""):
            terms.append((full, "full name"))
        return terms

    def _walk_to_networks(self, page: Page, q: PortalQuery) -> _Walk:
        """entry → location → Continue → Medical → /medical?view=networks.

        The returned `_Walk` names the step that failed, so a partial walk can never be mistaken for a
        completed one, and carries the portal's own echo of the location it actually scoped to.
        """
        w = _Walk()
        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError) as e:
            w.steps.append(f"navigation failed ({type(e).__name__})")
            return w
        self._settle(page, 4_000)
        blocked = self._refusal(page)
        if blocked:
            w.blocked = blocked
            w.steps.append(f"refused at the entry URL: {blocked}")
            return w
        self._dismiss_overlays(page)
        w.steps.append("guest landing")

        committed = self._commit_location(page, q)
        if not committed:
            w.steps.append("location not accepted")
            return w
        w.steps.append(f"location typed: {committed}")

        if not self._click_first(page, (_LOC_CONTINUE,)):
            w.steps.append("Continue not clickable")
            return w
        self._settle(page, 4_000)
        self._dismiss_overlays(page)  # a fresh consent layer can appear on the second view

        if not self._click_first(page, _MEDICAL, fallback_text="Medical"):
            w.blocked = self._refusal(page)
            w.steps.append("type of care 'Medical' not clickable")
            return w
        self._settle(page, 6_000)
        w.steps.append("care: Medical")

        # The portal echoes the location it actually accepted into its own utility bar, and that echo —
        # not what we typed — is what scoped the network list and every search. Read AFTER the medical
        # module loads, because that is where the bar lives.
        w.geo_echo = self._text_of(page, _LOC_SHOWN).replace("\n", " ").strip()
        w.geo_ok = bool(q.zip_code) and q.zip_code in w.geo_echo
        if w.geo_echo:
            w.steps.append(f"portal scoped to: {w.geo_echo}"
                           + ("" if w.geo_ok else " (!! does not contain the clinic ZIP)"))

        if "sessionError" in page.url:
            w.session_error = True
            w.steps.append("Humana reported a guest-session error (view=sessionError)")
            return w
        try:
            page.locator(_NET_INPUT).first.wait_for(state="visible", timeout=30_000)
        except (PlaywrightTimeout, PlaywrightError):
            w.blocked = self._refusal(page)
            w.steps.append("network-selection step never rendered")
            return w
        w.steps.append("network selection")
        w.reached = True
        return w

    def _commit_location(self, page: Page, q: PortalQuery) -> str | None:
        """Type the clinic's city/state/ZIP and commit it, returning what we typed.

        Google Places is the trap here: a suggestion is only trusted when it actually contains the ZIP
        we asked for. Otherwise we commit the raw text with Enter, which the portal accepts. Whatever
        happens, the portal's own echo (read by the caller) is what decides whether an absence counts.
        """
        for typed in self._location_terms(q):
            try:
                inp = page.locator(_LOC_INPUT).first
                inp.wait_for(state="visible", timeout=20_000)
                inp.click()
                inp.fill("")
                inp.type(typed, delay=110)  # real keystrokes: the Places request is keyup-driven
            except (PlaywrightTimeout, PlaywrightError):
                continue
            page.wait_for_timeout(4_000)  # Places debounces, then repaints the list more than once

            try:
                items = page.locator(f"{_LOC_ITEM}:visible")
                texts = [items.nth(i).inner_text() or "" for i in range(min(items.count(), 10))]
            except PlaywrightError:
                texts = []
            idx = next((i for i, t in enumerate(texts) if self._is_our_place(t, q)), None)
            if idx is None:
                continue  # try the next form before giving up on the suggestion route
            try:
                page.locator(f"{_LOC_ITEM}:visible").nth(idx).click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            page.wait_for_timeout(1_500)
            return typed

        # No form produced a suggestion we trust. Commit raw text with Enter, which this portal
        # accepts — that was the ORIGINAL behaviour and it is load-bearing: Places is intermittent
        # here, and on a quiet response the suggestion list is simply empty. Removing this fallback
        # turned a flaky-but-working walk into "location not accepted", which is the regression this
        # comment exists to prevent someone repeating.
        #
        # Enter the ZIP rather than the old "ST ZIP": the ZIP is the form Places validates, and
        # whether the portal actually landed near the clinic is not decided here anyway — the caller
        # reads the portal's OWN echo into `geo_ok`, and `_absence_blockers` refuses an OON when that
        # echo does not carry the clinic ZIP.
        fallback = (q.zip_code or "").strip() or next(iter(self._location_terms(q)), "")
        if not fallback:
            return None
        try:
            inp = page.locator(_LOC_INPUT).first
            inp.click()
            inp.fill("")
            inp.type(fallback, delay=110)
            page.wait_for_timeout(2_500)
            inp.press("Enter")
        except (PlaywrightTimeout, PlaywrightError):
            return None
        page.wait_for_timeout(1_500)
        return fallback

    def _location_terms(self, q: PortalQuery) -> list[str]:
        """The forms of this location to try, most reliable first.

        "ST ZIP" was the ONLY form tried, and Google Places rejects it: the live field showed
        `FL 33618` with "Enter a valid address, city and state, or ZIP code." Places wants a ZIP on
        its own, or a properly comma-separated "City, ST". The bare ZIP is safe here even though
        Places reads one as a house number, because `_is_our_place` refuses a suggestion whose ZIP is
        not the one we asked for — that guard is what "ST ZIP" was working around, and it was never
        needed.
        """
        state = (q.state or "").strip()[:2].upper()
        city = (q.city or "").strip()
        terms: list[str] = []
        for candidate in (
            f"{city}, {state} {q.zip_code}" if city and state and q.zip_code else None,
            q.zip_code,
            f"{city}, {state}" if city and state else None,
            city,
            f"{state} {q.zip_code}" if state and q.zip_code else None,  # the old form, last
            state,
        ):
            c = (candidate or "").strip().strip(",")
            if c and c not in terms:
                terms.append(c)
        return terms

    def _is_our_place(self, text: str, q: PortalQuery) -> bool:
        """Is this Places suggestion actually the clinic's ZIP?

        A naive `zip in text` test is unsafe: Places reads a bare ZIP as a HOUSE NUMBER, so a "33618"
        query can produce "33618 Samuel Ivy Drive, Tampa, FL 33619" — the digits are present but the
        ZIP is 33619. So the ZIP must appear somewhere other than the very start of the string, and the
        state must agree when we know it.
        """
        if not q.zip_code:
            return False
        if not any(m.start() > 0 for m in re.finditer(re.escape(q.zip_code), text)):
            return False
        if q.state and not re.search(rf"\b{re.escape(q.state)}\b", text, re.I):
            return False
        return True

    def _network_list(self, page: Page) -> list[dict]:
        """Every network Humana offers for this location, from its own session store.

        `sessionStorage.FCMEDICALSESSIONSTORE.networks.current` is the authoritative list (verified:
        12 rows for Thornton CO, agreeing with the DOM). Group headers arrive as entries with
        networkId 0 ("Medicare networks"); they are consumed as the line-of-business label for the rows
        that follow, never offered as selectable networks.
        """
        try:
            raw = page.evaluate(f"() => sessionStorage.getItem({_STORE_KEY!r})")
            rows = (json.loads(raw or "{}").get("networks") or {}).get("current") or []
        except (PlaywrightError, ValueError, AttributeError, TypeError):
            rows = []
        out: list[dict] = []
        group: str | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = (row.get("networkName") or "").strip()
            if not name:
                continue
            if not row.get("networkId"):  # networkId 0 → a section header, not a selectable network
                group = name
                continue
            out.append({"name": name, "group": group, "id": row.get("networkId")})
        if out:
            return out
        # Store unreadable: fall back to the (possibly partial) DOM list, marking the group unknown so
        # the line-of-business narrowing has less to work with rather than more.
        try:
            loc = page.locator(f"{_NET_PICKABLE}:visible")
            rows_dom = [(loc.nth(i).inner_text() or "").strip() for i in range(min(loc.count(), 40))]
        except PlaywrightError:
            return []
        return [{"name": t, "group": None, "id": None} for t in rows_dom if t]

    def _pick_network(self, networks: list[dict], q: PortalQuery) -> _Pin:
        """The network the 271's plan identifies, via the shared `plan_match.match_plan()`.

        Deliberately NOT a bespoke scorer. The previous version scored shared words itself, which is
        the CareFlex mis-pin plan_match.py was written to stop: every Humana MA network shares
        "Medicare", so word overlap picks a product type the member may not have (an HMO for a PPO
        member) and then licensed an out-of-network reading off it. Here `confirms` — an identifier
        match — is the only thing that licenses an absence, and a names-only match pins the search
        while saying so.
        """
        if not networks:
            return _Pin(None, "the portal offered no networks")
        if not q.plan:
            return _Pin(None, "no plan came from the 271")

        pool = self._same_line_of_business(networks, q)
        labels = [n["name"] for n in pool]
        m = match_plan(q.plan, labels)
        if m is None:
            shown = ", ".join(labels[:8]) or "—"
            return _Pin(None, f"plan {q.plan!r} matched none of Humana's networks distinctively: {shown}")

        # Product-type veto, applied only to the choice plan_match already made. A plan that names PPO
        # cannot be served by a network that names HMO, whatever words they share.
        want_types, got_types = _product_types(q.plan), _product_types(m.label)
        if want_types and got_types and not (want_types & got_types):
            return _Pin(
                None,
                f"the best match {m.label!r} names product type(s) {'/'.join(sorted(got_types))} while "
                f"the plan names {'/'.join(sorted(want_types))} — a different product, not a near miss")

        duplicate = sum(1 for n in networks if n["name"] == m.label) > 1
        return _Pin(m.label, m.basis, confirms=m.confirms_network, duplicate=duplicate)

    def _same_line_of_business(self, networks: list[dict], q: PortalQuery) -> list[dict]:
        """Narrow to the group header ("Medicare networks" / "Medicaid networks") the plan belongs to.

        Uses the domain's own classifier so this driver and the network resolver can never disagree
        about what line a plan is. When no group matches we keep them all rather than dropping
        candidates — a wider pool makes plan_match's uniqueness test stricter, which is the safe
        direction.
        """
        from network_probe.domain.line_of_business import line_of_business

        lob = line_of_business(q.plan or "", None)
        wanted = {"medicare": "medicare", "dual": "medicare", "medicaid": "medicaid",
                  "commercial": "commercial"}.get(lob)
        if not wanted:
            return networks
        pool = [n for n in networks if wanted in (n.get("group") or "").lower()]
        return pool or networks

    def _commit_network(self, page: Page, name: str) -> bool:
        """Choose `name` in the combobox and press Select, then verify the portal really pinned it.

        The verification is against the portal's own state, not against "something rendered": Humana's
        `selectedNetwork` DEFAULTS to the group header row ("Medicare networks", networkId 0), so
        pressing Select without picking a row leaves a plausible-looking selection that searches
        nothing. The store's selected name must EQUAL the network we asked for and carry a real id.
        """
        try:
            # `.v-field__input` sits over the input and swallows pointer events, so a plain click times
            # out with "intercepts pointer events"; force the click on the input itself.
            page.locator(_NET_INPUT).first.click(force=True)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        page.wait_for_timeout(2_000)

        if not (self._click_network_row(page, name) or self._scroll_then_click(page, name)
                or self._filter_then_click(page, name)):
            return False
        page.wait_for_timeout(1_500)
        if not self._click_first(page, (_NET_SELECT_BTN,)):
            return False
        self._settle(page, 6_000)
        if "sessionError" in page.url:
            return False
        try:
            sel = page.evaluate(
                f"() => {{ const s = JSON.parse(sessionStorage.getItem({_STORE_KEY!r}) || '{{}}')"
                ".selectedNetwork || {}; return [s.networkName || '', s.networkId || 0]; }")
            store_name, store_id = (sel or ["", 0])[0], (sel or ["", 0])[1]
        except (PlaywrightError, IndexError, TypeError):
            store_name, store_id = "", 0
        if store_name and store_id and _norm(store_name) == _norm(name):
            return True
        # Store unreadable (or shaped differently): fall back to the utility bar, which prints the
        # pinned network name. Compared to the name we asked for — never merely tested for existence.
        shown = self._text_of(page, _NET_SHOWN)
        return bool(shown) and _norm(name) == _norm(shown)

    def _click_network_row(self, page: Page, name: str) -> bool:
        """Click the row whose label is exactly `name`, among the selectable (non-header) rows."""
        try:
            rows = page.locator(f"{_NET_PICKABLE}:visible")
            for i in range(min(rows.count(), 60)):
                if (rows.nth(i).inner_text() or "").strip() == name:
                    rows.nth(i).click(timeout=8_000)
                    return True
        except (PlaywrightTimeout, PlaywrightError):
            return False
        return False

    def _filter_then_click(self, page: Page, name: str) -> bool:
        """Type the network name in case the combobox narrows to it, then click the row.

        Last resort: typing did NOT filter the CO list, so this is only for markets where it might.
        """
        try:
            page.locator(_NET_INPUT).first.type(name, delay=90)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        page.wait_for_timeout(2_500)
        return self._click_network_row(page, name)

    def _scroll_then_click(self, page: Page, name: str) -> bool:
        """Walk the list window until the row exists in the DOM.

        Kept for markets where the list virtualises: a network 30 rows down would simply not be
        rendered, and no selector can reach a row that does not exist.
        """
        for _ in range(20):
            try:
                box = page.locator(f"{_NET_ROW}:visible").first.bounding_box()
                if not box:
                    return False
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.wheel(0, 260)
            except (PlaywrightTimeout, PlaywrightError):
                return False
            page.wait_for_timeout(400)
            if self._click_network_row(page, name):
                return True
        return False

    def _continue_without_network(self, page: Page) -> bool:
        """Take Humana's own "view provider results for all plans & networks" escape hatch.

        This can only ever produce UNKNOWN (see `_absence_blockers`): it is here for the screenshot and
        for a possible presence signal, not for a verdict.
        """
        try:
            page.locator(_NET_INPUT).first.press("Escape")  # close the combobox overlay first
        except (PlaywrightTimeout, PlaywrightError):
            pass
        page.wait_for_timeout(800)
        if not self._click_first(page, (_NET_SKIP_BTN,), force=True):
            return False
        self._settle(page, 6_000)
        return "sessionError" not in page.url

    def _run_search(self, page: Page, term: str, q: PortalQuery, trail: list[str]) -> _Read | None:
        """Search one term in the current scope and read the result page.

        Returns None when the search could not be *performed* — a failure to search, which the caller
        must never confuse with an empty result set.
        """
        self._use_name_tab(page, trail)
        try:
            si = page.locator(_SEARCH_INPUT).first
            si.wait_for(state="visible", timeout=25_000)
            si.click(force=True)
            si.fill("")
            si.type(term, delay=90)
        except (PlaywrightTimeout, PlaywrightError):
            return None
        page.wait_for_timeout(2_000)
        # The suggestion panel under the box opens over the submit/Update button and a normal click on
        # it is intercepted (a 45s timeout, verified). Close the panel, then force the click.
        try:
            si.press("Escape")
        except (PlaywrightTimeout, PlaywrightError):
            pass
        page.wait_for_timeout(500)
        if not self._click_first(page, _SEARCH_BTNS, force=True, timeout_ms=10_000):
            return None
        self._settle(page, 7_000)
        self._dismiss_overlays(page)  # the survey modal lands on the results view, over the pager

        read = self._read_results(page, term)
        # Humana defaults to a 15-mile radius. Our provider practises AT the clinic address, but a
        # payer that lists only another of his sites would fall outside it and the empty set would look
        # like an absence. So take Humana's own "Expand distance to 50 miles" before reading anything
        # into an empty page.
        if read.total == 0 and self._click_first(page, (_RESULTS_EXPAND,), timeout_ms=5_000):
            self._settle(page, 6_000)
            trail.append("expanded the radius to 50 miles")
            read = self._read_results(page, term)
        if read.total is not None and read.total > len(read.cards) and self._widen_page(page):
            trail.append(f"widened the page to {_PER_PAGE_MAX} results")
            read = self._read_results(page, term)
        return read

    def _read_results(self, page: Page, term: str) -> _Read:
        """Read one results view: Humana's stated total, its term echo, and the cards actually seen."""
        summary = self._text_of(page, _RESULTS_SUMMARY)
        empty = self._text_of(page, _RESULTS_EMPTY)
        total = _parse_total(summary)
        if total is None and _NO_RESULTS_RE.search(empty):
            total = 0
        cards, cards_ok = self._cards(page)
        return _Read(
            term=term, total=total, cards=cards, cards_ok=cards_ok,
            echoed=_echoes_term(summary or empty, term),
            radius=self._text_of(page, _RESULTS_DISTANCE).replace("\n", " ").strip(),
        )

    def _use_name_tab(self, page: Page, trail: list[str]) -> None:
        """Switch "Search by:" to Name when the tab strip is present.

        A name-scoped search is what a staffer does and is narrower than the default All tab (which
        also matches specialties and facilities); a narrower set is what lets a complete page be read.
        Best-effort and self-reverting: if the Name tab replaces the single search box with something
        else we click back to All rather than leave the flow stranded. Whatever happens, the tab that is
        actually in force is recorded — it is part of what the verdict means.
        """
        used = "All (default)"
        try:
            tab = page.locator(_SEARCH_TABS).get_by_text("Name", exact=True).first
            if tab.count():
                tab.click(timeout=8_000)
                page.wait_for_timeout(1_500)
                if page.locator(_SEARCH_INPUT).count():
                    used = "Name"
                else:
                    page.locator(_SEARCH_TABS).get_by_text("All", exact=True).first.click(timeout=8_000)
                    page.wait_for_timeout(1_500)
                    used = "All (Name tab did not keep a single search box)"
        except (PlaywrightTimeout, PlaywrightError):
            used = "All (Name tab not clickable)"
        trail.append(f"search by: {used}")

    def _widen_page(self, page: Page) -> bool:
        """Ask for 30 results per page. One click on Humana's own pager — not paging through results."""
        try:
            opt = page.locator(_PER_PAGE).get_by_text(_PER_PAGE_MAX, exact=True).first
            if not opt.count():
                return False
            opt.click(timeout=8_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 5_000)
        return True

    def _cards(self, page: Page) -> tuple[list[str], bool]:
        """The provider cards, and whether that read can be trusted as complete.

        Humana ids each card with its numeric provider-location id (e.g. `<div id="31193931052395">`),
        so "descendant of the results list whose id is all digits" identifies exactly the cards: 20
        nodes for 20 cards, none nested inside another (verified). The id filter matters because the
        results HEADER — "59 in network results for “Smith”" — is itself a child of that same
        container, and a selector that picked it up would count UI chrome as a provider card and could
        even match our surname inside the header's echo of the search term.

        The boolean is the second half of the contract: any failure, or hitting the cap, returns False
        so the caller can never treat a partial read as a complete one. A swallowed exception that
        returned a short list as if it were the whole page is how an absence check gets falsified.
        """
        try:
            texts = page.eval_on_selector_all(
                f"{_RESULTS_LIST} [id]",
                """els => {
                    const cards = els.filter(e => /^[0-9]+$/.test(e.id));
                    return cards
                        .filter(e => !cards.some(o => o !== e && o.contains(e)))
                        .filter(e => e.offsetParent !== null)
                        .map(e => e.innerText || '');
                }""",
            )
        except PlaywrightError:
            return [], False
        cards = [t for t in texts if t.strip()]
        if len(cards) > _MAX_CARDS:
            return cards[:_MAX_CARDS], False
        return cards, True

    def _match(self, cards: list[str], q: PortalQuery) -> tuple[tuple[str, str] | None, str]:
        """Our provider among the cards: ((name line, card text) or None, reason).

        Identity comes from the QUERY, never from the search term — a bare-surname term with the
        expected name derived from it would make every same-surname provider a match. Two sibling
        drivers shipped that and turned a stranger into an IN_NETWORK verdict.

        Matching is by WHOLE TOKEN on the card's name line, because:
          * substring surname matching is unsound ("Shah" is inside "Shahid");
          * substring first-name matching is worse — a query for Kush Shah would match the card
            "Shah, Kushal MD", a different person, since "kush" is a substring of "kushal";
          * the name line is used rather than the whole card because a card also carries the group,
            street and hospital, any of which can contain a surname without naming that person.

        Nothing that cannot be resolved is allowed to decide. A card whose given name is only an
        initial ("Roulhac, M MD"), or merely a longer spelling of ours ("Shah, Kushal" for Kush), or
        whose surname appears somewhere in the card but not in its name line, returns "ambiguous" — the
        caller answers UNKNOWN, because returning it would be a false IN and calling it absent would be
        a false OON while our provider may be that very row.

        Which token is the GIVEN name matters, and Humana's format settles it: every card observed live
        reads "Last, First M CRED". Treating any single-letter token as a possible first initial is too
        blunt — the live Tampa search for "Shah" returned "Shah, Anna K MD", whose MIDDLE initial K
        collided with Kush and made a plainly different person ambiguous, which would suppress a
        legitimate absence reading. So the initial test applies to the token in the given-name position
        only, and falls back to the blunt (safer) test when the card is not in "Last, First" form.
        """
        want_last = _tokens(q.provider_last_name)
        if not want_last:
            return None, "no surname to match"
        want_first = _tokens(q.provider_first_name)
        ambiguous = False
        for card in cards:
            line = (card.strip().splitlines() or [""])[0].strip()
            got = _tokens(line)
            if not (want_last <= got):
                # The surname is nowhere in the name line. If it turns up elsewhere in the card the
                # layout may not be what we think it is, so decline rather than call this an absence.
                if want_last <= _tokens(card):
                    ambiguous = True
                continue
            if not want_first:
                ambiguous = True  # surname agrees and we have nothing to separate namesakes by
                continue
            others = got - want_last
            if want_first & others:
                return (line[:120], card), "surname + first name"
            given = self._given_name(line, want_last)
            # Only the given-name token can make a namesake ambiguous. When the format gives us no
            # given-name token, every remaining token is treated as a candidate instead.
            candidates = {given} if given else others
            if {t for t in candidates if len(t) == 1} & {f[0] for f in want_first if f}:
                ambiguous = True  # "Roulhac, M" could be Maurice or Michael
                continue
            if any(o.startswith(f) or f.startswith(o)
                   for f in want_first for o in candidates if len(o) > 1):
                ambiguous = True  # "Kushal" vs "Kush": a different person, or a fuller spelling
        return None, "ambiguous" if ambiguous else "no card matched"

    def _given_name(self, line: str, want_last: set[str]) -> str | None:
        """The token in the given-name position of a "Last, First M CRED" card, or None.

        None means "this card is not in a form that tells us which token is the first name" (e.g. the
        live outlier "Vipul Shah MD PA", a practice listing), and the caller then falls back to the
        more conservative reading rather than assuming a position.
        """
        if "," not in line:
            return None
        surname, _, rest = line.partition(",")
        if not want_last <= _tokens(surname):
            return None  # the comma is not where we think it is
        rest_tokens = [t for t in re.split(r"[^A-Za-z0-9]+", rest.upper()) if t]
        return rest_tokens[0] if rest_tokens else None

    # --- plumbing ----------------------------------------------------------------------------------

    def _refusal(self, page: Page) -> str:
        """An interactive challenge or a hard refusal, described — or "" when neither is on screen.

        Matched against visible text only, so a dormant bot-protection script is never called a block.
        Nothing here attempts to satisfy, solve or evade anything: the caller reports BLOCKED and the
        payer routes to its public FHIR directory.
        """
        text = self._page_text(page).lower()
        hit = next((p for p in _CHALLENGE_TEXT if p in text), None)
        if hit:
            return f"an interactive bot challenge is being shown (visible text matched {hit!r})"
        hit = next((p for p in _WAF_TEXT if p in text), None)
        if hit:
            return f"the edge refused the request (visible text matched {hit!r})"
        return ""

    def _click_first(self, page: Page, selectors: tuple[str, ...], fallback_text: str | None = None,
                     force: bool = False, timeout_ms: int = 12_000) -> bool:
        """Click the first selector that resolves; optionally fall back to the visible label.

        The click and the settle are deliberately separate: wrapping both in one try/except is how the
        UHC driver once decided a click had failed *after* it had already navigated, because networkidle
        timed out. A settle can never invalidate a click here. The click carries its own timeout so an
        intercepted control costs seconds rather than the 45s context default.
        """
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click(force=force, timeout=timeout_ms)
                return True
            except (PlaywrightTimeout, PlaywrightError):
                continue
        if fallback_text:
            # Last resort, and genuinely risky: loose text can match a heading instead of the control.
            # Exact-text only, which is what makes it safe enough for the <nucleus-button> cards (they
            # expose no button role, so get_by_role never matches them).
            try:
                loc = page.get_by_text(fallback_text, exact=True).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click(force=force, timeout=timeout_ms)
                return True
            except (PlaywrightTimeout, PlaywrightError):
                return False
        return False

    def _dismiss_overlays(self, page: Page) -> None:
        """Close consent / survey layers. Close buttons are matched only *inside* a known overlay
        container — a bare `[aria-label*=Close]` would also match the results map's own controls."""
        for sel in _OVERLAYS:
            try:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    b.click(timeout=6_000)
                    page.wait_for_timeout(800)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _settle(self, page: Page, pause_ms: int = 3_000) -> None:
        """Best-effort wait for the next step. Never raises, and a settle timeout must not be
        mistaken for a step that failed.

        Was `networkidle` at 12s. Profiled live 2026-07-31: the network was still busy on
        2 of 8 steps, costing ~15s of a 96.9s walk. On the rest it was already quiet, so
        the DOM-quiescence poll in `browser.settle` returns just as fast there while bounding
        the busy steps. The `pause_ms` values are a separate, unmeasured cost -- left alone.
        """
        pb.settle(page, pause_ms)

    def _text_of(self, page: Page, selector: str) -> str:
        try:
            loc = page.locator(selector).first
            return (loc.inner_text() or "").strip() if loc.count() else ""
        except PlaywrightError:
            return ""

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
