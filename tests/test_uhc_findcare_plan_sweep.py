"""UHC Find Care: what to do when the 271's plan string can never pin a plan.

Caught live 2026-08-06 on Ins Test 3 row 1 — Cindy Morgan / Conrad Manayan (NPI 1902811656,
Kennesaw GA 30144), plan string "UHC Medicare Advantage GA", staff ground truth **IN**. The capture
took 136s and returned:

    result_count=0, "UHC Find Care returned no results for NPI 1902811656 or Manayan near 30144
    (guest plan unconfirmed)"  [portal walk: … → no plan matched 'UHC Medicare Advantage GA']

Three separate things were wrong, and the live portal proved each one:

1. **The plan string cannot pin, ever.** It carries no identifier, and its only distinctive token is
   "UHC" — MEDICARE and ADVANTAGE are both in `plan_match._NON_DISTINCTIVE` and "GA" is under the
   3-char floor. One token is below `_MIN_DISTINCTIVE_TOKENS`, so `match_plan` returns None against
   *any* option list. Not flaky: arithmetic.

2. **The un-pinned `/browse` fallback is dead.** The driver fell through to it believing it was "a
   union of every network UHC sells". Measured on the live portal that day: NPI → 0, "Manayan" → 0,
   "Conrad Manayan" → 0, and control surname "Smith" → 0, with the portal rendering its own
   `suggestion-list-no-results` element. Without a pinned plan the search index is EMPTY. There is no
   union directory to fall back to, so the fallback bought nothing and cost ~80s.

3. **An empty typeahead was indistinguishable from a slow one.** `_await_suggestions` polled for
   suggestions but never read the portal's own empty-state, so every fruitless search waited out the
   full `_TYPEAHEAD_MAX_S` — 13.3s measured, three times per capture.

The fix is to stop guessing AND stop falling back: sweep the county's own plan list. Pin any GA plan
and the same NPI returns "Conrad Chang MANAYAN — General Surgery" instantly; verified live in 4 plans
spanning HMO-POS / Regional PPO / D-SNP / Group PPO, found in all four. Present in every plan searched
means the answer does not depend on which plan the member holds, which is a sound IN that never
required guessing one. Presence that VARIES means the plan string is load-bearing and we cannot pin
it — that is an UNKNOWN, and naming the divergence is the whole point.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from network_probe.portal.drivers.uhc_findcare import (
    _SWEEP_MAX_PLANS,
    UhcFindCareDriver,
    _sweep_indices,
)
from network_probe.portal.models import PortalQuery, PortalStatus
from network_probe.portal.plan_match import match_plan

# UHC's real Cobb County (Kennesaw 30144) Medicare list, in portal order, read live 2026-08-06.
GA_PLANS = [
    "AARP Medicare Advantage from UHC GA-5 (HMO-POS)",
    "UHC Complete Care Support GS-1A (Regional PPO C-SNP)",
    "UHC Dual Complete GA-D001 (PPO D-SNP)",
    "UHC Dual Complete GA-D2 (HMO-POS D-SNP)",
    "UHC Dual Complete GA-S1 (PPO D-SNP)",
    "UHC Dual Complete GA-S2 (PPO D-SNP)",
    "UHC Dual Complete GA-S3 (HMO-POS D-SNP)",
    "UHC Dual Complete GA-V1 (PPO D-SNP)",
    "UHC Medicare Advantage Patriot No Rx GS-MA01 (Regional PPO)",
    "UHC Nursing Home Plan GA-F001 (PPO I-SNP)",
    "UnitedHealthcare Group Medicare Advantage (HMO)",
    "UnitedHealthcare Group Medicare Advantage (PPO)",
]
PLAN_URL = ("https://findcare.guest.uhc.com/guest-plan-selection/plan-selection"
            "?planSelectionLob=MR&coverageType=M&chipValue=All")

MORGAN = PortalQuery(
    payer_key="unitedhealthcare-ga-atlanta", npi="1902811656", provider_first_name="Conrad",
    provider_last_name="Manayan", plan="UHC Medicare Advantage GA", state="GA", city="Kennesaw",
    zip_code="30144", tin="921600050",
)


# --- 1. the root cause, pinned as a regression ----------------------------------------------------


def test_the_ga_plan_string_can_never_pin_against_the_real_list():
    """The premise of the whole sweep. If this ever starts matching, the sweep is dead code."""
    assert match_plan("UHC Medicare Advantage GA", GA_PLANS) is None


def test_every_real_ga_label_does_carry_an_identifier_so_the_weak_side_is_the_271():
    """The portal is not the problem — 10 of the 12 labels name a market code. Two group plans do
    not, which is exactly why a sweep beats demanding a better label."""
    from network_probe.portal.plan_match import identifiers

    assert sum(1 for lab in GA_PLANS if identifiers(lab)) == 10


# --- 2. the sweep replaces the dead /browse fallback ----------------------------------------------


class _SweepDriver(UhcFindCareDriver):
    """Drives `_sweep`'s decision logic over a scripted portal, without a browser.

    The seams are `_repin` (which plan is selected) and `_find_provider` (what that plan's search
    said). Both are exercised live elsewhere; what needs adversarial testing is the verdict.
    """

    def __init__(self, presence: dict[str, bool], *, repin_fails_at: int | None = None):
        self.presence = presence
        self.repin_fails_at = repin_fails_at
        self.pinned: list[str] = []
        self.searched: list[str] = []

    def _repin(self, page, list_url, index, *, first=False, expect=None):
        if self.repin_fails_at is not None and len(self.pinned) >= self.repin_fails_at:
            return False
        self.pinned.append(GA_PLANS[index])
        return True

    def _find_provider(self, page, q):
        label = self.pinned[-1]
        self.searched.append(label)
        if self.presence[label]:
            return True, 1, "Conrad Chang MANAYAN", "NPI"
        return False, 0, None, None


def _pin(labels=tuple(GA_PLANS), list_url=PLAN_URL):
    from network_probe.portal.drivers.uhc_findcare import _Pin

    return _Pin(None, "no plan matched 'UHC Medicare Advantage GA'", labels=tuple(labels),
                list_url=list_url)


def _run(driver, q=MORGAN, pin=None):
    def result(status, note, **kw):
        from network_probe.portal.models import PortalCapture

        return PortalCapture(payer_key=q.payer_key, npi=q.npi, plan=q.plan, status=status,
                             portal_name="UHC Find Care (guest)", portal_url=PLAN_URL,
                             driver="uhc-findcare", note=note, **kw)

    return driver._sweep(object(), q, pin or _pin(), lambda label: None, result)


def test_present_in_every_swept_plan_is_a_decisive_in_network():
    """Manayan, live: found under all four plans tested. The member's plan is unknown and stays
    unknown — the point is that it no longer matters."""
    cap = _run(_SweepDriver({p: True for p in GA_PLANS}))
    assert cap.status == PortalStatus.IN_NETWORK
    assert cap.matched_name == "Conrad Chang MANAYAN"


def test_the_in_network_note_says_it_never_needed_to_pick_a_plan():
    cap = _run(_SweepDriver({p: True for p in GA_PLANS}))
    low = cap.note.lower()
    assert "every" in low and "plan" in low
    assert "does not depend on which plan" in low or "regardless of which plan" in low


#: A plan the sample actually visits. `_sweep_indices(12, 4)` selects 0, 4, 7 and 11, so a plan
#: outside that spread can never disagree with anything — the first draft of these tests used
#: GA-D2 (index 3) and silently asserted nothing, which is the failure mode a sampled sweep invites.
DIVERGENT = GA_PLANS[4]  # "UHC Dual Complete GA-S1 (PPO D-SNP)"


def test_presence_that_varies_by_plan_is_unknown_not_a_guess():
    """The plan string is load-bearing here and we cannot pin it. Guessing is the CareFlex bug."""
    presence = {p: True for p in GA_PLANS}
    presence[DIVERGENT] = False
    cap = _run(_SweepDriver(presence))
    assert cap.status == PortalStatus.UNKNOWN


def test_a_divergent_sweep_names_the_plan_that_disagreed():
    """§8: a reader must see WHY it declined, and which plan broke the invariance."""
    presence = {p: True for p in GA_PLANS}
    presence[DIVERGENT] = False
    cap = _run(_SweepDriver(presence))
    assert "GA-S1" in cap.note


def test_absence_across_the_sample_is_never_an_out_of_network():
    """The cap makes the sweep a SAMPLE. Absence from 4 of 12 plans is not absence from the
    member's, which may be one of the 8 never searched — the same asymmetry `_absence_blockers`
    enforces on the pinned path."""
    cap = _run(_SweepDriver({p: False for p in GA_PLANS}))
    assert cap.status == PortalStatus.UNKNOWN
    assert cap.status != PortalStatus.OUT_OF_NETWORK


def test_the_note_states_how_many_of_how_many_plans_were_searched():
    """No silent caps: a sample reported as if it were the whole list is a lie of omission."""
    cap = _run(_SweepDriver({p: True for p in GA_PLANS}))
    assert f"{_SWEEP_MAX_PLANS} of {len(GA_PLANS)}" in cap.note


def test_the_sweep_is_bounded_and_does_not_search_all_twelve():
    d = _SweepDriver({p: True for p in GA_PLANS})
    _run(d)
    assert len(d.searched) == _SWEEP_MAX_PLANS


def test_a_divergent_sweep_stops_early_rather_than_finishing_the_list():
    """Once presence varies the answer is settled; further plans buy nothing but portal load."""
    presence = {p: True for p in GA_PLANS}
    presence[DIVERGENT] = False  # the second plan the spread visits
    d = _SweepDriver(presence)
    _run(d)
    assert len(d.searched) == 2


def test_a_plan_list_the_walk_never_reached_is_unknown_not_a_browse_fallback():
    """The dead `/browse` directory is not a fallback any more; with no list there is no question
    we can put to the portal."""
    cap = _run(_SweepDriver({}), pin=_pin(labels=(), list_url=None))
    assert cap.status == PortalStatus.UNKNOWN
    assert "plan list" in cap.note.lower()


def test_a_portal_that_stops_re_pinning_reports_what_it_managed_to_search():
    """A wedged re-pin must not read as absence — it is fewer plans searched, not a negative."""
    d = _SweepDriver({p: True for p in GA_PLANS}, repin_fails_at=2)
    cap = _run(d)
    assert d.searched and len(d.searched) == 2
    assert cap.status == PortalStatus.IN_NETWORK
    assert f"2 of {len(GA_PLANS)}" in cap.note


# --- 3. the sample must span the list, not take the top N ------------------------------------------


def test_the_sweep_spreads_across_the_list_instead_of_taking_the_top_n():
    """GA's list is alphabetical and its first eight are six Dual Complete variants. Taking the top
    4 would sample one product family and call it invariance."""
    idx = _sweep_indices(len(GA_PLANS), _SWEEP_MAX_PLANS)
    assert idx[0] == 0 and idx[-1] == len(GA_PLANS) - 1
    assert len(idx) == _SWEEP_MAX_PLANS
    families = {GA_PLANS[i].split()[0] for i in idx}
    assert len(families) > 1, "a sample confined to one product family proves nothing"


@pytest.mark.parametrize("total,cap,expected_len", [
    (12, 4, 4), (4, 4, 4), (3, 4, 3), (1, 4, 1), (0, 4, 0), (2, 1, 1),
])
def test_sweep_indices_are_bounded_unique_and_in_range(total, cap, expected_len):
    idx = _sweep_indices(total, cap)
    assert len(idx) == expected_len
    assert len(set(idx)) == len(idx)
    assert all(0 <= i < total for i in idx)
    assert idx == sorted(idx)


# --- 4. the portal's own empty-state ends the wait --------------------------------------------------


class _TypeaheadPage:
    """Enough Page for `_await_suggestions`: a no-results element and a counting clock."""

    def __init__(self, *, no_results: bool):
        self.no_results = no_results
        self.waited_ms = 0

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def locator(self, selector):
        if "no-results" in selector:
            return _Vis(self.no_results)
        raise PlaywrightError("only the empty-state is scripted here")


class _Vis:
    def __init__(self, visible):
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def count(self):
        return 0


def test_the_portals_own_no_results_element_ends_the_poll_immediately():
    """13.3s measured per empty search, three per capture, on a page that had already answered."""
    page = _TypeaheadPage(no_results=True)
    d = UhcFindCareDriver()
    assert d._await_suggestions(page) == []
    assert page.waited_ms <= 2_000, (
        f"waited {page.waited_ms}ms after the portal had rendered its empty-state"
    )


def test_without_the_empty_state_it_still_waits_the_full_deadline():
    """A genuinely slow typeahead must still be waited out — that distinction is the whole reason
    `_await_suggestions` exists rather than a fixed sleep."""
    page = _TypeaheadPage(no_results=False)
    d = UhcFindCareDriver()
    assert d._await_suggestions(page) == []
    assert page.waited_ms > 5_000


# --- 5. a typeahead that already answered must not also open the results page ---------------------


class _SearchPage:
    """Enough Page for `_run_search`: scripted suggestions, and a counted Enter."""

    def __init__(self, suggestions, body=""):
        self.suggestions = suggestions
        self.body = body
        self.enter_presses = 0

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state, timeout=None):
        pass  # `_settle` probes networkidle; a quiet stub page settles instantly

    def evaluate(self, expr):
        return 0

    def inner_text(self, sel):
        return self.body

    def locator(self, selector):
        if "typeahead-suggestion-section" in selector:
            return _Sugg(self.suggestions)
        return _Sugg([])


class _Sugg:
    def __init__(self, items):
        self.items = items

    @property
    def first(self):
        return self

    def count(self):
        return len(self.items)

    def nth(self, i):
        return _Text(self.items[i])

    def is_visible(self):
        return False


class _Text:
    def __init__(self, t):
        self.t = t

    def inner_text(self):
        return self.t


class _Box:
    def __init__(self, page):
        self.page = page

    def click(self):
        pass

    def fill(self, v):
        pass

    def press(self, key):
        self.page.enter_presses += 1


def test_a_typeahead_that_named_our_provider_does_not_also_press_enter():
    """~5s of settle per plan searched, for a results page that has returned 0 cards on every live
    observation of this portal."""
    page = _SearchPage(["Conrad Chang MANAYANGeneral Surgery"])
    d = UhcFindCareDriver()
    found, count, matched = d._run_search(page, _Box(page), "1902811656", MORGAN)
    assert found and count == 1 and "MANAYAN" in matched
    assert page.enter_presses == 0


def test_a_stranger_only_typeahead_still_opens_the_results_page():
    """The shortcut is for positive identification only — otherwise the richer surface is still
    worth asking for."""
    page = _SearchPage(["Tony BUI, Pain Management"])
    d = UhcFindCareDriver()
    d._run_search(page, _Box(page), "Manayan", MORGAN)
    assert page.enter_presses == 1


def test_a_page_that_cannot_be_read_is_not_treated_as_no_results():
    """`_says_no_results` must fail closed: an unreadable DOM is not the portal saying 'nothing'."""

    class _Broken:
        def locator(self, selector):
            raise PlaywrightError("detached")

    assert UhcFindCareDriver()._says_no_results(_Broken()) is False
