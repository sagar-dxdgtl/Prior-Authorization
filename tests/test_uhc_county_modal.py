"""UHC's "Select county" modal — the step that killed the walk for a member on a multi-county ZIP.

Caught live 2026-08-06 from the app, plan "LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PPO)".
The stored trail ends:

    ... → care: Medical → county via member ZIP → no plan matched '...' in the plan list (0 offered)

with no "location committed (Select)" — the walk never reached a plan list at all. The screenshot
shows why: a modal headed **Select county**, carrying a dropdown
(`geospatial-county-dropdown-abyss-select-input-input`) and a **Search as guest** submit
(`modal-search-button-abyss-button-root`). `_click_any("[data-testid*='location-select']", "Select")`
matches none of it and returns False, so the walk dies with zero plans.

Reproduced deterministically: the modal appears for a ZIP spanning several counties (30101 Acworth,
30188 Woodstock) and never for a single-county one (30144 Kennesaw, Cobb only, ~8 clean runs).

**The county is load-bearing.** Measured across fresh contexts for 30101:

    Cherokee 11 plans   (GA-F001, no GA-D001)
    Cobb     12 plans   (GA-D001 + GA-F001)
    Bartow   14 plans   (GA-2 (PPO), GA-MA01 — absent from Cobb)
    Paulding 14 plans

so picking one would be the wrong-network guess this layer exists to prevent. Hence: select the
county when there is exactly one, decline when there are several.

Why we do not compare the counties in-session: the portal CACHES the first county's plan list. Going
back via the Edit control and choosing Paulding or Cherokee returned Cobb's 12 both times, identical
to the first selection. Only a fresh browser context reproduces the real per-county lists. An
in-session comparison would therefore compare four copies of one list and "prove" the county did not
matter — a stale UI manufacturing a confident wrong answer.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from network_probe.portal.drivers.uhc_findcare import UhcFindCareDriver
from network_probe.portal.models import PortalQuery, PortalStatus

ACWORTH_COUNTIES = ["Bartow, GA", "Cherokee, GA", "Cobb, GA", "Paulding, GA"]

MEMBER = PortalQuery(
    payer_key="unitedhealthcare-ga-atlanta", npi="1902811656", provider_first_name="Conrad",
    provider_last_name="Manayan", plan="LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PPO)",
    state="GA", city="Kennesaw", zip_code="30144", member_zip="30101",
)


# The real Cobb list with the CMS ids from sessionStorage.availablePlans, read live 2026-08-06.
COBB_OPTIONS = [
    ("AARP Medicare Advantage from UHC GA-5 (HMO-POS)", "H5322-047-001"),      # segmented
    ("UHC Complete Care Support GS-1A (Regional PPO C-SNP)", "R2604-002-000"),
    ("UHC Dual Complete GA-D001 (PPO D-SNP)", "H2406-052-000"),
    ("UHC Dual Complete GA-S1 (PPO D-SNP)", "H3256-005-001"),                  # segmented
    ("UnitedHealthcare Group Medicare Advantage (PPO)", "H2001-819-000"),
]


class _CountyDriver(UhcFindCareDriver):
    """Scripts the modal so the DECISION is what gets tested, not Playwright."""

    def __init__(self, counties, *, modal=True, options=()):
        self.counties = counties
        self.modal = modal
        self.options = tuple(options)
        self.chosen: list[int] = []

    def _county_modal(self, page):
        return self.modal

    def _county_options(self, page):
        return list(self.counties)

    def _choose_county(self, page, index):
        self.chosen.append(index)
        return True

    def _plan_options(self, page):
        return self.options, "url"


def _resolve(d, plan="UnitedHealthcare Group Medicare Advantage (PPO)"):
    q = PortalQuery(payer_key="uhc", npi="1902811656", plan=plan, zip_code="30144",
                    member_zip="30101")
    return d._resolve_county(object(), q)


def test_one_county_is_selected_and_the_walk_continues():
    """No ambiguity: a ZIP inside a single county needs no choice made on the member's behalf."""
    d = _CountyDriver(["Cobb, GA"])
    ok, why = _resolve(d)
    assert ok is True
    assert d.chosen == [0]
    assert "Cobb, GA" in why


def test_an_unsegmented_plan_makes_the_county_irrelevant_and_proceeds():
    """H2001-819-000 is segment 000 — CMS does not split it by county, and it was byte-identical in
    all four counties of ZIP 30101. No other county could have given a different network, so there
    is nothing to guess and declining would be needless."""
    d = _CountyDriver(ACWORTH_COUNTIES, options=COBB_OPTIONS)
    ok, why = _resolve(d, "UnitedHealthcare Group Medicare Advantage (PPO)")
    assert ok is True
    assert d.chosen == [0]
    assert "H2001-819-000" in why
    assert "segment" in why.lower() or "does not segment" in why.lower()


def test_a_county_segmented_plan_still_declines():
    """H5322-047-001 in Cherokee/Cobb vs -002 in Bartow/Paulding: the county picks the segment, so
    it picks the network."""
    d = _CountyDriver(ACWORTH_COUNTIES, options=COBB_OPTIONS)
    ok, why = _resolve(d, "AARP Medicare Advantage from UHC GA-5 (HMO-POS)")
    assert ok is False
    assert "H5322-047-001" in why
    assert "segment" in why.lower()


def test_a_plan_that_resolves_nowhere_declines_and_names_the_counties():
    d = _CountyDriver(ACWORTH_COUNTIES, options=COBB_OPTIONS)
    ok, why = _resolve(d, "Some Plan Nobody Sells")
    assert ok is False
    for county in ACWORTH_COUNTIES:
        assert county in why


def test_several_counties_with_no_readable_plan_list_decline():
    """Without a list there is nothing to test the segment against."""
    d = _CountyDriver(ACWORTH_COUNTIES, options=())
    ok, why = _resolve(d)
    assert ok is False
    assert "Bartow, GA" in why


def test_the_decline_names_every_county_so_a_human_can_finish_it():
    """§8: a staffer who knows where the member lives can act on this; 'ambiguous' alone cannot."""
    d = _CountyDriver(ACWORTH_COUNTIES, options=COBB_OPTIONS)
    _, why = _resolve(d, "AARP Medicare Advantage from UHC GA-5 (HMO-POS)")
    for county in ACWORTH_COUNTIES:
        assert county in why


def test_an_empty_county_list_is_a_decline_not_a_selection():
    """A modal we cannot read must never fall through as if it had been answered."""
    d = _CountyDriver([])
    ok, why = _resolve(d)
    assert ok is False and d.chosen == []
    assert why


def test_a_failed_selection_is_reported_as_a_decline():
    """Clicking the only county can still fail; that is not a silent success."""

    class _Stuck(_CountyDriver):
        def _choose_county(self, page, index):
            return False

    ok, why = _resolve(_Stuck(["Cobb, GA"]))
    assert ok is False
    assert "cobb" in why.lower()


# --- the segment rule itself ----------------------------------------------------------------------


@pytest.mark.parametrize("plan_id,expected", [
    ("H2001-819-000", True), ("R2604-002-000", True), ("H2406-052-000", True),
    ("H5322-047-001", False), ("H5322-047-002", False), ("H3256-005-001", False),
    (None, False), ("", False), ("GA-5", False), ("nonsense", False),
])
def test_only_segment_000_counts_as_unsegmented(plan_id, expected):
    from network_probe.portal.drivers.uhc_findcare import _unsegmented

    assert _unsegmented(plan_id) is expected


def test_the_match_key_exposes_the_identifier_in_both_written_forms():
    """A 271 may write H5322-047-001 or H5322047001, and `plan_match` decomposes the concatenated
    form into contract / contract+PBP / contract+PBP+segment."""
    from network_probe.portal.drivers.uhc_findcare import _match_key
    from network_probe.portal.plan_match import identifiers

    key = _match_key("AARP Medicare Advantage from UHC GA-5 (HMO-POS)", "H5322-047-001")
    ids = identifiers(key)
    assert {"H5322", "H5322047", "H5322047001"} <= ids


def test_a_plan_with_no_identifier_matches_on_its_name_alone():
    from network_probe.portal.drivers.uhc_findcare import _match_key

    assert _match_key("UHC Dual Complete GA-S1 (PPO D-SNP)", None) == "UHC Dual Complete GA-S1 (PPO D-SNP)"


def test_a_real_271_contract_now_pins_where_a_name_could_not():
    """The point of reading the store. 'H2001819000' identifies exactly one plan; the rendered label
    'UnitedHealthcare Group Medicare Advantage (PPO)' shares only non-distinctive words with it."""
    from network_probe.portal.drivers.uhc_findcare import _match_key
    from network_probe.portal.plan_match import match_plan

    keys = [_match_key(n, i) for n, i in COBB_OPTIONS]
    m = match_plan("UHC MEDICARE ADVANTAGE H2001819000", keys)
    assert m is not None and m.index == 4
    assert m.confidence == "high" and m.confirms_network, (
        "an identifier match is the only thing that may license an out-of-network reading"
    )


# --- the walk must stop, and the capture must be UNKNOWN ------------------------------------------


class _WalkDriver(_CountyDriver):
    """Enough of the walk to reach the county step with a fake page."""

    def _dismiss_overlays(self, page):
        return None

    def _click_any(self, page, sel, label, timeout_ms=8_000):
        return True

    def _commit_location(self, page, zip_code):
        return True

    def _plan_list(self, page):
        return (), None

    def _pick_plan(self, page, plan, options=None):
        return None


def test_the_walk_stops_at_an_undecidable_county_and_says_so_in_the_trail():
    d = _WalkDriver(ACWORTH_COUNTIES)
    pin, trail = d._walk_to_plan(object(), MEMBER)
    assert pin.name is None
    assert any("Bartow, GA" in s for s in trail), f"trail must record the block: {trail}"
    assert not any("location committed" in s for s in trail)


def test_a_single_county_walk_still_reaches_the_plan_step():
    d = _WalkDriver(["Cobb, GA"])
    pin, trail = d._walk_to_plan(object(), MEMBER)
    assert d.chosen == [0]
    assert any("Cobb, GA" in s for s in trail)


def test_no_modal_at_all_still_commits_with_the_select_button():
    """The single-county flow (30144, ~8 clean live runs) must be untouched by any of this."""
    clicks: list[str] = []

    class _NoModal(_WalkDriver):
        def _click_any(self, page, sel, label, timeout_ms=8_000):
            clicks.append(label)
            return True

    d = _NoModal([], modal=False)
    _, trail = d._walk_to_plan(object(), MEMBER)
    assert "Select" in clicks
    assert any("location committed (Select)" in s for s in trail)


def test_the_capture_is_unknown_and_never_out_of_network():
    """A location we could not resolve is not evidence about a network."""
    from network_probe.portal.models import PortalCapture

    def result(status, note, **kw):
        return PortalCapture(payer_key=MEMBER.payer_key, npi=MEMBER.npi, status=status,
                             portal_name="UHC Find Care (guest)", portal_url="x",
                             driver="uhc-findcare", note=note, **kw)

    from network_probe.portal.drivers.uhc_findcare import _Pin

    d = UhcFindCareDriver()
    why = "member ZIP spans 4 counties (Bartow, GA; Cherokee, GA) whose plan lists differ"
    cap = d._sweep(object(), MEMBER, _Pin(None, why), lambda label: None, result)
    assert cap.status == PortalStatus.UNKNOWN
    assert cap.status is not PortalStatus.OUT_OF_NETWORK
    assert "counties" in cap.note, "the reason the walk stopped must survive into the capture note"


# --- the sweep must not re-pin against a list that changed under it --------------------------------


class _RepinPage:
    """A page whose plan list CHANGES after navigation — exactly what the live portal did."""

    def __init__(self, before, after):
        self.before, self.after = before, after
        self.navigated = False
        self.clicked: list[int] = []

    def goto(self, url, **kw):
        self.navigated = True

    def wait_for_load_state(self, *a, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, expr):
        return 0

    @property
    def url(self):
        return "https://findcare.guest.uhc.com/x"

    def inner_text(self, sel):
        return ""

    def locator(self, selector):
        return _PlanLoc(self.after if self.navigated else self.before, self)


class _PlanLoc:
    def __init__(self, labels, page):
        self.labels, self.page = labels, page

    @property
    def first(self):
        return self

    def count(self):
        return len(self.labels)

    def nth(self, i):
        return _PlanEl(self.labels[i], i, self.page)

    def is_visible(self):
        return False


class _PlanEl:
    def __init__(self, text, index, page):
        self.text, self.index, self.page = text, index, page

    def inner_text(self):
        return self.text

    def click(self):
        self.page.clicked.append(self.index)


GA = ["AARP Medicare Advantage from UHC GA-5 (HMO-POS)",
      "UHC Complete Care Support GS-1A (Regional PPO C-SNP)",
      "UHC Dual Complete GA-D001 (PPO D-SNP)"]


def test_repin_refuses_when_the_plan_list_changed_under_it():
    """Live, a re-navigation returned a 1-plan list where 12 had been captured. Clicking index 4 of
    a list that is no longer there searches one plan and reports another — a confident answer about
    a plan we never looked at."""
    page = _RepinPage(before=GA, after=["UHC Complete Care Support GS-1A (Regional PPO C-SNP)"])
    ok = UhcFindCareDriver()._repin(page, "url", 2, first=False, expect=GA[2])
    assert ok is False
    assert page.clicked == [], "nothing may be clicked once the list is known to have shifted"


def test_repin_proceeds_when_the_list_is_the_one_it_captured():
    page = _RepinPage(before=GA, after=GA)
    ok = UhcFindCareDriver()._repin(page, "url", 2, first=False, expect=GA[2])
    assert ok is True
    assert page.clicked == [2]


def test_the_first_plan_needs_no_navigation_and_so_no_recheck():
    """The walk is already standing on the list; re-navigating would only cost a round trip."""
    page = _RepinPage(before=GA, after=["something else entirely"])
    ok = UhcFindCareDriver()._repin(page, "url", 1, first=True, expect=GA[1])
    assert ok is True and page.navigated is False
    assert page.clicked == [1]


@pytest.mark.parametrize("exc", [PlaywrightError("detached")])
def test_a_navigation_that_raises_is_a_decline_not_a_crash(exc):
    class _Boom(_RepinPage):
        def goto(self, url, **kw):
            raise exc

    assert UhcFindCareDriver()._repin(_Boom(GA, GA), "url", 1, first=False, expect=GA[1]) is False
