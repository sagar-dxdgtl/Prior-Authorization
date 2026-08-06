"""UHC Find Care (guest) — findcare.guest.uhc.com.

Covers three of the eleven Ins Test 3 rows (UHC Medicare Advantage GA, UHC Commercial NHP Access HMO
FL, UHC AARP Medicare Advantage FL) and is the reason this whole layer exists: on Test 2, flex.optum's
FHIR said David Naar (NPI 1760457477) was IN-network for AARP Medicare Advantage FL-0026, while this
portal showed him absent. The portal was right.

Observed structure (2026-07-28): the entry URL hydrates into a Find Care shell with two Abyss-design
inputs — `primary-search-input` (keyword: name, NPI, procedure) and `location-search-input`. Plan
selection is a separate guest step; when the shell exposes it we drive it from the 271's plan string,
and when we cannot confirm which network was searched the verdict stays UNKNOWN rather than OON.

2026-08-06 — THE UN-PINNED DIRECTORY IS GONE, AND SOME PLAN STRINGS CAN NEVER PIN. Both were measured
live on Ins Test 3 row 1 (Manayan, NPI 1902811656, Kennesaw GA 30144, "UHC Medicare Advantage GA",
staff ground truth IN), which took 136s to return "no results".

  * The plan string carries no identifier and exactly ONE distinctive token ("UHC" — MEDICARE and
    ADVANTAGE are non-distinctive, "GA" is under the 3-char floor), so `match_plan` returns None
    against any option list. That is arithmetic, not flakiness, and no amount of driver quality fixes
    it. Client plan strings routinely look like this; see the `_sweep` docstring for the answer.
  * The `/browse` fallback this driver fell back to is DEAD. With no plan pinned it returned 0 for the
    NPI, for the surname, for the full name, and for a control search of "Smith", rendering the
    portal's own `suggestion-list-no-results`. There is no "union of every network UHC sells" to read
    any more — without a plan the search index is empty, so the fallback cost ~80s and bought nothing.

So an unpinnable plan is now answered by SWEEPING the county's own plan list rather than by guessing
one plan or falling back to a directory that answers nothing. See `_sweep`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal import browser as pb
from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus
from network_probe.portal.plan_match import match_plan_with_fallback as match_plan


@dataclass
class _Pin:
    """The plan we pinned, and whether it is strong enough to license an absence reading.

    Same shape as the Humana driver's `_Pin`, deliberately: the two drivers must not disagree about
    what makes an out-of-network reading legitimate.
    """

    name: str | None
    why: str
    confirms: bool = False  # identifier-grade plan match — the only thing that may license an OON
    #: Every plan the walk saw, in portal order, and the URL they were listed at. Carried on the pin
    #: rather than returned separately so `_walk_to_plan` keeps its (pin, trail) shape. This is what
    #: `_sweep` needs when `name` is None: re-navigating to `list_url` restores the committed location
    #: and re-renders the full list, which is what makes searching several plans affordable (measured
    #: 2026-08-06: ~9s to return to the list, against ~54s to walk the flow again).
    labels: tuple[str, ...] = ()
    list_url: str | None = None

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

2026-08-06: it can no longer justify an IN either, because it returns NOTHING at all without a plan
(module docstring). `BROWSE` is kept only as the documented end of the flow — reached by pinning a
plan, never navigated to directly.
"""

ENTRY = "https://findcare.guest.uhc.com/guest-plan-selection"
BROWSE = "https://findcare.guest.uhc.com/guest-plan-selection/browse"

_SEARCH = "[data-testid='primary-search-input']"
_LOCATION = "[data-testid='location-search-input']"
# The portal's OWN empty-state for the typeahead (observed testid: `suggestion-list-no-results`).
# Keyed on the testid rather than the copy, which varies with the query: "No Results Found" on one
# search, "No results found. Phone number and NPI require 10 digits." on a short one.
_NO_RESULTS = "[data-testid*='no-results']"

# The "Select county" modal. UHC raises it INSTEAD of the inline location step when the ZIP spans
# more than one county — reproduced 2026-08-06 for 30101 (Acworth: Bartow/Cherokee/Cobb/Paulding) and
# 30188 (Woodstock), and never for single-county 30144 across ~8 runs. Nothing in `_OVERLAYS` or the
# "Select" commit matches it, so the walk used to die here with zero plans. See `_resolve_county`.
_COUNTY_TRIGGER = (
    "[data-testid='geospatial-county-dropdown-abyss-select-input-input']",
    "[data-testid*='county-dropdown'][data-testid$='select-input-input']",
)
_COUNTY_SUBMIT = (
    "[data-testid='modal-search-button-abyss-button-root']",
    "[role=dialog] button:has-text('Search as guest')",
)

# The plan list the SPA renders from, carrying the CMS identifier the rendered label does not:
# {"planName": "AARP Medicare Advantage from UHC GA-5 (HMO-POS)", "planIdentifier": "H5322-047-001"}
# — contract H5322 / PBP 047 / segment 001. `plan_match` ranks identifiers above every kind of word
# overlap, and a real 271 gives contract+PBP rather than marketing words, so reading this store is
# what lets a 271 pin exactly instead of scoring adjectives.
#
# It is also FRESHER than the DOM. Measured 2026-08-06: re-picking a county left the rendered list
# stuck at the first county's 12 plans while this store correctly moved to 14 (segment 002) and then
# 11 (segment 001). Where the two disagree the store is right — but see `_plan_options` for why a
# disagreement makes us drop the identifiers rather than trust them.
_PLAN_STORE_JS = """() => {
  const raw = sessionStorage.getItem('availablePlans');
  if (!raw) return null;
  const out = [];
  for (const grp of JSON.parse(raw)) {
    for (const p of (grp.planDetails || [])) {
      if (p && p.planName) out.push([p.planName, p.planIdentifier || null]);
    }
  }
  return out;
}"""

# A CMS plan id, as this portal writes it: contract-PBP-segment, e.g. H5322-047-001.
_PLAN_ID = re.compile(r"^([HRSE]\d{4})-(\d{3})-(\d{3})$", re.I)


def _unsegmented(plan_id: str | None) -> bool:
    """Whether this plan has NO service-area segments, i.e. exactly one version of it exists.

    CMS segments partition a plan's service area by county, and segment 000 means "not segmented".
    That is precisely what makes a county irrelevant: measured across all four counties of ZIP 30101,
    every 000 plan (R2604-002-000, H2001-819-000, …) was byte-identical wherever it was offered,
    while every segmented plan split — H5322-047-001 in Cherokee/Cobb against H5322-047-002 in
    Bartow/Paulding. So a 000 plan cannot be a different network depending on which county the
    member lives in, and a segmented one can.
    """
    m = _PLAN_ID.match((plan_id or "").strip())
    return bool(m) and m.group(3) == "000"


def _match_key(name: str, plan_id: str | None) -> str:
    """The string `plan_match` scores: the label plus its identifier in both written forms.

    Hyphenated ("H5322-047-001") survives for a 271 that writes it that way; the concatenated form
    ("H5322047001") is what `plan_match.identifiers` decomposes into contract / contract+PBP /
    contract+PBP+segment, so a 271 naming only the contract still matches.
    """
    if not plan_id:
        return name
    return f"{name} {plan_id} {plan_id.replace('-', '')}"

# Waiting is condition-based, not clock-based: see `_settle` and `_await_suggestions` for the measured
# reasons. Both caps exist only so a wedged portal cannot hang a walk forever — in a healthy run
# neither is reached.
_TYPEAHEAD_DEBOUNCE_MS = 1_200  # let the location autocomplete close before reading provider hits
_TYPEAHEAD_MAX_S = 12.0  # a genuinely empty typeahead must be waited out, not assumed
_TYPEAHEAD_POLL_MS = 300
_SEARCH_INPUT_MAX_MS = 30_000  # plan selection re-renders the shell; waiting costs nothing if it is quick

# Dismissable overlays that sit on top of the first step.
# Dismissable overlays sitting on top of the first step. UHC REDEPLOYS THIS SHELL — treat the list as
# a growing set of handles, not a fixed description of the page.
#
# 2026-07-31: UHC shipped a "new guest experience" mid-session. A modal headed "Welcome to the new
# guest experience!" with a Get started button appeared over the coverage-type cards, carrying none of
# the testids below. The cards were still there and still correct; they were simply covered. All three
# UHC rows of Ins Test 3 then died at "coverage type 'Medicare' NOT clickable" after ~198s of retries,
# and a query that had returned OUT_OF_NETWORK three times that same afternoon began failing with no
# code change in between. Hence the name-based entries: a vendor testid is not durable across a
# redeploy, but the button a human is asked to press is.
_OVERLAYS = (
    "[data-testid='guest-start-modal-abyss-modal-base-close-button']",
    "[data-testid='guest-coachmark-container-abyss-coachmark-close-button']",
    # Scoped to a dialog so a "Get started" elsewhere on the site is never clicked.
    "[role=dialog] button:has-text('Get started')",
    # Generic fallbacks for the next rename: any close control inside a modal or coachmark.
    "[data-testid*='modal'] [data-testid*='close']",
    "[data-testid*='coachmark'] [data-testid*='close']",
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


# How many plans an unpinnable plan string is worth searching. Each extra plan costs ~15s (re-navigate
# to the list, re-pin, re-search), so this is the knob that keeps a sweep near the cost of the single
# walk it replaces rather than 12x it. Four is enough to span GA's product families; the note always
# states how many of how many were searched, because a sample reported as a whole list is a lie.
_SWEEP_MAX_PLANS = 4


def _sweep_indices(total: int, cap: int) -> list[int]:
    """Up to `cap` positions spread ACROSS the list, not taken off the top.

    The plan list is alphabetical, so the top N tend to be one product family — six of Cobb County's
    first eight are Dual Complete variants. Sampling one family and calling the result plan-invariance
    would be exactly the reasoning this driver refuses elsewhere. Spreading the sample is what makes
    "present in every plan searched" mean anything.
    """
    if total <= 0 or cap <= 0:
        return []
    if total <= cap:
        return list(range(total))
    if cap == 1:
        return [0]
    step = (total - 1) / (cap - 1)
    return sorted({round(i * step) for i in range(cap)})


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
        except PlaywrightTimeout:
            pass
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))
        # The entry load paid the same never-firing networkidle as every step did — a 10s ceiling that
        # the _settle profiling never counted because it only instrumented _settle. Same treatment,
        # keeping the 3s the Abyss shell needs to hydrate after load.
        self._settle(page, 3_000)

        pin, trail = self._walk_to_plan(page, q)
        trail_box.extend(trail)
        shot("plan-walk")
        plan_confirmed = pin.name is not None
        if not plan_confirmed:
            # NOT a fall-through to /browse any more: that directory answers nothing without a plan
            # (module docstring). Ask the portal the question it CAN answer instead — several of the
            # county's own plans, one at a time.
            return self._sweep(page, q, pin, shot, result)

        # Absence may only be read as OON when the pin is identifier-grade. A names-only pin still
        # scopes the *search* — it just cannot license a negative. Only the pinned path below reads
        # absence at all; the sweep never does, so this is computed after its early return.
        blockers = self._absence_blockers(pin)

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

        # Once per pinned plan, not once per term. Plan selection leaves the search page's own location
        # field empty (verified live), so it does need setting — but `_run_search` used to redo it for
        # every term, which was three 4.1s round trips per capture for one committed location.
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
            if count and not blockers:
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"UHC Find Care returned {count} result(s) for {kind} {term!r} in plan "
                    f"{pin.name!r} near {q.zip_code or 'the clinic'}, and NPI {q.npi} is not among "
                    f"them — out-of-network for this plan. The plan was pinned by identifier "
                    f"({pin.why}), so this absence is absence from the member's own network.",
                    result_count=count, screenshot=shot(f"absent-{kind}"),
                )
            if count:
                return result(
                    PortalStatus.UNKNOWN,
                    f"UHC Find Care returned {count} result(s) for {kind} {term!r} without NPI "
                    f"{q.npi}, but absence cannot be read as out-of-network here: "
                    + "; ".join(blockers) + ".",
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

    def _walk_to_plan(self, page: Page, q: PortalQuery) -> tuple[_Pin, list[str]]:
        """Walk coverage type → type of care → county → plan list, pinning the member's network.

        Returns (pin, trail). `trail` records each step reached so a capture that stops early says
        exactly where — the portals redesign often, and a silent partial walk that then reports OON
        would be the worst possible failure mode. `pin.confirms` is the identifier-grade gate: only
        it may license reading an absence as OUT_OF_NETWORK.
        """
        trail: list[str] = []
        self._dismiss_overlays(page)
        trail.append("overlays dismissed")

        sel, label = _COVERAGE[self._coverage_type(q)]
        if not self._click_any(page, sel, label):
            return _Pin(None, f"coverage type {label!r} NOT clickable"), trail + [
                f"coverage type {label!r} NOT clickable"]
        trail.append(f"coverage: {label}")

        # "Type of care" — always Medical for a physician network check.
        if not self._click_any(page, "[data-testid*='medical']", "Medical"):
            return _Pin(None, "type of care 'Medical' NOT clickable"), trail + [
                "type of care 'Medical' NOT clickable"]
        trail.append("care: Medical")

        # THE MEMBER'S ZIP, not the clinic's. This step is UHC's "Select the area where you live",
        # and its plan list is scoped to the member's county — measured 2026-08-03, Port St. Lucie
        # 34986 offers 12 Medicare plans and Miami 33101 a different 17, with the member's own
        # FL-0026 in neither. A patient who travels to a specialist outside their home county
        # therefore had an unpinnable plan, and the walk could never confirm a network however
        # correct the 271 was. Falls back to the clinic ZIP, which is the old behaviour and still
        # right whenever the member is treated where they live.
        plan_zip = q.member_zip or q.zip_code
        if plan_zip and self._commit_location(page, plan_zip):
            # The member's ZIP is NOT echoed — this note is stored and displayed. Which county was
            # searched is auditable from the screenshot, which shows the portal's own echo.
            trail.append(
                "county via member ZIP" if q.member_zip else f"county via clinic ZIP {q.zip_code}"
            )
        # A multi-county ZIP raises the "Select county" modal instead of committing inline. It must be
        # answered — or declined — before anything else, because the Select button below is not part
        # of it and the plan list stays empty until it is dealt with.
        if self._county_modal(page):
            resolved, why = self._resolve_county(page, q)
            trail.append(why)
            if not resolved:
                return _Pin(None, why), trail
        # "Plans will populate upon location selection" — the plan list does not exist until the
        # location is *committed* with the Select button. Filling the ZIP is not enough.
        elif self._click_any(page, "[data-testid*='location-select']", "Select", timeout_ms=6_000):
            trail.append("location committed (Select)")
        self._dismiss_overlays(page)  # a fresh coachmark appears on the plan-selection step

        # Read the list BEFORE picking, so a plan string that pins nothing still leaves `_sweep` the
        # options and the URL it needs. Costs one locator read (measured: 0.0s).
        options, list_url = self._plan_options(page)
        labels = tuple(name for name, _ in options)

        m = self._pick_plan(page, q.plan, options)
        if m is not None:
            # The basis goes in the trail, not just the label: whether the pin was an identifier
            # match or a word match is what decides if an absence may be read as OON, so a reader
            # has to be able to see which one happened.
            trail.append(f"plan pinned: {m.label} [{m.basis}]")
            if not m.confirms_network:
                trail.append("pin is names-only — absence cannot be read as out-of-network")
            return _Pin(m.label, m.basis, confirms=m.confirms_network,
                        labels=labels, list_url=list_url), trail
        return _Pin(None, f"no plan matched {q.plan!r}", labels=labels, list_url=list_url), trail + [
            f"no plan matched {q.plan!r} in the plan list ({len(labels)} offered)"]

    # --- the county step ---------------------------------------------------------------------------

    def _county_modal(self, page: Page) -> bool:
        """Whether UHC is asking which county the member lives in."""
        for sel in _COUNTY_TRIGGER:
            try:
                if page.locator(sel).first.is_visible():
                    return True
            except (PlaywrightTimeout, PlaywrightError, AttributeError):
                continue
        return False

    def _county_options(self, page: Page) -> list[str]:
        """The counties on offer, in portal order. Opens the dropdown to read them."""
        for sel in _COUNTY_TRIGGER:
            try:
                page.locator(sel).first.click()
                page.wait_for_timeout(1_500)
                opts = page.locator("[role=option]:visible")
                names = [(opts.nth(i).inner_text() or "").strip() for i in range(opts.count())]
                if names:
                    return names
            except (PlaywrightTimeout, PlaywrightError, AttributeError):
                continue
        return []

    def _choose_county(self, page: Page, index: int) -> bool:
        """Pick the index-th county and submit the modal as a guest."""
        try:
            opts = page.locator("[role=option]:visible")
            if opts.count() <= index:
                return False
            opts.nth(index).click()
            page.wait_for_timeout(1_000)
        except (PlaywrightTimeout, PlaywrightError):
            return False
        for sel in _COUNTY_SUBMIT:
            try:
                btn = page.locator(sel).first
                btn.wait_for(state="visible", timeout=6_000)
                btn.click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            self._settle(page, 3_000)
            self._dismiss_overlays(page)
            return True
        return False

    def _resolve_county(self, page: Page, q: PortalQuery) -> tuple[bool, str]:
        """Answer the county modal, or refuse to. Returns (resolved, reason-for-the-trail).

        A ZIP is not a county, and on this portal the county picks the PLAN LIST. Measured across
        fresh contexts for 30101 (Acworth): Cherokee offers 11 plans, Cobb 12, Bartow and Paulding 14,
        and the products differ — Cobb alone carries GA-D001, only Bartow/Paulding carry GA-2 (PPO).
        Choosing one on the member's behalf is choosing a network for them, which is the single
        failure this layer exists to prevent.

        But the county only matters when it can change the ANSWER, and that is checkable. UHC's plan
        ids carry the CMS segment, and segments are exactly how a plan's service area is split by
        county: `H5322-047-001` in Cherokee/Cobb against `H5322-047-002` in Bartow/Paulding, while
        every unsegmented `-000` plan is one and the same wherever it is sold. So when the member's
        plan resolves here to an unsegmented id, no county could have produced a different plan or a
        different network, and there is nothing left to guess — proceed. When it resolves to a
        segmented id, or does not resolve at all, the county genuinely decides and we decline, naming
        the counties so a human who knows where the member lives can finish it.

        Why the check runs against THIS county's list rather than all of them: only the first county
        chosen in a session can actually be searched. Re-picking through the Edit control updates the
        store but leaves the rendered list on the first pick, and a reload reverts the choice
        outright (measured 2026-08-06). The unsegmented test needs one list, which is the one list
        we can trust.
        """
        counties = self._county_options(page)
        if not counties:
            return False, ("UHC asked which county the member lives in, but the modal listed none — "
                           "the plan list cannot be reached")
        if len(counties) == 1:
            if self._choose_county(page, 0):
                return True, f"county {counties[0]} (the only one for this ZIP)"
            return False, f"county {counties[0]} was the only option but could not be selected"

        named = "; ".join(counties)
        if not self._choose_county(page, 0):
            return False, (f"the member's ZIP spans {len(counties)} counties ({named}) and none "
                           f"could be selected to read a plan list")
        options, _ = self._plan_options(page)
        m = self._resolve_plan(options, q.plan)
        if m is not None:
            plan_id = options[m.index][1]
            if _unsegmented(plan_id):
                return True, (
                    f"county {counties[0]} of {len(counties)} the ZIP spans ({named}) — safe: the "
                    f"member's plan is {options[m.index][0]} ({plan_id}), which CMS does not segment "
                    f"by county, so no other county could have given a different plan or network"
                )
            return False, (
                f"the member's ZIP spans {len(counties)} counties ({named}) and their plan is "
                f"{options[m.index][0]} ({plan_id}) — a county-SEGMENTED plan, so which county they "
                f"live in decides which segment's network applies and it cannot be read off a ZIP"
            )
        return False, (
            f"the member's ZIP spans {len(counties)} counties ({named}) whose UHC plan lists "
            f"genuinely differ, and the plan string {q.plan!r} does not resolve to any plan in "
            f"{counties[0]}, so neither the county nor the plan can be pinned"
        )

    def _plan_list(self, page: Page) -> tuple[tuple[str, ...], str | None]:
        """The plan names on screen, in portal order, plus the URL they are listed at.

        Returns `((), None)` for any page that cannot be read — a driver that could not see the list
        must degrade to "no plan list", never to a partial one that a sweep would treat as complete.
        """
        options, url = self._plan_options(page)
        return tuple(name for name, _ in options), url

    def _plan_options(self, page: Page) -> tuple[tuple[tuple[str, str | None], ...], str | None]:
        """(plan name, CMS plan id) per option, in portal order, plus the list URL.

        The rendered list is what we must CLICK, so it defines the order and the count. The store
        (`_PLAN_STORE_JS`) supplies the identifiers the labels lack. They are zipped only when they
        agree on length: when a stale render disagrees with the store — which happens after a county
        is re-picked — pairing them by position would attach one plan's identifier to another plan's
        row, which is a worse error than having no identifier at all. So a mismatch drops the
        identifiers and keeps the rendered labels, and the driver degrades to name matching.
        """
        labels: tuple[str, ...] = ()
        url = None
        for sel in _PLAN_SURFACES:
            try:
                loc = page.locator(f"{sel}:visible")
                if loc.count():
                    labels, url = tuple(self._plan_labels(loc)), page.url
                    break
            except (PlaywrightTimeout, PlaywrightError, AttributeError):
                continue
        if not labels:
            return (), None
        try:
            stored = page.evaluate(_PLAN_STORE_JS)
        except (PlaywrightTimeout, PlaywrightError, AttributeError):
            stored = None
        if stored and len(stored) == len(labels):
            return tuple((labels[i], stored[i][1]) for i in range(len(labels))), url
        return tuple((name, None) for name in labels), url

    # --- the unpinnable-plan path ---------------------------------------------------------------

    def _sweep(self, page: Page, q: PortalQuery, pin: _Pin, shot, result) -> PortalCapture:
        """The plan string named no plan. Ask about several of the county's plans instead of one.

        A 271 plan string like "UHC Medicare Advantage GA" carries no identifier and too few
        distinctive words to pick a plan out of a 12-option list, and `plan_match` rightly refuses to
        guess — pinning the wrong plan produces a confident verdict about a network the member is not
        in, the worst failure this layer has. But refusing to guess used to mean refusing to answer.

        It does not have to. The question "is this provider in the member's network" has the same
        answer under every plan the member could plausibly hold, whenever their participation does
        not vary across those plans — and that is checkable. Pin each of a spread of the county's
        plans in turn and search:

          * present in every one  -> IN_NETWORK. Which plan the member holds is still unknown, and no
            longer matters: the answer is invariant across the plans searched, so nothing was guessed.
            Verified live 2026-08-06 for Manayan across HMO-POS / Regional PPO / D-SNP / Group PPO.
          * presence VARIES       -> UNKNOWN, naming the plan that disagreed. Here the plan string IS
            load-bearing and we cannot pin it, so declining is the only honest answer.
          * present in none       -> UNKNOWN, never OON. The sweep is a bounded SAMPLE, so absence
            across it is not absence from the member's plan, which may be one of the ones never
            searched. Same asymmetry `_absence_blockers` enforces on the pinned path: presence is
            positive evidence wherever it is found, absence needs proof we searched the right network.
        """
        if not pin.labels or not pin.list_url:
            # `pin.why` carries whatever stopped the walk — an unanswerable county, an empty list —
            # and it must survive into the note, or the capture reads as a mysterious blank.
            return result(
                PortalStatus.UNKNOWN,
                f"UHC Find Care could not be searched for NPI {q.npi}: {pin.why}. Without a plan "
                f"list there is no network to search in, and UHC's un-pinned directory is not a "
                f"fallback — without a plan it returns no results for anyone.",
                screenshot=shot("no-plan-list"),
            )

        total = len(pin.labels)
        seen: list[tuple[str, bool, str | None]] = []
        for n, i in enumerate(_sweep_indices(total, _SWEEP_MAX_PLANS)):
            if not self._repin(page, pin.list_url, i, first=(n == 0), expect=pin.labels[i]):
                break  # a wedged or shifted list is fewer plans searched, not a negative result
            found, _count, matched, _kind = self._find_provider(page, q)
            seen.append((pin.labels[i], found, matched))
            if len({f for _, f, _ in seen}) > 1:
                break  # presence already varies; further plans cannot change the answer

        if not seen:
            return result(
                PortalStatus.UNKNOWN,
                f"The plan string {q.plan!r} matched none of the {total} plans offered, and no plan "
                f"could be selected to search NPI {q.npi} in.",
                screenshot=shot("sweep-no-pin"),
            )

        searched = len(seen)
        scope = (f"searched {searched} of {total} plans offered near "
                 f"{q.zip_code or 'the clinic'}, spread across the list")
        present = [lab for lab, f, _ in seen if f]
        absent = [lab for lab, f, _ in seen if not f]
        matched = next((m for _, f, m in seen if f and m), None)

        if not absent:
            return result(
                PortalStatus.IN_NETWORK,
                f"NPI {q.npi} is listed in UHC Find Care under EVERY plan searched — {scope}: "
                + "; ".join(present)
                + f". Listed as {matched!r}. The 271 named {q.plan!r}, which carries no plan "
                f"identifier and so could not be pinned to one of these; it did not need to be, "
                f"because the answer does not depend on which plan the member holds.",
                result_count=searched, matched_name=matched, screenshot=shot("sweep-in"),
            )
        if present:
            return result(
                PortalStatus.UNKNOWN,
                f"NPI {q.npi}'s network status DEPENDS on the plan, and {q.plan!r} carries no "
                f"identifier that could pin one — {scope}. Listed under: " + "; ".join(present)
                + ". NOT listed under: " + "; ".join(absent)
                + ". Supply the member's plan identifier (contract/PBP or market code such as GA-5) "
                "to settle it.",
                result_count=searched, matched_name=matched, screenshot=shot("sweep-split"),
            )
        return result(
            PortalStatus.UNKNOWN,
            f"NPI {q.npi} was not listed under any plan searched — {scope}: " + "; ".join(absent)
            + f". That is NOT out-of-network: {searched} of {total} plans is a sample, so the "
            f"member's own plan may be one never searched, and {q.plan!r} carries no identifier to "
            f"pin it. Supply the plan identifier to make an absence reading valid.",
            result_count=0, screenshot=shot("sweep-absent"),
        )

    def _repin(self, page: Page, list_url: str, index: int, *, first: bool = False,
               expect: str | None = None) -> bool:
        """Select the index-th plan, returning to the plan list first when we have already left it.

        The return trip is a plain `goto`: the committed location survives it and the full list
        re-renders (verified live 2026-08-06, ~9s), which is what keeps a sweep near the cost of the
        single walk it replaces instead of multiplying it.

        `expect` is the label the caller believes sits at `index`, and it is checked AFTER navigating.
        The re-render is not always faithful: measured live, a re-navigation returned a ONE-plan list
        where twelve had been captured. Clicking position `index` of a list that has shifted searches
        one plan while the note names another — a confident answer about a plan we never looked at,
        which is worse than searching one plan fewer. So a shifted list declines instead.
        """
        if not first:
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=45_000)
            except (PlaywrightTimeout, PlaywrightError):
                return False
            self._settle(page, 2_500)
            self._dismiss_overlays(page)
            if expect is not None:
                now, _ = self._plan_list(page)
                if index >= len(now) or now[index] != expect:
                    return False
        for sel in _PLAN_SURFACES:
            try:
                loc = page.locator(f"{sel}:visible")
                if loc.count() <= index:
                    continue
                loc.nth(index).click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            self._settle(page, 3_000)
            return True
        return False

    def _find_provider(self, page: Page, q: PortalQuery) -> tuple[bool, int, str | None, str | None]:
        """Run this driver's search terms against whatever plan is currently pinned.

        Returns (found, result_count, matched_name, term_kind). Absence detail is deliberately thin —
        the sweep only ever reads presence, because a bounded sample cannot license an absence verdict.
        """
        try:
            search = page.locator(_SEARCH).first
            search.wait_for(state="visible", timeout=_SEARCH_INPUT_MAX_MS)
        except (PlaywrightTimeout, PlaywrightError):
            return False, 0, None, None
        if q.zip_code:
            self._set_location(page, q.zip_code)
        widest = 0
        for term, kind in self._search_terms(q):
            found, count, matched = self._run_search(page, search, term, q)
            if found:
                return True, count, matched, kind
            widest = max(widest, count)
        return False, widest, None, None

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
        """Try every handle. One that matches nothing — or an element that detaches while we look at
        it — must never stop the rest: the overlay we could not dismiss is rarely the blocking one,
        and giving up early is how a covered page turns into "coverage type NOT clickable"."""
        for sel in _OVERLAYS:
            try:
                b = page.locator(sel).first
                if b.is_visible():
                    b.click()
                    page.wait_for_timeout(1_000)
            except Exception:  # noqa: BLE001 — best-effort by design; see the docstring
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

    def choose_plan(self, labels: list[str], plan: str | None):
        """Resolve the 271's plan string onto the portal's own labels. Returns a `PlanMatch` or None.

        Delegates entirely to `portal/plan_match`, which ranks identifiers (contract / PBP / market
        code such as FL-0026) above word overlap and refuses ties outright. The driver used to score
        ≥4-char token overlap itself, which is how "AARP Medicare Advantage CareFlex from UHC FL-35
        (HMO-POS)" was pinned for a member on FL-0026 (PPO): AARP, MEDICARE, ADVANTAGE and FROM are
        shared by every UHC Medicare product and identify nothing.
        """
        if not plan or not labels:
            return None
        return match_plan(plan, labels)

    def _plan_labels(self, loc) -> list[str]:
        """Read every visible option label, in portal order. No cap.

        The old scorer stopped at 12; UHC's Florida Medicare list is longer than that, so the
        member's own plan could sit outside the window and lose to a filler product that merely
        shared marketing words.
        """
        out: list[str] = []
        for i in range(loc.count()):
            try:
                out.append((loc.nth(i).inner_text() or "").strip().splitlines()[0][:120])
            except PlaywrightError:
                out.append("")
        return out

    def _resolve_plan(self, options, plan: str | None):
        """Match the 271's plan against these options — identifiers first, then bare names.

        Two passes, deliberately. The enriched key is what reaches tier 1, the identifier tier, which
        is the only thing that can license an out-of-network reading. But decorating a label with its
        id also defeats tier 1.5, which asks whether the plan string simply IS one of the labels:
        "UnitedHealthcare Group Medicare Advantage (PPO)" is exactly a label but is not exactly
        "UnitedHealthcare Group Medicare Advantage (PPO) H2001-819-000 H2001819000". Losing an exact
        name match to our own decoration would be a regression, so the names get their own pass.
        """
        if not options or not plan:
            return None
        m = self.choose_plan([_match_key(n, i) for n, i in options], plan)
        if m is None:
            m = self.choose_plan([n for n, _ in options], plan)
        if m is not None:
            m.label = options[m.index][0]  # report the plan's own name, never the matching key
        return m

    def _pick_plan(self, page: Page, plan: str | None, options=None):
        """Pin the member's plan in the portal's list. Returns the `PlanMatch` clicked, or None.

        `options` are the (name, plan id) pairs already read by the caller; matching runs against
        `_match_key` so the CMS identifier counts, which is the only thing that reaches tier 1 and
        therefore the only thing that can license an out-of-network reading.
        """
        if not plan:
            return None
        for sel in _PLAN_SURFACES:
            try:
                loc = page.locator(f"{sel}:visible")
                if not loc.count():
                    continue
                opts = options if options is not None else [(x, None) for x in self._plan_labels(loc)]
                if len(opts) != loc.count():
                    opts = [(x, None) for x in self._plan_labels(loc)]
                m = self._resolve_plan(opts, plan)
                if m is None:
                    continue
                loc.nth(m.index).click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            self._settle(page, 3_000)  # post-click, so a slow settle cannot discard a pinned plan
            return m
        return None

    def _absence_blockers(self, pin) -> list[str]:
        """Everything that must hold before "not in the results" may be called OUT_OF_NETWORK.
        Empty list = it may. Mirrors the same gate in the Humana driver.

        Absence is the asymmetric direction: absence from the WRONG network is indistinguishable
        from absence from the right one, so it needs an identifier-grade pin. Presence does not —
        finding the provider in the list that was searched is positive evidence either way.
        """
        if not getattr(pin, "name", None):
            return [f"no plan was pinned ({getattr(pin, 'why', 'no match')}), so absence is absence "
                    f"from an unknown scope — UHC's un-pinned directory is the union of every "
                    f"network it sells"]
        if not getattr(pin, "confirms", False):
            return [f"the plan matched {pin.name!r} on names only, not on a plan identifier "
                    f"({getattr(pin, 'why', '')}); every UHC Medicare product shares the words AARP, "
                    f"Medicare and Advantage, so this may not be the member's network"]
        return []

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
        must be refilled — but by the CALLER, once per pinned plan. Doing it here re-committed the same
        location for every term, three 4.1s round trips per capture for one unchanged ZIP.
        """
        try:
            search.click()
            search.fill(term)
        except (PlaywrightTimeout, PlaywrightError):
            return False, 0, None
        cands = self._await_suggestions(page)
        # Fast path: the typeahead has already named our provider, so the results page cannot add
        # anything. `_cards` has returned 0 on EVERY live observation of this portal — the suggestion
        # list is its result surface — while pressing Enter and settling costs ~5s, which a sweep pays
        # once per plan searched. Taken only on a positive identification: an empty or stranger-only
        # list still opens the results page, because that is the surface that might carry more.
        ours_early, _ = self.identify(cands, q)
        if ours_early:
            return True, len(cands), ours_early
        if cands and q.npi and q.npi in self._page_text(page):
            return True, len(cands), self._matched_name(cands, q) or f"NPI {q.npi} present on page"

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

        The deadline is the LAST resort, not the normal exit. The portal renders its own empty-state
        when it has nothing, and reading it is what separates "has not answered yet" from "answered:
        nothing" — without that, every fruitless search waited the full 12s ceiling. Measured
        2026-08-06: 13.3s per term, three terms per capture, on a page that had already said so.
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
            # Checked AFTER suggestions, so a page carrying both a stale empty-state and fresh hits
            # is read as hits. Presence is the direction that must never be lost to a race.
            if self._says_no_results(page):
                return []
            try:
                page.wait_for_timeout(_TYPEAHEAD_POLL_MS)
            except PlaywrightError:
                break
        return []

    def _says_no_results(self, page: Page) -> bool:
        """Whether the portal has rendered its own empty-state for the typeahead.

        Fails closed: a DOM that cannot be read is not the portal saying "nothing", and treating it
        as such would turn a detached element into a false absence.
        """
        try:
            return bool(page.locator(_NO_RESULTS).first.is_visible())
        except (PlaywrightTimeout, PlaywrightError):
            return False

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
