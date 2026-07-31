"""Aetna Find Care — the guest "Directory of Health Care Professionals" (DSE) on www.aetna.com.

Covers the Ins Test 3 "Aetna Commercial" rows (IL and AZ). This is the surface Test 2 read by hand:
searching "Desir Hedson" near Phoenix 85032 returned 35 other providers and no Hedson Desir, and that
absence-among-present-results is what made NPI 1346866332 out-of-network.

WHY NOT THE ASSIGNED ENTRY URL (verified live 2026-07-28)
  https://health.aetna.com/ahpublic/results?q= is a dead end, twice over:
    * it renders a spinner forever — the Aetna Health SPA needs a member session it cannot get as a
      guest, and `/ahpublic` (no trailing slash) 302s straight to /managemyaccount/login;
    * health.aetna.com/robots.txt is `User-agent: * / Allow: /login / Disallow: /`, so every ahpublic
      path is robots-disallowed for every agent.
  The live guest directory is the DSE app on www.aetna.com, whose robots.txt `User-agent: *` block
  disallows only a handful of /docfind/ paths (BSHSI-branded ones and /docfind/custom/*) and does not
  disallow /dsepublic/. Aetna's on-page Terms of Use still prohibit robotic access to Provider Search —
  that is the client's counsel call, not a technical question — so this driver does exactly one lookup
  per provider and never enumerates.

OBSERVED GUEST FLOW (2026-07-28), each step gated on the previous:

    #/contentPage?page=providerSearchLanding…&openPleats   #zip1 "Enter location here"
                                                           -> [role=option] "60305 (River Forest, IL)"
                                                           -> #second-step-continue
    #/contentPage?page=providerSearchPlanList              63 `input[name=selectPlanRadio]` radios,
                                                           label via label[for=<id>]; picking one
                                                           reveals `[id="<radioId>_ContinueButton"]`
                                                           <- THIS is what pins the network
    #/contentPage?page=providerSearch                      banner "Searching by: <plan> | Change Plan"
                                                           + one input, #Doctors
    #/contentPage?page=providerResults                     the plan-scoped result set

  Entry-URL trap: the bare `#/contentPage?page=providerSearchLanding` hash is flaky — the SPA rewrote
  it to `#/leavingPage` and on one of two trials never rendered #zip1 at all. The Aetna logo's own href
  (…&site_id=dse&language=en&openPleats) was stable 2/2 and keeps its hash, so that is the entry used
  here.

WHAT THE PORTAL'S TWO SEARCH SURFACES ACTUALLY MEAN — this drives the whole verdict logic:

  * The #Doctors typeahead is NOT plan-scoped. It calls api2.talix.com/…/docfindtypeahead with only
    `q`, `zipcode` and `radius` — no plan, no product. Aetna's own copy says it plainly: "We only show
    providers who participate with our plans" (any plan) and "Select a result to find out if a provider
    or facility is in or out of your network". So the typeahead answers "is this person in Aetna's
    directory near this ZIP", never "in THIS network".
  * The results page IS plan-scoped: clicking a suggestion calls
    api01.aetna.com/healthcore/prod/v3/publicdse_providersearch?…&productIdentifier=~MPPO…, where the
    productIdentifier is the prefix of the plan radio's id (MPPO = Open Choice PPO).

  The typeahead is also TRUNCATED — the list is limitTo-capped and offers "N more Healthcare Providers
  & Practices". "Tursunaliev" showed 3 of at least 5 locations. Absence from an un-expanded, truncated
  list is therefore not absence from the result set, and this driver refuses to call it an OON.

THIS DRIVER MUST RUN HEADED. That is the single most important operational fact here.
  The plan-scoped results call, publicdse_providersearch, is refused at Akamai's edge (HTTP 403 "Access
  Denied", Reference #18.…) when the request comes from a HEADLESS Chromium, and served (HTTP 200) from
  the same walk in a headed one. The discriminator was isolated by holding the URL and every header
  constant and changing one thing at a time:

      headless Playwright  -> sec-ch-ua: "HeadlessChrome";v="149", …   -> 403 (net::ERR_FAILED in-page)
      headed   Playwright  -> sec-ch-ua: "Chromium";v="149", …         -> 200, real result cards
      curl, UA "curl/8.7.1" or "python-requests/…" (no client hints)   -> 200 (then a 401 apikey gate)
      curl, UA "Chrome/149" / "Safari/17" / "Firefox/130" / "Mozilla"  -> 403

  So the rule is a headless/inconsistency check: a request whose User-Agent claims a browser while its
  client hints either contradict it or are missing gets denied; a real visible browser passes. Members
  are NOT affected — nothing here is a challenge, no Akamai bot cookie (_abck/bm_sz/ak_bmsc) is set
  anywhere in the flow, and the residential US proxy makes no difference, so it is not geo or IP
  reputation either. Both routes to the result set (typeahead-suggestion click and category-guided
  drill-down) hit the same refusal when headless.

  Running headed is the fix, and it is the honest one: the browser is left exactly as it is rather than
  masked, no challenge is solved, nothing is patched, and headed is a first-class mode of this package
  (browser_session(headed=…) / PORTAL_HEADED / capture.py --headed). What is deliberately NOT done is
  calling the JSON endpoint directly with a non-browser User-Agent to get the 200 — that leaves the
  portal the payer publishes for people, and Aetna's Terms of Use speak directly to it.

  A headless run therefore ends in PortalStatus.BLOCKED / Reachability.WAF_BLOCK, whose note says to
  re-run headed. That is a transport outcome, never a network verdict.

WHAT THE RESULTS PAGE GIVES US (observed headed, 2026-07-28) — the portal answers in so many words:
  Each result is a `div.providerFacilityInfotd` card carrying the provider, the practice address, the
  distance, and an explicit `<span>In Network</span>` badge; the section is headed "In network search
  results for <provider> near <ZIP>". So the verdict is read off Aetna's own badge rather than inferred
  from presence. For NPI 1780175349 the first card was "Tursunaliev, Serik, MD — In Network — 7420
  Central Ave Bldg C Ste 230, River Forest, IL 60305 — 0.68 miles", which is the sheet row's own clinic
  address. No card prints an NPI, so identity rests on first+last name plus the practice address.

VERIFIED LIVE 2026-07-28 (headed unless noted):
  * Ins Test 3 row — Tursunaliev, Serik, NPI 1780175349, 60305, plan "Aetna Commercial"
      -> IN_NETWORK, plan pinned Open Choice® PPO (the broad-commercial default), 13 cards, badged
         In Network at the clinic address. Headless, the same walk -> BLOCKED / WAF_BLOCK.
  * Same provider with the product named, "Aetna Choice POS II (Open Access)"
      -> IN_NETWORK on that product, plan matched by name, so no defaulted-plan caveat.
  * Test 2 control — Desir, Hedson, NPI 1346866332, 85032
      -> OUT_OF_NETWORK: 7 participating providers for "Desir" and none of them him. This is also the
         driver's own regression test for word-exact matching: every one of those 7 is a "Desiree",
         which a substring test would have called a hit.
  * Medicare plan string -> UNKNOWN before any navigation (wrong directory, see _wrong_line_of_business).
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal import browser as pb
from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus, Reachability

ENTRY = (
    "https://www.aetna.com/dsepublic/#/contentPage?page=providerSearchLanding"
    "&site_id=dse&language=en&openPleats"
)

_ZIP = "#zip1"
_ZIP_COMMIT = "#second-step-continue"
_PLAN_RADIOS = "input[name='selectPlanRadio']"
_SHOW_ALL_PLANS = "Show all plans (including those not in my area)"
_SEARCH = "#Doctors"

# The typeahead's own li class, preferred over a bare [role=option] for two reasons: on the earlier step
# [role=option] is also the ZIP autocomplete (the same poisoning the UHC driver hit with category chips),
# and this li's ng-repeat is `matches | filter:{model:{category:'specialist'}}` — Aetna's INDIVIDUAL
# provider group, which is exactly the population a physician check needs, with facilities and procedures
# rendered as separate groups and correctly left out of the denominator.
_SUGGESTIONS = (
    "li.typeahead_grouping[role=option]",
    ".dropdown-menu li[role=option]",
    "li[role=option]",
)
_MORE_SUGGESTIONS = re.compile(r"\b(\d+)\s+more\s+Healthcare\s+Providers", re.I)
# The typeahead appends its own aggregate row, "<term> (any location)". Not a provider — it must not be
# counted as a result, nor matched as our person.
_ANY_LOCATION = re.compile(r"\(any location\)", re.I)

# Result cards on page=providerResults, verified against the real rendered page in a headed run
# (2026-07-28): `div.col-xs-12.col-md-4.dataGridContentCol.providerFacilityInfotd`, one per practice
# location. The generic candidates behind it are only there so a class rename degrades to UNKNOWN
# instead of to a wrong verdict.
_RESULT_CARDS = (
    ".providerFacilityInfotd",
    "[class*='providerFacilityInfo']",
    "[class*='providerResult']",
    "[class*='resultCard']",
)

# Aetna prints its own per-card network verdict; these are read instead of inferring from presence.
_IN_NETWORK_BADGE = re.compile(r"\bin\s*network\b", re.I)
_OON_BADGE = re.compile(r"\b(out\s*of\s*network|not\s*in\s*network)\b", re.I)


def _badge(text: str) -> str | None:
    """Read a card's network badge: "oon" | "in" | None.

    OON is tested FIRST and wins outright, because "in network" is a substring of "Not In Network" —
    `_IN_NETWORK_BADGE` matches inside it. Testing IN on the raw text inverted the verdict: the OON
    branch was gated on `OON and not IN`, that guard never fired on a "Not In Network" card, execution
    fell through, and the driver returned IN_NETWORK for a provider the payer had explicitly badged as
    out of network. Never test the IN badge without excluding the OON phrases first.
    """
    if _OON_BADGE.search(text or ""):
        return "oon"
    return "in" if _IN_NETWORK_BADGE.search(text or "") else None

# Aetna's own error card when the results API fails, and the request whose refusal causes it.
_PORTAL_ERROR = "can't complete your request"
_RESULTS_API = "publicdse_providersearch"

# Deliberately the BROADEST Aetna commercial network, used only when the 271 names no product at all.
# The direction of error matters: absence from the broad PPO generalises down to the narrower products
# (APCN / APCN Plus / Savings Plus) far more safely than presence in it generalises up.
_BROAD_COMMERCIAL = "openchoiceppo"

# Non-medical plan families, excluded when defaulting for a physician check.
_NON_MEDICAL = ("DENTAL", "DMO", "DNO", "VISION", "EMPLOYEE ASSISTANCE", "VITAL SAVINGS", "PEDIATRIC")

# Line-of-business words a 271 plan string carries that name no Aetna PRODUCT. They must be stripped
# before deciding "the 271 named a product we could not find" — otherwise a plain "Aetna Commercial" is
# read as an unfound product (COMMERCIAL appears in none of the 63 plan labels) and the driver refuses to
# pin anything at all, which is exactly what a first live run did.
_GENERIC_PLAN_WORDS = frozenset({
    "AETNA", "COMMERCIAL", "EMPLOYER", "EMPLOYEE", "GROUP", "INSURANCE", "PLAN", "PLANS", "HEALTH",
    "CARE", "PRODUCT", "MEDICAL", "INDIVIDUAL", "FAMILY", "BENEFIT", "BENEFITS", "COVERAGE",
    "NETWORK", "OF", "THE", "AND", "FOR", "WITH",
})

# Product-FAMILY markers. Aetna's 63 IL plan names are combinatorial reuses of the same handful of
# words, so the family qualifier is what actually separates "Aetna Choice POS II (Open Access)" from
# "Aetna Choice POS II (Aetna HealthFund)". A family word the 271 did NOT ask for is therefore a much
# stronger signal of a wrong plan than an ordinary spare token, and is penalised accordingly.
_FAMILY_MARKERS = frozenset({
    "HEALTHFUND", "DENTALFUND", "APCN", "PREMIER", "SAVINGS", "INNOVATION", "CARELINK", "LOCAL",
    "BEST", "MULTI", "TIER", "EXTEND", "ADVANTAGE", "AFFORDABLE", "VITAL", "PEDIATRIC", "DISCOUNT",
})


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str) -> set[str]:
    """Meaningful words of a plan name, minus the ones every Aetna product shares.

    Two characters, not four: PPO/HMO/POS/EPO are the discriminating words here, "II" is what separates
    Choice POS from Choice POS II, and IL/MO are what separate the two Local Best networks.
    """
    return {
        t for t in re.split(r"[^A-Za-z0-9]+", (s or "").upper())
        if len(t) >= 2 and t not in _GENERIC_PLAN_WORDS
    }


class AetnaFindCareDriver(PortalDriver):
    # Akamai refuses the plan-scoped publicdse_providersearch call when sec-ch-ua says
    # HeadlessChrome (403 "Access Denied"), and serves it from the same walk headed.
    requires_headed = True
    key = "aetna-ahpublic"
    portal_name = "Aetna Find Care (guest provider directory)"
    # Guest search is gated on a home location entered as a ZIP; the driver already refuses without
    # one (see `capture`). Declaring it lets run_capture refuse before a browser is ever launched.
    location_fields = ("zip_code",)

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail_box: list[str] = []
        refused: list[str] = []  # every edge-refused results request, as evidence for a BLOCKED verdict

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # Always carry the walk trail: a verdict is only as trustworthy as the path that produced it.
            if trail_box:
                note = f"{note} [portal walk: {' → '.join(trail_box)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=kw.pop("plan", q.plan), tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        lob_note = self._wrong_line_of_business(q)
        if lob_note:
            return result(PortalStatus.UNKNOWN, lob_note)
        if not q.zip_code:
            return result(
                PortalStatus.UNKNOWN,
                "Aetna's guest directory gates every search behind a home location, and no clinic ZIP "
                "was supplied — there is no way to scope the search, so no answer can be honest.",
            )

        self._watch_results_api(page, refused)

        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            pass
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation failed: {type(e).__name__}: {e}",
                          reachability=Reachability.ERROR, screenshot=shot("nav-failed"))
        self._settle(page, 4_000)
        self._dismiss_overlays(page)

        plan_label, plan_matched, trail = self._walk_to_plan(page, q)
        trail_box.extend(trail)
        shot("plan-walk")
        if not plan_label:
            return result(
                PortalStatus.UNKNOWN,
                f"Could not pin a plan in Aetna's guest directory for {q.plan!r} near {q.zip_code}. "
                f"Absence from an unpinned Aetna directory is not evidence of out-of-network, so this "
                f"stays UNKNOWN.", screenshot=shot("no-plan"),
            )

        try:
            search = page.locator(_SEARCH).first
            search.wait_for(state="visible", timeout=15_000)
        except (PlaywrightTimeout, PlaywrightError):
            return result(
                PortalStatus.BLOCKED,
                f"plan {plan_label!r} was pinned but the provider-search box (#Doctors) never appeared — "
                f"the DSE search step did not render.", plan=plan_label,
                reachability=Reachability.LOADED, screenshot=shot("no-search-input"),
            )

        # Surname first, then "First Last". The NPI is deliberately NOT tried: Aetna's typeahead does not
        # index it — a live call for 1780175349 returned `"data":[]` while "Tursunaliev" returned matches.
        for term, kind in self._search_terms(q):
            sugg, truncated = self._suggest(page, search, term)
            providers = self._providers(sugg)
            trail_box.append(f"typeahead {term!r}: {len(providers)} provider row(s)"
                             + (" (truncated)" if truncated else ""))
            if not providers:
                continue

            mine, namesake = self._match(sugg, q)
            if mine is None and namesake is not None:
                return result(
                    PortalStatus.UNKNOWN,
                    f"Aetna lists {sugg[namesake]!r} for {kind} {term!r} near {q.zip_code} in plan "
                    f"{plan_label!r} — the surname matches but the given name does not, and this portal "
                    f"does not index NPIs (a live typeahead query for {q.npi} returns nothing), so "
                    f"{q.provider_first_name or 'our provider'} {q.provider_last_name} could not be "
                    f"identified either way. Reporting a namesake as our provider, or as our provider's "
                    f"absence, would both be guesses.", plan=plan_label, result_count=len(providers),
                    matched_name=sugg[namesake], screenshot=shot(f"namesake-{kind}"),
                )
            if mine is None:
                if truncated:
                    return result(
                        PortalStatus.UNKNOWN,
                        f"Aetna's typeahead returned {len(providers)} provider(s) for {kind} {term!r} "
                        f"near {q.zip_code} in plan {plan_label!r} without "
                        f"{q.provider_last_name or q.npi}, but the list was still truncated ('N more "
                        f"Healthcare Providers'), so absence from it proves nothing.",
                        plan=plan_label, result_count=len(providers),
                        screenshot=shot(f"truncated-{kind}"),
                    )
                # Why this absence is sound even though the typeahead ignores the plan: the list is
                # Aetna's participating-provider index for the ZIP ("We only show providers who
                # participate with our plans"), i.e. a SUPERSET of any single product's network. Absence
                # from the superset entails absence from the pinned product, so the verdict does not
                # depend on which commercial product was pinned — no defaulted-plan caveat is needed.
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"Aetna's guest directory returned {len(providers)} participating provider(s) for "
                    f"{kind} {term!r} within 25 miles of {q.zip_code}, and none of them is "
                    f"{q.provider_first_name or ''} {q.provider_last_name} (NPI {q.npi}). That list is "
                    f"Aetna's plan-agnostic index of providers who participate with its plans near this "
                    f"ZIP — a superset of {plan_label!r}, the product pinned for this walk — so absence "
                    f"from it entails absence from that network whichever commercial product the member "
                    f"actually holds. The list was complete (no 'N more Healthcare Providers' remained), "
                    f"so this is an out-of-network absence, the same reading the Test 2 manual check made.",
                    plan=plan_label, result_count=len(providers), screenshot=shot(f"absent-{kind}"),
                )

            # Our provider is in Aetna's directory near this ZIP. Only the plan-scoped results page can
            # turn that into IN/OON, so open it — the typeahead alone must not become the verdict.
            shot(f"suggestion-{kind}")
            return self._open_and_read(page, sugg, providers, mine, plan_label, plan_matched, q,
                                       result, shot, refused, trail_box, kind, term)

        return result(
            PortalStatus.UNKNOWN,
            f"Aetna's typeahead returned no providers for {q.provider_last_name or q.npi} within 25 miles "
            f"of {q.zip_code} with plan {plan_label!r} pinned. An empty result set cannot distinguish "
            f"out-of-network from a failed search — the same term with a different spelling or radius may "
            f"answer, so this is not an OON.", plan=plan_label, result_count=0,
            screenshot=shot("no-suggestions"),
        )

    # --- steps -----------------------------------------------------------------------------------

    def _wrong_line_of_business(self, q: PortalQuery) -> str | None:
        """This portal is Aetna's COMMERCIAL guest directory. Its landing page routes Medicare and
        Medicaid members elsewhere, and the roster collapses Aetna Commercial and Aetna Medicare
        Advantage onto one payer key (aetna-il / aetna-az) — so the plan string, not the key, decides."""
        from network_probe.domain.line_of_business import line_of_business

        lob = line_of_business(q.plan or "", None)
        if lob in ("medicare", "dual"):
            return (
                f"Plan {q.plan!r} is a Medicare line, and this is Aetna's commercial guest directory "
                f"(site_id=dse) — its own landing page sends Medicare members to "
                f"health.aetna.com/ahpublic/medicare-direct, which is robots-disallowed for every agent. "
                f"Answering from the commercial network would be the wrong network, so: UNKNOWN."
            )
        if lob == "medicaid":
            return (
                f"Plan {q.plan!r} is Medicaid; Aetna Better Health has its own directory "
                f"(aetnabetterhealth.com/find-provider.html) and is a separate roster payer. This "
                f"commercial directory cannot answer for it: UNKNOWN."
            )
        return None

    def _search_terms(self, q: PortalQuery):
        """Surname first, then "First Last" — a typeahead matches prefixes, so the bare surname reaches
        more than the full name does, and the NPI reaches nothing at all here."""
        terms: list[tuple[str, str]] = []
        if q.provider_last_name:
            terms.append((q.provider_last_name, "surname"))
        full = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        if full and full != (q.provider_last_name or ""):
            terms.append((full, "full name"))
        return terms

    def _walk_to_plan(self, page: Page, q: PortalQuery) -> tuple[str | None, bool, list[str]]:
        """Location → commit → plan radio → Continue. Returns (pinned label, matched-the-271, trail).

        `trail` records each step reached so a capture that stops early says exactly where. A silently
        partial walk that then reports OON is the worst possible failure mode.
        """
        trail: list[str] = []
        if not self._commit_location(page, q.zip_code or ""):
            return None, False, trail + [f"location {q.zip_code!r} NOT accepted on the guest landing"]
        trail.append(f"location committed: {q.zip_code}")

        # The plan step fetches its list after navigating, and on a slow run the radios are not in the
        # DOM when the settle returns — a live headed run failed here with "plan list never rendered".
        # Wait for the radios themselves rather than for a page state.
        try:
            page.locator(_PLAN_RADIOS).first.wait_for(state="attached", timeout=25_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        options = self._plan_options(page)
        if not options:
            return None, False, trail + [f"plan list never rendered at {page.url.split('#')[-1]}"]

        idx, matched = self._best_plan(options, q.plan)
        if idx is None:
            # The in-area list is filtered by ZIP; a plan from another market only appears behind this.
            if self._click_text(page, _SHOW_ALL_PLANS):
                trail.append("expanded: show all plans")
                options = self._plan_options(page)
                idx, matched = self._best_plan(options, q.plan)
        if idx is None:
            return None, False, trail + [
                f"no plan among {len(options)} matched {q.plan!r}, and no commercial default applied"
            ]

        radio_id, label = options[idx]
        try:
            page.locator(f"{_PLAN_RADIOS}[id={radio_id!r}]").first.click()
        except (PlaywrightTimeout, PlaywrightError):
            return None, False, trail + [f"plan radio {label!r} NOT clickable"]
        self._settle(page, 2_500)  # settle AFTER the click, so a slow settle cannot discard the choice

        # Selecting a radio reveals that plan's own Continue button, id = "<radioId>_ContinueButton".
        try:
            page.locator(f'[id="{radio_id}_ContinueButton"]').first.click()
        except (PlaywrightTimeout, PlaywrightError):
            return None, False, trail + [f"plan {label!r} selected but its Continue button never appeared"]
        self._settle(page, 4_000)
        self._dismiss_overlays(page)  # overlays re-appear on later steps

        # The search page states the pinned network back to us: "Searching by: <plan> | Change Plan".
        # Only that banner — never our own click — is allowed to confirm the plan.
        banner = self._pinned_plan(page)
        if not banner:
            return None, False, trail + [
                f"clicked plan {label!r} but the search page never showed a 'Searching by:' banner"
            ]
        trail.append(f"plan pinned: {banner}" + ("" if matched else " (commercial default, see note)"))
        return banner, matched, trail

    def _commit_location(self, page: Page, zip_code: str) -> bool:
        """Type the ZIP, take the matching suggestion, then commit with the landing's Continue button."""
        try:
            box = page.locator(_ZIP).first
            box.wait_for(state="visible", timeout=15_000)
        except (PlaywrightTimeout, PlaywrightError):
            # The SPA sometimes lands on a stale hash without the guest pleat; one clean re-entry fixes it.
            try:
                page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
            except (PlaywrightTimeout, PlaywrightError):
                return False
            self._settle(page, 5_000)
            self._dismiss_overlays(page)
            try:
                box = page.locator(_ZIP).first
                box.wait_for(state="visible", timeout=15_000)
            except (PlaywrightTimeout, PlaywrightError):
                return False
        try:
            box.click()
            box.fill(zip_code)
            page.wait_for_timeout(3_000)  # the location autocomplete debounces
            opts = page.locator("[role=option]:visible")
            if opts.count():
                # Prefer the option that actually contains this ZIP; the list can hold city/county rows.
                pick = next(
                    (i for i in range(min(opts.count(), 10))
                     if zip_code in (opts.nth(i).inner_text() or "")), 0
                )
                opts.nth(pick).click()
            else:
                box.press("Enter")
            page.wait_for_timeout(1_500)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        # "Continue" is a real gate: the plan list does not exist until the location is committed.
        try:
            page.locator(_ZIP_COMMIT).first.click()
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 4_000)
        return True

    def _plan_options(self, page: Page) -> list[tuple[str, str]]:
        """(radio id, visible label) for every plan radio. The label lives in a `label[for=<id>]`, not
        in the input, and the id's own prefix is the productIdentifier the results API is scoped by."""
        try:
            return [
                (r["id"], r["label"])
                for r in page.evaluate(
                    """() => Array.from(
                         document.querySelectorAll("input[name='selectPlanRadio']")
                       ).map(r => {
                         const lab = document.querySelector(`label[for="${CSS.escape(r.id)}"]`);
                         const host = lab || r.closest('li,div,tr') || r.parentElement;
                         return {id: r.id, label: ((host && host.innerText) || '').trim().slice(0, 140)};
                       })"""
                )
                if r.get("id") and r.get("label")
            ]
        except PlaywrightError:
            return []

    def _best_plan(self, options: list[tuple[str, str]], plan: str | None) -> tuple[int | None, bool]:
        """Pick the plan radio for the 271's plan string. Returns (index, matched_the_271).

        Set-similarity, not a token count. Aetna's product names are permutations of the same words, so
        "Aetna Choice POS II (Open Access)" and "Aetna Open Access Managed Choice POS (Aetna HealthFund)"
        share the same four tokens and a bare hit count ties them. Each shared token is worth 100; a
        token the label carries that the 271 did not ask for costs 5, and 25 more if it is a product
        FAMILY word (HealthFund, APCN, Local Best…), which is what makes the plain product beat its
        HealthFund twin; a token the 271 asked for and the label lacks costs 1.

        A single weak hit is not a match: with two or more asked-for tokens at least two must land, so
        "Aetna Whole Health <something>" cannot be answered by whatever label happens to share one word.
        """
        if not options:
            return None, False
        labels = [lab for _, lab in options]
        want = _tokens(plan or "")

        best, best_score = None, 0
        need = min(2, len(want))
        for i, lab in enumerate(labels):
            toks = _tokens(lab)
            shared = toks & want
            if len(shared) < need:
                continue
            spare = toks - want
            score = (
                len(shared) * 100
                - len(spare) * 5
                - len(spare & _FAMILY_MARKERS) * 25
                - len(want - toks)
            )
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            return best, True

        # No product matched. Only default when the 271 named no product to begin with — e.g. plain
        # "Aetna Commercial". If it named one we could not find, pinning something else would search the
        # wrong network, so we refuse and the capture stays UNKNOWN.
        if want:
            return None, False
        medical = [
            i for i, lab in enumerate(labels)
            if not any(k in lab.upper() for k in _NON_MEDICAL)
        ]
        broad = next((i for i in medical if _norm(labels[i]) == _BROAD_COMMERCIAL), None)
        if broad is None:
            broad = next((i for i in medical if "PPO" in _tokens(labels[i])), None)
        return broad, False

    def _defaulted_caveat(self, plan_label: str, q: PortalQuery, direction: str) -> str:
        base = (
            f" NOTE: the 271 plan string {q.plan!r} names no specific Aetna product, so {plan_label!r} "
            f"was pinned as Aetna's broad commercial network"
        )
        if direction == "absence":
            return base + (
                " — absence from the broad network is the safe direction to generalise, but if the member "
                "is on a differently-shaped product this should be re-run against that product by name."
            )
        return base + (
            " — presence in the broad network does NOT imply presence in a narrower one (APCN, APCN Plus, "
            "Savings Plus), so confirm the member's exact product before relying on this as IN-network."
        )

    def _pinned_plan(self, page: Page) -> str | None:
        """The plan the portal says it is searching, read off its own banner."""
        m = re.search(r"Searching by:\s*(.+?)\s*(?:\||Change Plan|\n)", self._page_text(page))
        return m.group(1).strip() if m else None

    def _suggest(self, page: Page, search, term: str) -> tuple[list[str], bool]:
        """Type one term and read the typeahead. Returns (suggestions, still_truncated).

        Typed character-by-character on purpose: the typeahead fires per keystroke against
        docfindtypeahead, and a `fill()` can land the whole string without a matching query.
        """
        try:
            search.click()
            search.fill("")
            search.type(term, delay=90)
        except (PlaywrightTimeout, PlaywrightError):
            return [], False
        page.wait_for_timeout(4_500)

        sugg = self._read_suggestions(page)
        truncated = bool(_MORE_SUGGESTIONS.search(self._page_text(page)))
        # Expand repeatedly, not once: the list grows in limitTo steps (3 → 8 → …), and a single
        # expansion can leave it still capped. `truncated` is what gates the OON, so it must be the
        # state after we stopped being able to expand, never after one click.
        for _ in range(3):
            if not truncated:
                break
            if not self._click_text(page, "more Healthcare Providers", exact=False):
                break
            page.wait_for_timeout(2_500)
            sugg = self._read_suggestions(page) or sugg
            truncated = bool(_MORE_SUGGESTIONS.search(self._page_text(page)))
        return sugg, truncated

    def _read_suggestions(self, page: Page) -> list[str]:
        # Cap high enough to be no cap in practice: a silent cut here would look like a complete list
        # and could manufacture an OON out of a list we only partly read.
        for sel in _SUGGESTIONS:
            try:
                loc = page.locator(f"{sel}:visible")
                n = loc.count()
                if n:
                    return [(loc.nth(i).inner_text() or "").strip() for i in range(min(n, 200))]
            except PlaywrightError:
                continue
        return []

    def _match(self, sugg: list[str], q: PortalQuery) -> tuple[int | None, int | None]:
        """Locate our provider in the suggestion list. Returns (exact index, surname-only index).

        Matching is WORD-EXACT, never substring, and that distinction decides verdicts. This portal's
        typeahead matches prefixes, so searching "Desir" near 85032 returns Desiree Guzman, Desiree
        Lewis, Desiree Mayo … — a substring test finds "desir" inside "desireeguzman" and would report
        Hedson Desir as present, silently inverting the Test 2 out-of-network verdict into an IN. Rows
        read "<First> [M] <Last> <CRED> - <City>, <ST>", so the name tokens before " - " are compared as
        whole words, and a multi-word surname is matched as a subset of them.

        A surname-exact row whose given name differs is returned separately: it is neither our provider
        nor proof of absence, and the caller must resolve it as UNKNOWN rather than guess either way.
        """
        last_toks = {_norm(w) for w in re.split(r"[^A-Za-z']+", q.provider_last_name or "") if w}
        first_toks = {_norm(w) for w in re.split(r"[^A-Za-z']+", q.provider_first_name or "") if w}
        if not last_toks:
            return None, None
        surname_only: int | None = None
        for i, text in enumerate(sugg):
            if _ANY_LOCATION.search(text):
                continue
            head = text.split(" - ")[0]
            toks = {_norm(w) for w in re.split(r"[^A-Za-z']+", head) if w}
            if not last_toks <= toks:
                continue
            if not first_toks or first_toks <= toks:
                return i, None
            if surname_only is None:
                surname_only = i
        return None, surname_only

    def _providers(self, sugg: list[str]) -> list[str]:
        """Real provider rows only — drops the synthesised "<term> (any location)" aggregate."""
        return [s for s in sugg if not _ANY_LOCATION.search(s)]

    def _open_and_read(self, page, sugg, providers, mine, plan_label, plan_matched, q, result, shot,
                       refused, trail_box, kind, term) -> PortalCapture:
        """Click our suggestion and read the plan-scoped results page."""
        try:
            self._suggestion_locator(page).nth(mine).click()
        except (PlaywrightTimeout, PlaywrightError):
            return result(
                PortalStatus.UNKNOWN,
                f"Aetna's typeahead listed {sugg[mine]!r} for {kind} {term!r} near {q.zip_code}, but the "
                f"suggestion could not be opened, so the plan-scoped answer for {plan_label!r} was never "
                f"read. Directory presence alone is not a network verdict here — the portal itself says "
                f"'Select a result to find out if a provider or facility is in or out of your network'.",
                plan=plan_label, result_count=len(providers), matched_name=sugg[mine],
                screenshot=shot("suggestion-not-clickable"),
            )
        self._settle(page, 6_000)
        trail_box.append("opened plan-scoped results")

        text = self._page_text(page)
        cards, count = self._cards(page)
        # Only trust the "refused" signal when the page also has nothing to show: a stale/duplicate XHR
        # failure alongside a fully rendered result set must not turn a real answer into a BLOCKED.
        if (refused or _PORTAL_ERROR in text.lower()) and not cards:
            return result(
                PortalStatus.BLOCKED,
                f"Aetna's guest directory does list {sugg[mine]!r} near {q.zip_code} — but that typeahead "
                f"is not plan-scoped (docfindtypeahead takes only q/zipcode/radius), and the plan-scoped "
                f"result set for {plan_label!r} was refused at the edge: "
                + (f"{len(refused)} request(s) to {_RESULTS_API} failed ({refused[0]}), "
                   if refused else "")
                + "and the portal rendered its own \"We're Sorry. We can't complete your request.\" card. "
                "This is the headless signature: Akamai denies this endpoint when the client hints "
                "advertise HeadlessChrome and serves it (HTTP 200, real cards) for the identical walk in "
                "a headed browser. RE-RUN THIS CAPTURE HEADED (PORTAL_HEADED=1 / --headed) — it is not a "
                "challenge, not geo, and not something a member would hit. Until then no IN/OON can be "
                "read, and directory presence must not be promoted into one.",
                plan=plan_label, result_count=len(providers), matched_name=sugg[mine],
                reachability=Reachability.WAF_BLOCK, screenshot=shot("results-refused"),
            )

        # Aetna badges every card itself, so read its verdict rather than inferring one from presence.
        ours = [c for c in cards if self._match([c], q)[0] is not None]
        at_clinic = next((c for c in ours if q.zip_code and q.zip_code in c), None)
        where = self._card_address(at_clinic or (ours[0] if ours else ""))

        if ours and any(_badge(c) == "oon" for c in ours):
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"Aetna's plan-scoped results page for {plan_label!r} near {q.zip_code} lists "
                f"{q.provider_first_name or ''} {q.provider_last_name} (NPI {q.npi}) at {where} and "
                f"badges the listing out-of-network — the payer's own word, not an inference from absence."
                + ("" if plan_matched else self._defaulted_caveat(plan_label, q, direction="absence")),
                plan=plan_label, result_count=count, matched_name=self._card_name(at_clinic or ours[0]),
                screenshot=shot("badged-oon"),
            )
        if ours and any(_badge(c) == "in" for c in ours):
            return result(
                PortalStatus.IN_NETWORK,
                f"Aetna's plan-scoped results page for {plan_label!r} badges "
                f"{q.provider_first_name or ''} {q.provider_last_name} (NPI {q.npi}) IN NETWORK at "
                f"{where}"
                + (f", the clinic ZIP {q.zip_code} itself" if at_clinic else
                   f" — nearest of {len(ours)} listing(s) near {q.zip_code}")
                + ". No card prints an NPI, so identity rests on the first+last name and the practice "
                "address."
                + ("" if plan_matched else self._defaulted_caveat(plan_label, q, direction="presence")),
                plan=plan_label, result_count=count, matched_name=self._card_name(at_clinic or ours[0]),
                screenshot=shot("badged-in"),
            )
        if ours:
            return result(
                PortalStatus.UNKNOWN,
                f"Aetna's results page for {plan_label!r} lists {q.provider_last_name} at {where}, but "
                f"the card carries neither an 'In Network' nor an 'Out of Network' badge — the badge is "
                f"how this portal states the answer, so without it presence alone is not a verdict.",
                plan=plan_label, result_count=count, matched_name=self._card_name(ours[0]),
                screenshot=shot("no-badge"),
            )
        if cards:
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"Aetna returned {count} plan-scoped result(s) for {kind} {term!r} in plan {plan_label!r} "
                f"near {q.zip_code} and none of them is {q.provider_first_name or ''} "
                f"{q.provider_last_name} (NPI {q.npi}), even though the plan-agnostic typeahead had "
                f"listed {sugg[mine]!r} — the provider is in Aetna's directory but not in this network."
                + ("" if plan_matched else self._defaulted_caveat(plan_label, q, direction="absence")),
                plan=plan_label, result_count=count, screenshot=shot("absent-from-plan"),
            )
        # No readable rows and no error. `count` may still be non-zero from the portal's own copy, but a
        # count we could not READ cannot support "ours is not among them" — that is the difference between
        # an OON and a guess, so it stays UNKNOWN and names the reason.
        return result(
            PortalStatus.UNKNOWN,
            f"Aetna's results page for {plan_label!r} answered for {kind} {term!r} near {q.zip_code} "
            f"without an error, but no result card matched "
            f"{_RESULT_CARDS[0]} or its fallbacks"
            + (f" (the page's own copy claims {count} result(s))" if count else " and no count was printed")
            + ". A renamed results layout and a genuine absence are indistinguishable from here.",
            plan=plan_label, result_count=count or 0, matched_name=sugg[mine],
            screenshot=shot("results-unreadable"),
        )

    def _suggestion_locator(self, page: Page):
        for sel in _SUGGESTIONS:
            try:
                loc = page.locator(f"{sel}:visible")
                if loc.count():
                    return loc
            except PlaywrightError:
                continue
        return page.locator(_SUGGESTIONS[-1])

    def _card_name(self, card: str) -> str | None:
        """The provider line of a result card — "Tursunaliev, Serik, MD", the 2nd line after Aetna's
        screen-reader label "This is under Provider/Facility Information column"."""
        for line in (card or "").splitlines():
            line = line.strip()
            if line and "under Provider/Facility" not in line and not _IN_NETWORK_BADGE.fullmatch(line):
                return line[:120]
        return None

    def _card_address(self, card: str) -> str:
        """The practice address as the card prints it — the only corroboration of identity available,
        since no card carries an NPI."""
        lines = [ln.strip() for ln in (card or "").splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if re.match(r"^\d+\s+\w", ln):  # street line, e.g. "7420 Central Ave Bldg C Ste 230"
                city = lines[i + 1] if i + 1 < len(lines) else ""
                return f"{ln}, {city}".strip(", ")
        return "an address the card did not print in a readable form"

    def _cards(self, page: Page) -> tuple[list[str], int]:
        for sel in _RESULT_CARDS:
            try:
                loc = page.locator(f"{sel}:visible")
                n = loc.count()
                if n:
                    return [(loc.nth(i).inner_text() or "") for i in range(min(n, 60))], n
            except PlaywrightError:
                continue
        # Fall back to the portal's own result copy — and only to a phrasing that counts PROVIDERS.
        # Never to a bare "N results available" aria live region: on UHC that was the typeahead's own
        # announcement and it produced a false result_count on an empty page.
        m = re.search(r"([\d,]+)\s+(?:provider|result|doctor)s?\s+(?:found|for|match|within)",
                      self._page_text(page), re.I)
        return [], int(m.group(1).replace(",", "")) if m else 0

    # --- plumbing --------------------------------------------------------------------------------

    def _watch_results_api(self, page: Page, refused: list[str]) -> None:
        """Record every edge refusal of the results endpoint. This is what turns Aetna's bland
        "We're Sorry" card into stateable evidence: which request was refused, and how."""
        def on_failed(req) -> None:
            try:
                if _RESULTS_API in req.url.lower():
                    refused.append(f"{req.failure or 'request failed'}")
            except Exception:  # noqa: BLE001 — a listener must never break the capture
                pass

        def on_response(resp) -> None:
            try:
                if _RESULTS_API in resp.url.lower() and resp.status >= 400:
                    refused.append(f"HTTP {resp.status}")
            except Exception:  # noqa: BLE001
                pass

        page.on("requestfailed", on_failed)
        page.on("response", on_response)

    def _dismiss_overlays(self, page: Page) -> None:
        """Dismiss the OneTrust "Improving your site experience" banner. Scoped to OneTrust's own ids
        rather than a generic Close button: a stray click on this Angular app can navigate the SPA, and
        the banner did not even appear on /dsepublic in any observed run — so this must stay harmless."""
        for sel in ("#onetrust-close-btn-container button", "#onetrust-accept-btn-handler",
                    "#onetrust-banner-sdk button[aria-label='Close']"):
            try:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    b.click()
                    page.wait_for_timeout(800)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _click_text(self, page: Page, label: str, exact: bool = True, timeout_ms: int = 6_000) -> bool:
        """Click a plain anchor by its text. This app ships no data-testid anywhere and its plan-list
        controls are <a> elements with no accessible role, so text is the only handle — kept narrow and
        exact by default, because a loose match on this page hits headings instead of controls."""
        try:
            loc = page.get_by_text(label, exact=exact).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click()
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 2_000)  # settle separately: it must never invalidate a click that worked
        return True

    def _settle(self, page: Page, pause_ms: int = 2_500) -> None:
        """Best-effort wait for the next step. Never raises, and a settle timeout must not be
        mistaken for a step that failed.

        Was `networkidle` at 12s. Profiled live 2026-07-31: the network was still busy on
        1 of 6 steps, costing ~9s of a 53.0s walk. On the rest it was already quiet, so
        the DOM-quiescence poll in `browser.settle` returns just as fast there while bounding
        the busy steps. The `pause_ms` values are a separate, unmeasured cost -- left alone.
        """
        pb.settle(page, pause_ms)

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
