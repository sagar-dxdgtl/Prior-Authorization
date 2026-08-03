"""Aetna Find Medicare Providers — health.aetna.com/ahpublic/medicare-direct.

The Medicare counterpart to `aetna_ahpublic`, which drives Aetna's COMMERCIAL guest directory and
deliberately refuses Medicare lines (`_wrong_line_of_business`) because answering a Medicare member
from the commercial network would be the wrong network. That refusal left every Aetna Medicare row
with no portal evidence at all. This driver is that missing half.

WHY THIS SURFACE IS THE BEST IN THE SYSTEM (verified live 2026-08-03, Cook County IL)

Every other portal makes us infer network status from ABSENCE among a result set, and absence is
weak: it needs the plan pinned, the geography proven, the result set complete, and a namesake ruled
out. This one states the answer outright, and states the two things that qualify it:

    NPI ID: 1780175349                          <- identity, so no namesake can be mistaken for ours
    In Network                                  <- the badge, a POSITIVE statement either way
    Aetna Medicare Premier (PPO) - H5521-016    <- the plan it is answering FOR, echoed back

So a verdict here rests on a positive statement about a named NPI inside a named plan, not on the
absence of a row. That is why `_verdict_from_badge` treats an explicit "Out of Network" badge as
decisive even though this driver could not license an OON from absence alone.

PLAN IDENTIFIERS — the reason this is worth a driver at all

The plan radios carry the CMS contract-PBP-year in their `value`, not just their label:

    label "Aetna Medicare Premier (PPO) - H5521-016"
    value {"planId":"H5521-016-2026","planName":"Aetna Medicare Premier (PPO) - H5521-016"}

`planId` is what we match on, NOT the label. Measured: `identifiers()` reads only `{H5521}` from the
label because the hyphen stops the PBP being captured — and H5521 is shared by THREE Cook County
plans (016 Premier, 086 Signature Extra, 286 Eagle). Matching on the label alone therefore ties, and
a tie resolved by list order is exactly the mis-pin defect fixed in the UHC and Cigna drivers. The
`planId` normalises to `H5521016`, which is contract+PBP and unique.

A Medicare 271 usually carries the H-number, so this is the rare portal where tier-1 identifier
matching actually fires and an out-of-network reading is genuinely licensed.

COUNTY SCOPE — use the MEMBER's ZIP

"Where do you live?" means the member, not the clinic: the page header reads "2026 Individual plan
options for Cook County, 60305" and the lists are county-specific. Same lesson as UHC Find Care, so
the same fix — `member_zip` first, clinic ZIP as the fallback. See portal/models.PortalQuery.

ROBOTS: `health.aetna.com/robots.txt` is `User-agent: * / Allow: /login / Disallow: /`. The
commercial driver avoided this host for that reason. Driving it is a policy decision taken by the
owner on 2026-08-03; it is recorded here so the next reader sees it rather than rediscovering it.
The compliant alternative is Aetna's CMS-mandated PDEX FHIR directory at
`apif1.aetna.com/fhir/v1/providerdirectorydata/` — live, covers Commercial AND Medicare, `/metadata`
public, queries OAuth2-gated (see docs/payer-sources/SIGNUP-CHECKLIST.md). Prefer it once credentials
exist; this driver should then become the fallback, not the primary.

OBSERVED FLOW (each step gated on the previous):

    /ahpublic/medicare-direct   "Where do you live?" -> Google-style suggestion
                                county auto-fills {"countyCode":"17031","countyName":"Cook County"}
                                plan year select · Individual vs Employer radio · View Plans
    /ahpublic/healthplans       radios, value={"planId":"H5521-016-2026",...}   <- PINS THE NETWORK
                                [data-test=plan-selection-continue-btn]
    /ahpublic/find-care         #find-care-search-input -> typeahead [role=option]
    /ahpublic/provider/<id>     NPI + network badge + the plan echoed
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus
from network_probe.portal.plan_match import match_plan_with_fallback as match_plan

ENTRY = "https://health.aetna.com/ahpublic/medicare-direct"

_ADDRESS = "[data-test='user-selected-address-input-input']"
_ADDRESS_OPTION = "[data-test='user-selected-address-search-result-item']"
_COUNTY = "[data-test='public-user-form-county-select-select']"
_PLAN_YEAR = "[data-test='public-user-form-plan-year-select-select']"
_IND_OPTION = "[data-test='public-user-form-ind-option']"
_PLAN_CONTINUE = "[data-test='plan-selection-continue-btn']"
_SEARCH = "#find-care-search-input"

# Overlays that swallow clicks. The Medallia/Kampyle invite is the one that actually bites — it sits
# over the "Individual plan" radio and makes the click time out with "intercepts pointer events".
# Removed from the DOM rather than clicked away: it has no stable close control and re-renders.
_OVERLAY_NODES = (
    "#kampyleInviteContainer",
    "#MDigitalInvitationWrapper",
    "[id*='kampyle']",
    "[id*='MDigital']",
)
_COOKIE_CLOSE = ("#onetrust-accept-btn-handler", "[aria-label='Close']")

#: "In Network" / "Out of Network" as the provider page words them. Anchored on word boundaries so
#: "Out of Network" can never be read as "In Network" by substring.
_IN_BADGE = re.compile(r"\bin[\s\-]?network\b", re.I)
_OUT_BADGE = re.compile(r"\bout[\s\-]?of[\s\-]?network\b", re.I)
_NPI_ON_PAGE = re.compile(r"NPI\s*ID\s*:?\s*(\d{10})", re.I)


@dataclass
class _Pin:
    """The plan we pinned, and whether it is identifier-grade. Same shape as the UHC/Humana pins."""

    name: str | None
    why: str
    confirms: bool = False


def _plan_id_tokens(plan_id: str | None) -> str:
    """`"H5521-016-2026"` -> `"H5521016"` — contract+PBP, the form `plan_match.identifiers` reads.

    The trailing year is dropped deliberately: a 271 names the contract and PBP, never the plan year,
    and leaving it on would make the identifier unmatchable.
    """
    if not plan_id:
        return ""
    m = re.match(r"\s*([HRSE]\d{4})[-\s]?(\d{3})?", plan_id, re.I)
    if not m:
        return plan_id.strip()
    return f"{m.group(1).upper()}{m.group(2) or ''}"


def _normalise_wanted(plan: str | None) -> str:
    """Append the CONCATENATED contract+PBP to the 271 plan string, so tier 1 can see it.

    `identifiers()` reads `H5521-016` as just `H5521` — the hyphen stops the PBP being captured. That
    is contract-level, and one Aetna contract covers several plans in a county: H5521 alone is shared
    by Premier (016), Signature Extra (086) and Eagle (286). Measured on the first live run, the pin
    landed on the right plan only because it happened to be first in the list — the same
    order-dependent tie fixed in the UHC and Cigna drivers, and not something to leave to luck.

    Appending "H5521016" makes the match exact and unique. The original text is kept so the
    distinctive-token tier still works when a 271 carries no identifier at all.
    """
    if not plan:
        return ""
    extra = {
        f"{m.group(1).upper()}{m.group(2)}"
        for m in re.finditer(r"\b([HRSE]\d{4})[-\s]?(\d{3})\b", plan, re.I)
    }
    return f"{plan} {' '.join(sorted(extra))}".strip() if extra else plan


class AetnaMedicareDirectDriver(PortalDriver):
    key = "aetna-medicare-direct"
    portal_name = "Aetna Find Medicare Providers (guest)"
    # The plan list is county-scoped and the county comes from a typed location, so a location is
    # mandatory — but city or state alone can resolve a county, unlike the ZIP-only commercial site.
    location_fields = ("zip_code", "city", "state")
    # The SPA wedges on a restored storage_state: it reopens on a previous county's plan list.
    requires_fresh_context = True

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=kw.pop("plan", q.plan), tin=q.tin,
                status=status, portal_name=self.portal_name, portal_url=page.url, driver=self.key,
                note=note, **kw,
            )

        if not q.plan:
            return result(
                PortalStatus.UNKNOWN,
                "No plan string was supplied, and this directory answers per Medicare plan. Without "
                "one there is no network to search, so neither presence nor absence would mean "
                "anything about this member.",
            )

        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            pass
        except PlaywrightError as e:
            return result(PortalStatus.BLOCKED, f"navigation failed: {type(e).__name__}: {e}",
                          screenshot=shot("nav-failed"))
        self._settle(page, 3_000)
        self._dismiss_overlays(page)
        trail.append("overlays dismissed")

        pin, walked = self._walk_to_plan(page, q, trail)
        shot("plan-walk")
        if pin.name is None:
            return result(
                PortalStatus.UNKNOWN,
                f"could not pin plan {q.plan!r} in Aetna's Medicare plan list ({pin.why}). Every "
                f"option is one county's own Medicare product, so neither presence nor absence in "
                f"an unpinned list is evidence about this member's network.",
                screenshot=shot("no-plan"),
            )
        if not walked:
            return result(PortalStatus.UNKNOWN,
                          f"pinned {pin.name!r} but the provider-search step never rendered.",
                          plan=pin.name, screenshot=shot("no-search"))

        found, detail = self._search_provider(page, q, trail)
        if not found:
            # Absence, and only an identifier-grade pin may read it as out-of-network.
            if pin.confirms:
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"Aetna's Medicare directory for {pin.name!r} returned no listing for "
                    f"{self._who(q)} — out-of-network for this plan. The plan was pinned by "
                    f"identifier ({pin.why}), so this absence is absence from the member's own network.",
                    plan=pin.name, result_count=0, screenshot=shot("absent"),
                )
            return result(
                PortalStatus.UNKNOWN,
                f"Aetna's Medicare directory returned no listing for {self._who(q)} in {pin.name!r}, "
                f"but that plan was matched on names only, not on a plan identifier ({pin.why}) — so "
                f"absence cannot be read as out-of-network.",
                plan=pin.name, result_count=0, screenshot=shot("absent-unpinned"),
            )

        return self._verdict_from_badge(page, q, pin, detail, trail, result, shot)

    # --- steps ---------------------------------------------------------------------------------

    def _walk_to_plan(self, page: Page, q: PortalQuery, trail: list[str]) -> tuple[_Pin, bool]:
        """location → county → year → Individual → View Plans → pin the plan. (pin, reached_search)."""
        # THE MEMBER'S ZIP. "Where do you live?" scopes the plan list to the member's county, and the
        # county lists are disjoint — the clinic ZIP makes a travelling member's plan unpinnable.
        loc = q.member_zip or q.zip_code or q.city or q.state
        if not self._commit_location(page, loc):
            trail.append("location NOT accepted")
            return _Pin(None, "the location was not accepted, so no county plan list loaded"), False
        trail.append("county via member ZIP" if q.member_zip else f"county via clinic location {loc!r}")

        self._select_latest_year(page, trail)
        self._dismiss_overlays(page)
        if not self._click(page, _IND_OPTION):
            trail.append("'Individual plan' NOT clickable")
            return _Pin(None, "the plan-type step could not be completed"), False
        trail.append("plan type: Individual")

        if not self._click(page, "button:has-text('View Plans')"):
            trail.append("'View Plans' NOT clickable")
            return _Pin(None, "the plan list was never requested"), False
        self._settle(page, 4_000)
        self._dismiss_overlays(page)

        options = self._plan_options(page)
        if not options:
            trail.append("no plan options rendered")
            return _Pin(None, "the county returned no Medicare plans"), False
        trail.append(f"{len(options)} plan(s) offered")

        pin = self._pick_plan(page, q.plan, options, trail)
        if pin.name is None:
            return pin, False
        if not self._click(page, _PLAN_CONTINUE):
            trail.append("plan 'Continue' NOT clickable")
            return pin, False
        self._settle(page, 5_000)
        try:
            page.locator(_SEARCH).first.wait_for(state="visible", timeout=30_000)
        except (PlaywrightTimeout, PlaywrightError):
            return pin, False
        trail.append("provider search reached")
        return pin, True

    def _plan_options(self, page: Page) -> list[dict]:
        """Every plan radio as {id, plan_id, label}.

        Reads `value`, not just the label: the label's H-number is contract-only (the hyphen stops
        the PBP being captured) and one contract covers several plans in a county. Radios whose
        `planId` is not a contract id are skipped — the page repeats the same labels under a
        pharmacy section with opaque hashed ids, and those would tie with the real ones.

        Read in ONE `page.evaluate` rather than per-radio Playwright locators. The context default
        timeout is 45s, and a per-radio `label[for=…].inner_text()` that resolves to nothing waits
        that full 45s — with a dozen radios that is minutes of dead time for a page whose DOM is
        already fully rendered. One evaluate is also simply faster: no round trip per element.
        """
        try:
            raw = page.evaluate(
                """() => Array.from(document.querySelectorAll('input[type=radio]')).map(r => {
                     const lab = document.querySelector(`label[for="${r.id}"]`);
                     return { id: r.id || '', value: r.value || '',
                              label: (lab ? lab.innerText : '').trim() };
                   })"""
            ) or []
        except PlaywrightError:
            return []
        out: list[dict] = []
        for row in raw:
            rid, val = row.get("id") or "", (row.get("value") or "").strip()
            if not rid or not val.startswith("{"):
                continue
            try:
                plan_id = (json.loads(val) or {}).get("planId") or ""
            except (ValueError, TypeError):
                continue
            if not re.match(r"\s*[HRSE]\d{4}", plan_id, re.I):
                continue  # hashed pharmacy duplicate, or the "pick later" option
            out.append({"id": rid, "plan_id": plan_id, "label": (row.get("label") or "")[:120]})
        return out

    def _pick_plan(self, page: Page, plan: str | None, options: list[dict], trail: list[str]) -> _Pin:
        """Match the 271 plan against the county's plans, identifier first.

        The string handed to `plan_match` is "<contract+PBP> <label>", so tier 1 sees an unambiguous
        `H5521016` rather than the label's contract-only `H5521`.
        """
        haystack = [f"{_plan_id_tokens(o['plan_id'])} {o['label']}".strip() for o in options]
        m = match_plan(_normalise_wanted(plan), haystack)
        if m is None:
            trail.append(f"no plan matched {plan!r}")
            return _Pin(None, "plan_match declined among this county's Medicare plans")
        chosen = options[m.index]
        self._dismiss_overlays(page)
        if not self._click(page, f"#{chosen['id']}"):
            trail.append(f"plan {chosen['label']!r} not clickable")
            return _Pin(None, f"the plan radio for {chosen['label']!r} was not clickable")
        trail.append(f"plan pinned: {chosen['label']} [{m.basis}]")
        if not m.confirms_network:
            trail.append("pin is names-only — absence cannot be read as out-of-network")
        return _Pin(chosen["label"] or chosen["plan_id"], m.basis, confirms=m.confirms_network)

    def _search_provider(self, page: Page, q: PortalQuery, trail: list[str]) -> tuple[bool, str]:
        """Type the provider, open the matching typeahead entry. (opened, detail_label).

        Searched by SURNAME because the typeahead answers names, not NPIs — the NPI is then read off
        the detail page, which is what actually proves identity. That ordering matters: it means a
        namesake cannot be mistaken for our provider, because we verify after opening, not before.
        """
        terms = [t for t in (q.provider_last_name, q.npi) if t]
        for term in terms:
            self._dismiss_overlays(page)
            try:
                box = page.locator(_SEARCH).first
                box.wait_for(state="visible", timeout=15_000)
                box.click(timeout=10_000)
                box.fill("", timeout=10_000)
                box.type(term, delay=90, timeout=15_000)
            except (PlaywrightTimeout, PlaywrightError):
                continue
            page.wait_for_timeout(4_000)
            opts = page.locator("[role=option]")
            n = min(opts.count(), 12)
            for i in range(n):
                try:
                    text = (opts.nth(i).inner_text() or "").strip()
                except PlaywrightError:
                    continue
                if not self._looks_like_our_provider(text, q):
                    continue
                try:
                    opts.nth(i).click(timeout=10_000)
                except (PlaywrightTimeout, PlaywrightError):
                    continue
                self._settle(page, 4_000)
                trail.append(f"opened {text.splitlines()[0][:60]!r} from a {term!r} search")
                return True, text.splitlines()[0][:80]
            trail.append(f"{n} suggestion(s) for {term!r}, none matching {self._who(q)}")
        return False, ""

    def _looks_like_our_provider(self, text: str, q: PortalQuery) -> bool:
        """Surname must appear as a WORD, and it must be a practitioner rather than a facility.

        Per-token, never substring: Cigna's typeahead answered "Orem" with "Shoaf, Noremi D", and
        "noremi" contains "orem". The NPI check on the detail page is the real proof; this only picks
        which entry to open.
        """
        last = (q.provider_last_name or "").strip().lower()
        if not last:
            return False
        tokens = {t for t in re.split(r"[^a-z]+", text.lower()) if t}
        return last in tokens

    def _verdict_from_badge(self, page: Page, q: PortalQuery, pin: _Pin, detail: str,
                            trail: list[str], result, shot) -> PortalCapture:
        """Read the detail page: NPI proves identity, the badge states the answer.

        Both are required. A badge without a matching NPI is a badge about somebody else, and this
        portal happily opens a same-surname stranger.
        """
        # The profile hydrates in stages: the name paints first, the NPI and the network badge arrive
        # with a later call. Reading once immediately after the click saw neither and reported "no NPI
        # published" for a page that in fact carries both. Poll for the NPI, which is the thing that
        # gates everything else — condition-based, so a fast page costs nothing.
        body = ""
        for _ in range(12):
            try:
                body = page.locator("body").inner_text()
            except PlaywrightError:
                body = ""
            if _NPI_ON_PAGE.search(body):
                break
            page.wait_for_timeout(1_000)
        m = _NPI_ON_PAGE.search(body)
        shown_npi = m.group(1) if m else None
        if shown_npi:
            trail.append(f"detail page NPI {shown_npi}")

        if shown_npi and q.npi and shown_npi != q.npi:
            return result(
                PortalStatus.UNKNOWN,
                f"Aetna listed {detail!r} for surname {q.provider_last_name!r} in {pin.name!r}, but "
                f"its NPI is {shown_npi}, not {q.npi} — a namesake, so this page says nothing about "
                f"our provider. Neither in- nor out-of-network follows.",
                plan=pin.name, matched_name=detail, screenshot=shot("namesake"),
            )
        if not shown_npi:
            return result(
                PortalStatus.UNKNOWN,
                f"Aetna listed {detail!r} in {pin.name!r} but published no NPI on the profile, so "
                f"this cannot be shown to be NPI {q.npi} rather than a namesake.",
                plan=pin.name, matched_name=detail, screenshot=shot("no-npi"),
            )

        # OUT is checked first: "Out of Network" contains "Network", and an in-badge regex that ran
        # first on a loose match would read the negative as a positive.
        if _OUT_BADGE.search(body):
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"Aetna's Medicare directory shows {detail} (NPI {shown_npi}) as OUT of network for "
                f"{pin.name!r}. This is the portal's own positive statement about our NPI inside the "
                f"member's plan, not an inference from absence.",
                plan=pin.name, matched_name=detail, result_count=1, screenshot=shot("badge-out"),
            )
        if _IN_BADGE.search(body):
            return result(
                PortalStatus.IN_NETWORK,
                f"Aetna's Medicare directory shows {detail} (NPI {shown_npi}) as IN network for "
                f"{pin.name!r} — the portal's own statement about our NPI inside the member's plan.",
                plan=pin.name, matched_name=detail, result_count=1, screenshot=shot("badge-in"),
            )
        return result(
            PortalStatus.UNKNOWN,
            f"Aetna listed {detail} (NPI {shown_npi}) in {pin.name!r} but published no network "
            f"badge on the profile, so presence here does not state participation in this plan.",
            plan=pin.name, matched_name=detail, screenshot=shot("no-badge"),
        )

    # --- helpers -------------------------------------------------------------------------------

    def _who(self, q: PortalQuery) -> str:
        name = " ".join(p for p in (q.provider_first_name, q.provider_last_name) if p).strip()
        return f"{name} (NPI {q.npi})" if name else f"NPI {q.npi}"

    def _commit_location(self, page: Page, loc: str | None) -> bool:
        """Type the location and take the first suggestion; the county select then auto-fills."""
        if not loc:
            return False
        # Two attempts, because the suggestion service is a third-party geocoder that intermittently
        # answers only on a later keystroke — the first live failure was exactly this, a fixed 3s wait
        # that expired before the list painted. Typing character-by-character (not fill) is what makes
        # the SPA fire its debounce at all.
        for attempt in range(2):
            try:
                box = page.locator(_ADDRESS).first
                box.wait_for(state="visible", timeout=20_000)
                box.click(timeout=10_000)
                box.fill("", timeout=10_000)
                box.type(loc, delay=110, timeout=20_000)
            except (PlaywrightTimeout, PlaywrightError):
                continue
            # Condition-based: wait for the suggestion, do not assume a duration.
            try:
                page.locator(_ADDRESS_OPTION).first.wait_for(state="visible", timeout=15_000)
                page.locator(_ADDRESS_OPTION).first.click(timeout=10_000)
            except (PlaywrightTimeout, PlaywrightError):
                if attempt == 0:
                    self._dismiss_overlays(page)
                    continue
                return False
            # The county select carries a JSON value once it resolves; empty means it never did.
            for _ in range(10):
                try:
                    if (page.locator(_COUNTY).first.input_value() or "").strip():
                        return True
                except (PlaywrightTimeout, PlaywrightError):
                    pass
                page.wait_for_timeout(800)
        return False

    def _select_latest_year(self, page: Page, trail: list[str]) -> None:
        """Take the highest offered plan year. Aetna defaults to the current one, but during the
        autumn overlap it offers two and the default is not always the one in force."""
        try:
            sel = page.locator(_PLAN_YEAR).first
            years = [o.strip() for o in sel.locator("option").all_inner_texts() if o.strip().isdigit()]
            if years:
                sel.select_option(max(years))
                trail.append(f"plan year {max(years)}")
        except (PlaywrightTimeout, PlaywrightError):
            pass

    def _dismiss_overlays(self, page: Page) -> None:
        for sel in _COOKIE_CLOSE:
            try:
                b = page.locator(sel).first
                if b.is_visible():
                    b.click()
                    page.wait_for_timeout(600)
            except Exception:  # noqa: BLE001 — best-effort; a missing banner must not stop the walk
                continue
        try:
            page.evaluate(
                "(sels) => sels.forEach(s => document.querySelectorAll(s).forEach(e => e.remove()))",
                list(_OVERLAY_NODES),
            )
        except PlaywrightError:
            pass

    def _click(self, page: Page, selector: str, timeout_ms: int = 12_000) -> bool:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click()
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 2_000)
        return True

    def _settle(self, page: Page, pause_ms: int = 2_500) -> None:
        """Fixed pause, never `networkidle`: this SPA polls, so networkidle does not fire and every
        step would pay the full timeout. Same finding as the UHC driver's timing work."""
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass
