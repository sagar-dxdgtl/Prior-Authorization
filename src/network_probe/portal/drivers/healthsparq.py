"""HealthSparq / Kyruus find-a-doctor — the shared Blues directory platform, driven for AZ Blue.

Covers the Ins Test 3 row "BCBS Arizona AZ" (Desir, Hedson — NPI 1346866332 — United Vein Centers,
Peoria AZ 85382) on the BlueCard **Statewide / National PPO** network. HealthSparq powers many Blues
plans behind one URL shape (`<payer>.healthsparq.com/healthsparq/public/#/one/…`), so this driver is
parameterised by `HealthSparqSite` rather than hardcoding AZ Blue: a new HealthSparq payer needs a
config entry, not a new driver.

WHY the entry URL in targets.py cannot work (observed 2026-07-28)
----------------------------------------------------------------
`https://azblue.healthsparq.com/` renders 101 characters: "Oops! We weren't expecting you here.
Please log into your health plan's website to access this page." The HealthSparq app is *only*
reachable through a launch URL that carries the plan/network codes in the hash, and it 400s
("noParams=true") without them. Those launch URLs are published, one per network, by the payer's own
guest gate — for AZ Blue, `https://www.azblue.com/find-a-doctor/browse-the-network`, a Sitecore page
whose embedded JSON contains every (network label → launch URL) pair. The driver harvests that map and
picks the launch URL whose label matches the 271's plan string. THAT is what makes this plan-first:
the network is pinned by the payer's own published URL for that named network, before any search runs.

Observed flow (all selectors verified live 2026-07-28, headed Chromium)
----------------------------------------------------------------------
    www.azblue.com/find-a-doctor/browse-the-network    Sitecore JSON: "href"/"text" pairs ->
                                                       {network label: healthsparq launch URL}
    <host>/healthsparq/public/#/one/city=&state=AZ&insurerCode=…&productCode=PPO&brandCode=…&alphaPrefix=…
    [data-test='sbp-trigger']                          NETWORK chip -> "Statewide/National PPO…"
                                                       <- THE on-screen plan confirmation
    [data-test='button-welcome']  "Choose a location"  welcome wizard (location is mandatory)
      #input-location                                  "Enter an address, city or zip code"
      #suggestion-location-1  "Peoria, AZ 85382"       (see the suggestion-index trap below)
      button.location-confirm  "Yes, this is correct"   <- commits it; without this nothing searches
    [data-test='springboard-tile-button'] "Doctors by Name"
    [data-test='search-suggest-input']                 "Type a last name to learn if a doctor is in
                                                        your network"
    [data-test='search-input-suggestion']              the typeahead's in-network NAME INDEX — the
                                                        real result set for a name lookup
    Enter -> …/search/isPromotionSearch=               [data-test='provider-card'],
                                                       [data-test='pagination-details'] "Displaying
                                                       1-1 of 1", [data-test='local-tab']
                                                       "In-person care (139)"

Traps that each cost a live run, so a future reader does not re-derive them
--------------------------------------------------------------------------
1. HEADLESS IS REFUSED, HEADED IS NOT. Imperva Advanced Bot Protection serves the launch URL fine to
   headed Chromium (0 blocked responses) but 403s the app's own `/service/domain/plans` XHR under
   headless, leaving the SPA showing "We skipped a beat… Error code: 403". Reproduced 4/4 headless,
   never once headed. So this portal needs `PORTAL_HEADED=1`. Detected and reported as BLOCKED — never
   worked around: no stealth plugin, no fingerprint patching, no solver. Note `/service/login` itself
   returns 200 with the right network context, so the *plan pin* is fine; only the follow-up is refused.
2. THE UN-PINNED DIRECTORY ANNOUNCES ITSELF, AND IT OVER-INCLUDES. The "Do Not Know My Network" launch
   URL (no `productCode`) loads a working search whose NETWORK chip reads "Choose a network" and which
   banners "To view in-network results, choose a network first." It searches `productCode=all` — the
   union of every network the payer sells — so neither presence nor absence there is an answer. Proven,
   not assumed: on 2026-07-28 Hedson Desir (NPI 1346866332) was FOUND in that un-pinned view, card
   "1 network accepted", at the sheet's own clinic address, while being ABSENT from Statewide / National
   PPO — where Arthur Maydell, at the same practice, reads "14 in network". Reading an IN off the
   un-pinned surface would be exactly the flex.optum/Naar false-IN this whole layer exists to stop, so a
   match there returns UNKNOWN, never IN_NETWORK.
3. THERE IS NO NPI ANYWHERE IN THIS PORTAL. Advanced search exposes 18 filters and none is NPI, and the
   provider profile page prints no NPI (checked the rendered text *and* the HTML for 1992078745). So the
   match is by name, and NPI is deliberately never typed into the search box — it would return nothing
   and prove nothing.
4. `#suggestion-location-0` IS NOT ALWAYS THE ZIP. With the field empty, index 0 is "Use my current
   location"; after typing, index 0 is the ZIP and index 1 is "Use my current location". Blindly
   clicking index 0 hands the portal the *browser's* location. Skip any suggestion naming "current
   location".
5. THE PLATFORM USES `data-test`, NOT `data-testid`. `[data-testid]` matches exactly one element on the
   whole results page (an unrelated promo button).
6. DO NOT COUNT PAGE-1 CARDS AS THE RESULT SET. "Desir" returns 139 fuzzy matches with ~10 cards
   rendered; paging through them would breach the human-scale-volume rule anyway. The typeahead name
   index is the oracle for "is this person in this network" — that is also the surface the Test 2 manual
   check read. Cards are used only to *find* a match, never to prove absence.
7. THE ALPHA PREFIX IN THE LAUNCH URL IS NOT MEMBER DATA. `alphaPrefix=XBP` is the network's own prefix
   as AZ Blue publishes it on a public page; no member ID, DOB or name is ever sent.

Live results (2026-07-28, headed)
--------------------------------
* POSITIVE CONTROL — Arthur T Maydell, NPI 1992078745, ZIP 85032, Statewide / National PPO →
  card "Arthur T. Maydell, MD — United Vein Centers — 14 in network" → IN_NETWORK. Matches the Test 2
  manual verdict, which is what proves the matcher can actually detect an IN rather than only absences.
* SHEET ROW — Hedson Desir, NPI 1346866332, ZIP 85382, Statewide / National PPO → 6 in-network name
  suggestions and 139 results, none of them Desir → OUT_OF_NETWORK. Corroborated by trap 2: he IS in
  AZ Blue's provider file, just not in this network, so the absence is a real network exclusion and not
  a missing record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

# --- HealthSparq's own handles (data-test, not data-testid — see trap 5) ---------------------------
_NETWORK_CHIP = "[data-test='sbp-trigger']"
_WELCOME_BUTTON = "[data-test='button-welcome']"
_LOCATION_TRIGGER = "[data-test='location-finder-trigger']"
_LOCATION_TEXT = "[data-test='location-finder-trigger-text']"
_LOCATION_INPUT = "#input-location"
_LOCATION_SUGGESTION = "[id^='suggestion-location-']"
_LOCATION_CONFIRM = "button.location-confirm"
_SEARCH_INPUT = "[data-test='search-suggest-input']"
_SUGGESTION = "[data-test='search-input-suggestion']"
_PROVIDER_CARD = "[data-test='provider-card']"
_PAGINATION = "[data-test='pagination-details']"
_LOCAL_TAB = "[data-test='local-tab']"

# Dismissable banners, re-checked after each navigation because they re-appear. Deliberately short and
# specific: a loose label like "Continue" would happily click a load-bearing button in the wizard.
# "I understand" is the un-pinned view's "choose a network first" banner, observed 2026-07-28.
_OVERLAY_LABELS = ("I understand", "Accept")

# The SPA's own failure shells. All three are short and unmistakable; matching them keeps a refusal from
# being mistaken for "the provider is absent".
_REFUSAL_MARKERS = (
    "we skipped a beat",  # HealthSparq's error shell (carries "Error code: 403" / "400")
    "there was an error processing your request",
    "we weren't expecting you here",  # the bare host root, with no launch params
)

# Chip text that means "no network is pinned" — the un-pinned directory (trap 2).
_UNPINNED_CHIP = ("choose a network", "select a network", "network")


@dataclass(frozen=True)
class HealthSparqSite:
    """One HealthSparq tenant. Adding a payer is a config entry, not a new driver."""

    portal_key: str
    portal_name: str
    host: str  # e.g. "azblue.healthsparq.com"
    insurer_code: str  # HealthSparq tenant code, e.g. "BCBSAZ_I"
    state: str
    #: the payer's own guest gate that publishes one launch URL per network; harvested live
    network_gate_url: str | None = None
    #: (label, productCode, brandCode, alphaPrefix) — the same map, baked in as of 2026-07-28 so the
    #: driver still pins a network if the gate page is restyled. alphaPrefix is the NETWORK's, not a
    #: member's (trap 7); an empty string means the payer publishes none for that network.
    networks: tuple[tuple[str, str, str, str], ...] = ()
    #: launch tail with no productCode — loads a working but UN-PINNED directory (trap 2)
    generic_brand_code: str | None = None


# AZ Blue's medical networks, read out of the Sitecore JSON on
# www.azblue.com/find-a-doctor/browse-the-network on 2026-07-28. Dental brands (BCBSAZDENTAL) and the
# "Do Not Know My Network" entry are deliberately absent: the first is the wrong network for a physician
# check, the second is the un-pinned directory.
_AZBLUE_NETWORKS: tuple[tuple[str, str, str, str], ...] = (
    ("Statewide / National PPO", "PPO", "BCBSAZBLUE", "XBP"),
    ("Statewide/National PPO + Prosano", "PRS", "BCBSAZBLUE", ""),
    ("Statewide HMO", "HMO", "BCBSAZBLUE", "XBK"),
    ("Alliance HMO", "ALH", "BCBSAZBLUE", "XAH"),
    ("Alliance PPO / EPO", "ALN", "BCBSAZBLUE", "XBA"),
    ("Alliance PPO + Prosano", "APR", "BCBSAZBLUE", "S3C"),
    ("BlueHPN National EPO (In AZ: Alliance Network)", "HPN", "BCBSAZBLUE", "Z5M"),
    ("EPO", "PPO", "BCBSAZBLUE", "XBP"),
    ("CHS", "CHS", "BCBSAZBLUE", "XBP"),
    ("Blue Preferred Care Tiers", "SOA", "BCBSAZMAYO", "S3Z"),
    ("High Deductible Health Plan", "PP2", "BCBSAZMAYO", "SYD"),
    ("PimaConnect", "PMA", "BCBSAZBLUE", "PMA"),
    ("PimaConnect PPO and Prosano", "PPR", "BCBSAZBLUE", ""),
    ("Indemnity", "PAR", "BCBSAZBLUE", "XBC"),
    ("ACA Health Choice", "STH", "BCBSAZBLUE", "IAZ"),
    ("Focus", "FCS", "BCBSAZBLUE", "FZI"),
    ("Focus + Prosano", "FCP", "BCBSAZBLUE", "FPZ"),
    ("MaricopaFocus (Maricopa County)", "MCF", "BCBSAZBLUE", "FLH"),
    ("PimaFocus (Pima County)", "PMF", "BCBSAZBLUE", "FPO"),
    ("Neighborhood (All counties except for Maricopa and Pima)", "NBR", "BCBSAZBLUE", "NNJ"),
    ("Blue Best Life - Classic/Plus", "MDH", "AZMEDICARE", ""),
    ("BlueJourney PPO", "MPP", "AZMEDICARE", ""),
    ("Medicare Supplement Senior Preferred Medical", "SEN", "BCBSAZBLUE", "XBS"),
)

SITES: dict[str, HealthSparqSite] = {
    "azblue-healthsparq": HealthSparqSite(
        portal_key="azblue-healthsparq",
        portal_name="AZ Blue / HealthSparq",
        host="azblue.healthsparq.com",
        insurer_code="BCBSAZ_I",
        state="AZ",
        network_gate_url="https://www.azblue.com/find-a-doctor/browse-the-network",
        networks=_AZBLUE_NETWORKS,
        generic_brand_code="BCBSAZBLUE",
    ),
}

# The payer gate embeds its network links as Sitecore JSON, `&` escaped as &.
_GATE_LINK_RE = re.compile(
    r'"href"\s*:\s*"(?P<url>https://[^"]*?/healthsparq/public/\#/one/[^"]*)"\s*,\s*"text"\s*:\s*"(?P<label>[^"]*)"'
)


def _tokens(s: str | None) -> set[str]:
    """Distinctive tokens for plan/network matching. Length >= 3, not >= 4: "PPO"/"HMO"/"EPO" are the
    single most discriminating tokens in a Blues network name and a 4-char floor would drop them."""
    return {t for t in re.split(r"[^A-Za-z0-9]+", (s or "").upper()) if len(t) >= 3}


def _norm_name(s: str | None) -> list[str]:
    """Lowercase alphabetic name tokens: "Arthur T. Maydell, MD" -> ['arthur', 't', 'maydell', 'md']."""
    return [t for t in re.split(r"[^a-z]+", (s or "").lower()) if t]


_CHROME = re.compile(
    r"see all results|show all|showing\s+\d|^\s*\d+\s+(results?|providers?)|more (provider|result)|"
    r"no results|did you mean|search for|results for\b",
    re.I,
)


def _is_chrome(line: str) -> bool:
    """Portal UI text that echoes the query without naming a provider. It contains the surname, so a
    naive containment test accepts it as a match — verified: `_match(['See all results for Desir'],
    'Desir')` returned that string as the matched provider name."""
    return bool(_CHROME.search(line or ""))


class HealthSparqDriver(PortalDriver):
    # Imperva 403s the app's own /service/domain/plans XHR when headless: reproduced 4/4
    # headless vs 0/6 headed. Nothing is bypassed — headless simply does not get served.
    requires_headed = True
    key = "azblue-healthsparq"
    portal_name = "AZ Blue / HealthSparq"

    def __init__(self, portal_key: str = "azblue-healthsparq") -> None:
        try:
            self.site = SITES[portal_key]
        except KeyError as e:  # a config gap must fail loudly, not silently drive the wrong tenant
            raise ValueError(
                f"no HealthSparqSite configured for portal_key {portal_key!r}; "
                f"known: {sorted(SITES)}"
            ) from e
        self.key = self.site.portal_key
        self.portal_name = self.site.portal_name

    # --- the contract ------------------------------------------------------------------------------

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []
        site = self.site

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The trail is mandatory: a walk that stopped early and then reported OON is the worst
            # failure mode this layer can have, and recording every step makes it impossible to hide.
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        # --- 1. which network does this member's plan name? -------------------------------------
        networks = self._network_map(page, site)
        trail.append(f"{len(networks)} networks published by the payer gate")
        picked = self._best_network(networks, q.plan)
        if picked:
            label, launch = picked
            trail.append(f"network selected: {label}")
        elif site.generic_brand_code:
            # Still worth driving: seeing whether the payer lists this provider at ALL distinguishes
            # "excluded from the network" from "not in the payer's file", which is useful context for a
            # human. But nothing here can be decisive — this surface unions every network (trap 2), so
            # the only outcomes below are UNKNOWN.
            label, launch = "", self._generic_launch(site)
            trail.append(f"no network matched plan {q.plan!r} — falling back to the un-pinned directory")
        else:
            return result(
                PortalStatus.UNKNOWN,
                f"could not map plan {q.plan!r} onto any of {len(networks)} networks {self.portal_name} "
                f"publishes, and this platform has no un-pinned directory configured. Without a pinned "
                f"network a search proves nothing about NPI {q.npi}.",
                screenshot=shot("no-network-match"),
            )

        # --- 2. open the pinned launch URL -------------------------------------------------------
        try:
            page.goto(launch, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation to the launch URL failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))
        self._settle(page, 12_000)  # the Ember shell hydrates well after domcontentloaded
        trail.append("launch URL opened")

        refusal = self._refusal(page)
        if refusal:
            # Imperva ABP (trap 1). Reported, never defeated.
            return result(
                PortalStatus.BLOCKED,
                f"{self.portal_name} served its error shell ({refusal!r}) instead of the directory. "
                f"Verified 2026-07-28: this is Imperva Advanced Bot Protection refusing the app's own "
                f"/service/domain/plans XHR under HEADLESS Chromium — it does not happen headed. Re-run "
                f"with PORTAL_HEADED=1. No challenge was shown and none was bypassed.",
                screenshot=shot("blocked-shell"),
            )

        self._dismiss_overlays(page)
        pinned = self._pinned_network(page)
        plan_confirmed = bool(picked) and bool(pinned) and self._chip_agrees(pinned, label)
        if pinned:
            trail.append(f"NETWORK chip reads {pinned!r}")
        if plan_confirmed:
            trail.append("plan CONFIRMED on screen")
        else:
            trail.append("plan NOT confirmed — no OON may be reported")

        # --- 3. location (mandatory: the wizard blocks search until it is committed) --------------
        where = self._location_term(q)
        if not where:
            return result(
                PortalStatus.UNKNOWN,
                f"{self.portal_name} requires a search location before it will search, and this query "
                f"carried no ZIP, city or state for NPI {q.npi}.",
                screenshot=shot("no-location"),
            )
        if self._set_location(page, where):
            trail.append(f"location committed: {self._location_shown(page) or where}")
        else:
            return result(
                PortalStatus.UNKNOWN,
                f"could not commit the search location {where!r} in {self.portal_name}; every search on "
                f"this platform is location-scoped, so nothing was searched for NPI {q.npi}.",
                screenshot=shot("location-failed"),
            )
        self._dismiss_overlays(page)  # a fresh banner can appear after the wizard closes

        # --- 4. Doctors by Name -> search -------------------------------------------------------
        if not self._open_name_search(page):
            return result(
                PortalStatus.BLOCKED,
                f"the 'Doctors by Name' search never opened in {self.portal_name} — the shell did not "
                f"hydrate, or the platform changed.",
                screenshot=shot("no-search-input"),
            )
        trail.append("category: Doctors by Name")

        network_desc = label or f"the un-pinned {self.portal_name} directory"
        who = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p) or "the provider"
        for term, kind in self._search_terms(q):
            found, count, matched, suggestions = self._run_search(page, term, q)
            trail.append(f"searched {kind} {term!r} → {count} result(s), {suggestions} suggestion(s)")
            if found and plan_confirmed:
                return result(
                    PortalStatus.IN_NETWORK,
                    f"{self.portal_name} lists {matched!r} in {network_desc} within 30 miles of "
                    f"{where} — matched NPI {q.npi} ({who}) by {kind} {term!r}"
                    f"{self._accepted(page)}. Network pinned on screen as {pinned!r}.",
                    result_count=count, matched_name=matched, screenshot=shot(f"match-{kind}"),
                )
            if found:
                # A match in the UN-PINNED directory is NOT an in-network answer, and this is not
                # theoretical: on 2026-07-28 Hedson Desir (NPI 1346866332) was found in exactly this
                # un-pinned view of AZ Blue — card "1 network accepted" — while being absent from
                # Statewide / National PPO. The un-pinned view is productCode=all, a union of every
                # network the payer sells, so reading an IN off it is the same over-inclusion that made
                # flex.optum's FHIR call David Naar in-network when the member portal said otherwise.
                return result(
                    PortalStatus.UNKNOWN,
                    f"{self.portal_name} lists {matched!r} ({who}, NPI {q.npi}) within 30 miles of "
                    f"{where}{self._accepted(page)}, but the NETWORK chip read {pinned!r} — this is the "
                    f"un-pinned directory, a union of every network this payer sells. Presence there "
                    f"does not put the provider in the member's network (verified live: this same "
                    f"surface lists a provider who is absent from Statewide / National PPO), so the "
                    f"answer stays UNKNOWN until the plan can be mapped to a named network.",
                    result_count=count, matched_name=matched, screenshot=shot(f"match-unpinned-{kind}"),
                )
            if suggestions and plan_confirmed:
                # The typeahead is this platform's in-network NAME INDEX; present-but-not-ours is the
                # same evidence the Test 2 manual checks read. Card counts are NOT used to prove
                # absence (trap 6).
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"{self.portal_name} answered {suggestions} in-network name suggestion(s) and "
                    f"{count} result(s) for {kind} {term!r} in {network_desc} within 30 miles of "
                    f"{where}, and none is {who} (NPI {q.npi}) — out-of-network for this plan. Network "
                    f"pinned on screen as {pinned!r}. NB this portal indexes no NPI, so the match is "
                    f"by name.",
                    result_count=count, screenshot=shot(f"absent-{kind}"),
                )
            if suggestions:
                return result(
                    PortalStatus.UNKNOWN,
                    f"{self.portal_name} answered {suggestions} name suggestion(s) for {kind} {term!r} "
                    f"without {who} (NPI {q.npi}), but the network was not confirmed on screen (chip "
                    f"read {pinned!r}) — absence from an un-pinned directory is not evidence of "
                    f"out-of-network.",
                    result_count=count, screenshot=shot(f"absent-unpinned-{kind}"),
                )
            if count:
                # Results came back but the name index offered nothing. The cards are fuzzy matches and
                # we will not page through them, so this cannot be turned into an OON.
                return result(
                    PortalStatus.UNKNOWN,
                    f"{self.portal_name} returned {count} result(s) for {kind} {term!r} in "
                    f"{network_desc} but its in-network name index offered no suggestion for that term, "
                    f"and the result cards are fuzzy matches we do not page through (human-scale volume). "
                    f"Absence from page one is not evidence of out-of-network for NPI {q.npi}.",
                    result_count=count, screenshot=shot(f"fuzzy-only-{kind}"),
                )

        return result(
            PortalStatus.UNKNOWN,
            f"{self.portal_name} returned nothing at all for "
            f"{q.provider_last_name or 'the provider name'} in {network_desc} within 30 miles of "
            f"{where}"
            + ("" if plan_confirmed else " (network unconfirmed)")
            + f". An empty result set does not distinguish out-of-network from a failed search, so NPI "
            f"{q.npi} stays UNKNOWN.",
            result_count=0, screenshot=shot("no-results"),
        )

    # --- network selection (the plan-first half) ---------------------------------------------------

    def _network_map(self, page: Page, site: HealthSparqSite) -> list[tuple[str, str]]:
        """(label, launch URL) for every network this payer publishes.

        Harvested live from the payer's guest gate — that page IS the authority on which networks exist
        this plan year — and merged with the baked-in map so a restyled gate degrades instead of
        breaking. Live entries win; baked-in entries fill the gaps.
        """
        live = self._harvest_gate(page, site)
        merged: dict[str, str] = dict(live)
        for label, product, brand, prefix in site.networks:
            merged.setdefault(label, self._launch(site, product, brand, prefix))
        return sorted(merged.items())

    def _harvest_gate(self, page: Page, site: HealthSparqSite) -> list[tuple[str, str]]:
        if not site.network_gate_url:
            return []
        try:
            page.goto(site.network_gate_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3_000)
            html = page.content() or ""
        except (PlaywrightTimeout, PlaywrightError):
            return []  # best-effort: the baked-in map carries the driver
        out: dict[str, str] = {}
        for m in _GATE_LINK_RE.finditer(html):
            url = m.group("url").replace("\\u0026", "&").replace("\\/", "/")
            label = m.group("label").replace("\\u0026", "&").strip()
            if site.host not in url or "productCode=" not in url:
                continue  # no productCode -> the un-pinned directory (trap 2)
            if re.search(r"brandCode=[^&]*(DENTAL|VISION)", url, re.I):
                continue  # wrong network for a physician check
            if "do not know" in label.lower() or not label:
                continue
            out[label] = url
        return sorted(out.items())

    def _launch(self, site: HealthSparqSite, product: str, brand: str, prefix: str) -> str:
        # Param order copied from the payer's published links; HealthSparq's /service/login is fussy
        # about the hash it is handed, so it is reproduced rather than normalised.
        tail = (f"city=&state={site.state}&insurerCode={site.insurer_code}"
                f"&productCode={product}&brandCode={brand}")
        if prefix:
            tail += f"&alphaPrefix={prefix}"
        return f"https://{site.host}/healthsparq/public/#/one/{tail}"

    def _generic_launch(self, site: HealthSparqSite) -> str:
        return (f"https://{site.host}/healthsparq/public/#/one/city=&state={site.state}"
                f"&insurerCode={site.insurer_code}&brandCode={site.generic_brand_code}")

    def _best_network(self, networks: list[tuple[str, str]], plan: str | None) -> tuple[str, str] | None:
        """The published network whose label the 271 plan string names. None rather than a guess.

        Ranked by (shared distinctive tokens, fewer unmatched label tokens) — the second term is what
        separates "Statewide / National PPO" from "Statewide/National PPO + Prosano", which tie on the
        first. A tie on BOTH terms is genuine ambiguity and returns None; so does a label that shares
        only one token unless the plan string names the whole label (which is how single-token networks
        like "Focus" and "EPO" stay reachable without letting a bare "PPO" pick a Medicare product).
        """
        if not plan or not networks:
            return None
        want = _tokens(plan)
        if not want:
            return None
        ranked: list[tuple[int, int, str, str]] = []
        for label, url in networks:
            have = _tokens(label)
            if not have:
                continue
            score = len(have & want)
            if score == 0:
                continue
            if score < 2 and have - want:
                continue  # one weak token and the label is not fully named -> not enough to pin
            ranked.append((score, -len(have - want), label, url))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
            return None  # two networks fit equally well — refuse rather than pick
        return ranked[0][2], ranked[0][3]

    def _pinned_network(self, page: Page) -> str | None:
        """The NETWORK chip text, which is the portal telling us which network it will search."""
        try:
            chip = page.locator(_NETWORK_CHIP).first
            chip.wait_for(state="visible", timeout=10_000)
            return (chip.inner_text() or "").strip() or None
        except (PlaywrightTimeout, PlaywrightError):
            return None

    def _chip_agrees(self, chip: str, label: str) -> bool:
        """Does the on-screen chip corroborate the network we asked for?

        The chip truncates ("Statewide/National PPO…"), so this is a token overlap, not equality. The
        un-pinned directory's chip reads exactly "Choose a network" — reject that outright (trap 2).
        """
        low = chip.strip().lower().rstrip(".… ")
        if low in _UNPINNED_CHIP or "choose a network" in low or "select a network" in low:
            return False
        return bool(_tokens(chip) & _tokens(label))

    # --- location ---------------------------------------------------------------------------------

    def _location_term(self, q: PortalQuery) -> str | None:
        if q.zip_code:
            return q.zip_code
        if q.city and q.state:
            return f"{q.city}, {q.state}"
        return q.state or q.city or None

    def _set_location(self, page: Page, where: str) -> bool:
        """Open the location panel, type, pick the real suggestion, and COMMIT it.

        Committing matters: "Yes, this is correct" is what closes the wizard and unlocks search. And the
        suggestion index is not stable — index 0 is "Use my current location" when the field is empty
        (trap 4) — so suggestions are chosen by text, never by position.
        """
        for opener in (_WELCOME_BUTTON, _LOCATION_TRIGGER):
            try:
                b = page.locator(opener).first
                if b.is_visible():
                    b.click()
                    self._settle(page, 2_500)
                    break
            except (PlaywrightTimeout, PlaywrightError):
                continue
        try:
            inp = page.locator(_LOCATION_INPUT).first
            inp.wait_for(state="visible", timeout=15_000)
            inp.click()
            inp.fill(where)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        page.wait_for_timeout(3_500)  # the geocoder debounces

        picked = False
        try:
            opts = page.locator(f"{_LOCATION_SUGGESTION}:visible")
            for i in range(min(opts.count(), 8)):
                text = (opts.nth(i).inner_text() or "").strip()
                if "current location" in text.lower():
                    continue  # never hand the portal the browser's own location
                opts.nth(i).click()
                picked = True
                break
        except (PlaywrightTimeout, PlaywrightError):
            picked = False
        if not picked:
            try:
                page.locator(_LOCATION_INPUT).first.press("Enter")
            except (PlaywrightTimeout, PlaywrightError):
                return False
        self._settle(page, 2_500)

        try:  # the confirm button; a click here is what actually applies the location
            confirm = page.locator(_LOCATION_CONFIRM).first
            confirm.wait_for(state="visible", timeout=10_000)
            confirm.click()
        except (PlaywrightTimeout, PlaywrightError):
            pass  # some entry points apply the pick immediately; verified below either way
        self._settle(page, 4_000)
        shown = self._location_shown(page)
        return bool(shown and shown.strip().lower() != "location")

    def _location_shown(self, page: Page) -> str | None:
        try:
            return (page.locator(_LOCATION_TEXT).first.inner_text() or "").strip() or None
        except (PlaywrightTimeout, PlaywrightError):
            return None

    # --- search -----------------------------------------------------------------------------------

    def _search_terms(self, q: PortalQuery):
        """Surname first, then the full name — and never the NPI.

        This platform indexes no NPI at all (trap 3), so searching one would burn a lookup and return
        nothing. Surname before full name because the box is a prefix typeahead: the bare surname
        matches strictly more than "First Last" does.
        """
        terms: list[tuple[str, str]] = []
        if q.provider_last_name:
            terms.append((q.provider_last_name, "surname"))
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        if full and full != (q.provider_last_name or ""):
            terms.append((full, "full name"))
        return terms

    def _open_name_search(self, page: Page) -> bool:
        """Open the "Doctors by Name" category — that modal is what exposes the search box.

        Clicked by accessible ROLE and exact name, never by loose text: the phrase "Doctors by Name"
        also appears in the tile's own description paragraph, and clicking that does nothing while
        looking like success.
        """
        if self._search_box_visible(page):
            return True  # already open (e.g. the modal survived a previous step)
        try:
            tile = page.get_by_role("button", name="Doctors by Name").first
            tile.wait_for(state="visible", timeout=15_000)
            tile.click()
        except (PlaywrightTimeout, PlaywrightError):
            return False
        # Settle separately so a slow post-click network can never invalidate a click that worked.
        self._settle(page, 3_000)
        return self._search_box_visible(page)

    def _search_box_visible(self, page: Page, timeout_ms: int = 10_000) -> bool:
        try:
            page.locator(_SEARCH_INPUT).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except (PlaywrightTimeout, PlaywrightError):
            return False

    def _run_search(self, page: Page, term: str, q: PortalQuery) -> tuple[bool, int, str | None, int]:
        """One term. Returns (matched_us, result_count, matched_name, suggestion_count).

        Two surfaces are read, and they mean different things. The typeahead
        (`search-input-suggestion`) is the network's own NAME INDEX and is the oracle for absence. The
        results page cards are fuzzy matches, used only to find a match — never to prove one is missing
        (trap 6).

        Enter is pressed even when the typeahead already matched, because the results-page card is much
        better evidence than the suggestion list: it names the practice and the "N in network" count,
        whereas the typeahead panel is a modal that covers the NETWORK chip and the LOCATION in the
        screenshot. The suggestion match is kept as the fallback name.
        """
        try:
            box = page.locator(_SEARCH_INPUT).first
            box.wait_for(state="visible", timeout=10_000)
            box.click()
            box.fill(term)
        except (PlaywrightTimeout, PlaywrightError):
            return False, 0, None, 0
        page.wait_for_timeout(5_000)  # the typeahead debounces, then queries the pinned network

        suggestions = self._texts(page, _SUGGESTION, cap=40)
        suggested = self._match(suggestions, q)

        try:
            box.press("Enter")
        except (PlaywrightTimeout, PlaywrightError):
            return bool(suggested), len(suggestions), suggested, len(suggestions)
        self._settle(page, 8_000)

        cards = self._texts(page, _PROVIDER_CARD, cap=40)
        count = self._result_count(page) or len(cards)
        hit = self._match(cards, q) or suggested
        return bool(hit), count, hit, len(suggestions)

    def _accepted(self, page: Page) -> str:
        """The card's own networks-accepted count, e.g. " (card: '14 in network')". Pure evidence —
        Maydell showed "14 in network" and Desir "1 network accepted" on the same clinic, which is the
        clearest possible statement that this platform's answer is per-network, not per-payer."""
        for text in self._texts(page, "[data-test='plans-accepted-trigger']", cap=1):
            cleaned = text.replace("\xa0", " ").strip()
            if cleaned:
                return f" (card reads {cleaned!r})"
        return ""

    def _result_count(self, page: Page) -> int:
        """The portal's own total, not the number of cards on screen and not a live region (which is
        what produced a false count on the reference driver)."""
        for sel, pattern in (
            (_PAGINATION, r"of\s+([\d,]+)"),  # "Displaying 1-1 of 1"
            (_LOCAL_TAB, r"\(([\d,]+)\)"),  # "In-person care (139)"
        ):
            for text in self._texts(page, sel, cap=3):
                m = re.search(pattern, text)
                if m:
                    return int(m.group(1).replace(",", ""))
        return 0

    def _match(self, candidates: list[str], q: PortalQuery) -> str | None:
        """Is OUR provider among these portal names? Name-only — this portal indexes no NPI at all.

        The expectation is derived from the QUERY, never from the search term. That distinction IS the
        bug this replaced: the first search term is the bare surname, so a term-derived expectation had
        no first name left to check, the first-name guard never ran, and every same-surname provider
        matched — a false IN against a different doctor in the same practice. The old docstring
        described the guard correctly; the code just never reached it.

        Surname must appear as a WHOLE token (a substring test lets "Desir" match "Desrosiers"), and a
        known first name must agree — same token, or one a prefix of the other, so "Rob"/"Robert" and
        bare initials both work.

        Residual gap, deliberately left: if the caller knows no first name, `given` is empty and this
        degrades to surname-only. The Ins Test 3 sheet always supplies "Last, First", so the guard does
        fire in practice. It is NOT tightened to "no first name -> no match", because `None` here means
        *absent*, and on the OON path absence is the dangerous direction — refusing to match on thin
        identity would convert a false IN into a false OON, which is no better.
        """
        want = _norm_name(" ".join(p for p in (q.provider_first_name, q.provider_last_name) if p))
        if not want:
            return None
        surname, given = want[-1], want[:-1]
        for text in candidates:
            for line in (text or "").splitlines():
                if _is_chrome(line):
                    continue  # quotes the search back but names nobody, e.g. "See all results for Desir"
                have = _norm_name(line)
                if surname not in have:
                    continue
                if given and not any(
                    h.startswith(g) or g.startswith(h) for g in given for h in have if h != surname
                ):
                    continue
                return line.strip()[:120]
        return None

    # --- plumbing ---------------------------------------------------------------------------------

    def _texts(self, page: Page, selector: str, cap: int) -> list[str]:
        try:
            loc = page.locator(selector)
            n = min(loc.count(), cap)
            return [loc.nth(i).inner_text() or "" for i in range(n)]
        except PlaywrightError:
            return []

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""

    def _refusal(self, page: Page) -> str | None:
        """The SPA's error shell, matched on VISIBLE text. Bot-protection script presence is never a
        block on its own; only a shell actually shown instead of the directory counts."""
        text = self._page_text(page).lower()
        return next((m for m in _REFUSAL_MARKERS if m in text), None)

    def _dismiss_overlays(self, page: Page) -> None:
        """Banners and coachmarks re-appear at later steps, so this runs more than once."""
        for label in _OVERLAY_LABELS:
            try:
                b = page.get_by_role("button", name=label, exact=True).first
                if b.is_visible():
                    b.click()
                    page.wait_for_timeout(1_500)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _settle(self, page: Page, pause_ms: int = 3_000) -> None:
        """Best-effort wait. Never raises: this platform streams analytics and may never reach
        networkidle, and a settle that throws must not be mistaken for a step that failed."""
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass
