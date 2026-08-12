"""Defect regressions for the Sapphire profile sweep — the step that reads an NPI off each result.

THE DEFECT, MEASURED LIVE 2026-08-12 against ci=BCBSSC. Searching "MICHELLE MON" at the Atlanta
clinic ZIP 30307 returns two cards, in this order:

    card 0  Micaela R Moen, MD     275 Collier Rd Nw Ste 470, Atlanta, GA 30309
    card 1  Michelle D Mon, MD     99 Krog St Ne Ste 301, Atlanta, GA 30307   <- ours, NPI 1639148703

Cards carry no NPI, so each is opened in turn. Between candidates the driver re-navigates to the
result URL, and that re-navigation intermittently repaints NOTHING:

    round 0 card 0: profile ok=True npis=['1760845168'] in 14s
              restore: nav=4s wait_results=False total=34s cards=0   <-- blew the 30s budget
              ...15s later cards=0
    round 0 card 1: !! NO LINK (cards on page: 0)
              restore: nav=0s wait_results=True total=20s cards=2    <-- the NEXT try recovered

`_back_to` returned no status, `_card_link` then found nothing and returned None, and that was
counted as a profile which "did not render an NPI" — so a provider whose profile was never opened
was reported as though it had been checked. A human opened the same profile by hand and the NPI was
right there under More About This Provider -> Identifiers.

Three separate fixes are pinned here: the restore is verified and retried, the candidate whose name
looks like the query is opened FIRST (so the common case needs no restore at all), and the three
ways a candidate can fail stop sharing one sentence.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.sapphire_shopping import (
    Card,
    ResultSet,
    SapphireShoppingDriver,
    candidate_order,
)
from network_probe.portal.models import PortalQuery, PortalStatus

# Verbatim first lines from the live cards (2026-08-12).
MOEN = "Micaela R Moen, MD\nSurgery\nCompare\n275 Collier Rd Nw Ste 470, Atlanta, GA 30309"
MON = "Michelle D Mon, MD\nSurgery\nCompare\n99 Krog St Ne Ste 301, Atlanta, GA 30307"

Q = PortalQuery(
    payer_key="bcbs-south-carolina-ga-atlanta", npi="1639148703",
    provider_first_name="MICHELLE", provider_last_name="MON",
    state="GA", zip_code="30307",
)


@pytest.fixture
def d():
    return SapphireShoppingDriver()


class TestCandidateOrder:
    def test_the_provider_whose_name_matches_is_opened_first(self):
        """Live order puts a stranger first. Ours must still be the first profile opened."""
        assert candidate_order([Card(text=MOEN), Card(text=MON)], Q) == [1, 0]

    def test_ordering_never_drops_or_duplicates_a_candidate(self):
        """It is an ORDER, not a filter — every card is still swept, or absence is unprovable."""
        cards = [Card(text=MOEN), Card(text=MON), Card(text="Someone Else, DO\nSurgery")]
        assert sorted(candidate_order(cards, Q)) == [0, 1, 2]

    def test_with_no_provider_name_the_portals_own_order_stands(self):
        q = PortalQuery(payer_key="p", npi="1", zip_code="30307")
        assert candidate_order([Card(text=MOEN), Card(text=MON)], q) == [0, 1]

    def test_a_near_miss_spelling_still_sorts_above_a_stranger(self):
        """The sheet's given name is not always the directory's ("Desire" vs "Desiree Amelia")."""
        cards = [Card(text="Bob Stranger, MD"), Card(text="Desiree Amelia Clarke, MD")]
        q = PortalQuery(payer_key="p", npi="1", provider_first_name="Desire",
                        provider_last_name="Clarke")
        assert candidate_order(cards, q)[0] == 1


class _ScriptedDriver(SapphireShoppingDriver):
    """Drives `_identify` with the browser seams scripted, so the sweep's bookkeeping is testable."""

    def __init__(self, *, npis_by_index: dict[int, tuple[str, ...]],
                 open_ok: dict[int, bool] | None = None,
                 restores: list[bool] | None = None):
        super().__init__()
        self.npis_by_index = npis_by_index
        self.open_ok = open_ok or {}
        self.restores = list(restores or [])
        self.opened: list[int] = []
        self.restore_calls = 0
        self._current: int | None = None

    def _open_profile(self, page, index):  # noqa: ARG002
        if not self.open_ok.get(index, True):
            return False
        self.opened.append(index)
        self._current = index
        return True

    def _profile_body(self, page):  # noqa: ARG002
        """Shaped like the real profile: the attestation and the Identifiers NPI in one body read."""
        npis = self.npis_by_index.get(self._current, ())
        return 'In "Preferred Blue" Network\n' + "\n".join(f"NPI: {n}" for n in npis)

    def _back_to(self, page, url):  # noqa: ARG002
        self.restore_calls += 1
        return self.restores.pop(0) if self.restores else True


def _rs():
    return ResultSet(surfaced=True, cards=[Card(text=MOEN), Card(text=MON)])


class TestSweepBookkeeping:
    def test_ours_is_found_on_the_first_profile_when_the_name_matches(self):
        """The whole point of the ordering: no restore is needed, so the flaky step never runs."""
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ("1639148703",)})
        ours, reads = dr._identify(_ScriptedPage(), _rs(), Q, [])
        assert ours is not None and "1639148703" in ours.npis
        assert dr.opened == [1], "the name-matching card must be opened first"
        assert dr.restore_calls == 0, "no re-navigation should have been needed"

    def test_a_candidate_that_never_opened_is_not_counted_as_read(self):
        """The reported defect: card 1 was never opened, and the walk said a profile had been read
        for it. An unopened profile is not a profile that showed no NPI."""
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",)}, open_ok={1: False})
        ours, reads = dr._identify(_ScriptedPage(), _rs(), Q, [])
        assert ours is None
        by_index = {r.index: r.outcome for r in reads}
        assert by_index[1] == "unopened"
        assert sum(1 for r in reads if r.outcome == "read") == 1

    def test_a_failed_restore_marks_the_remaining_candidates_unopened(self):
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ("9999999999",)},
                             restores=[False])
        ours, reads = dr._identify(_ScriptedPage(), _rs(), Q, [])
        assert ours is None
        outcomes = {r.index: r.outcome for r in reads}
        assert outcomes[1] == "read" and outcomes[0] == "unopened"

    def test_an_opened_profile_with_no_npi_is_its_own_outcome(self):
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ()})
        _, reads = dr._identify(_ScriptedPage(), _rs(), Q, [])
        assert {r.index: r.outcome for r in reads}[1] == "no-npi"

    def test_a_portal_still_searching_is_reported_as_working_not_as_empty(self):
        """Screenshotted verbatim in the failing state: the page had lost nothing — network and
        location still pinned — it was sitting on the portal's own spinner. Saying the list "did not
        re-render" there would misdescribe a portal that was simply still thinking."""
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ("9999999999",)},
                             restores=[False])
        page = _ScriptedPage()
        page.body = "Looking for matches to your search...\nThis could take a few minutes"
        _, reads = dr._identify(page, _rs(), Q, [])
        assert "still running its own search" in next(
            r.reason for r in reads if r.outcome == "unopened"
        )


class _ScriptedPage:
    url = "https://shoppingforcare.sapphirethreesixtyfive.com/search/name/MICHELLE%20MON"

    body = ""

    def wait_for_timeout(self, ms):  # noqa: ARG002
        return None

    def inner_text(self, sel):  # noqa: ARG002
        return self.body

    def locator(self, sel):  # noqa: ARG002
        return _Count(0)


class TestTheNoteTellsTheTruth:
    """A walk that could not open a profile must say so, and name the provider it could not open —
    otherwise the reader cannot tell a portal that answered from one that was never asked."""

    def _capture(self, dr, monkeypatch):
        rs = _rs()
        monkeypatch.setattr(dr, "_wait_shell", lambda page: True)
        monkeypatch.setattr(dr, "_challenge", lambda page: None)
        monkeypatch.setattr(dr, "_click_visible", lambda *a, **k: False)
        monkeypatch.setattr(dr, "_commit_location", lambda p, q, t: ("Atlanta, GA — 30307", "33.7,-84.3"))
        monkeypatch.setattr(dr, "_pinned_network", lambda page: "Preferred Blue")
        monkeypatch.setattr(dr, "_search", lambda p, q, g, t: (rs, "MICHELLE MON"))
        monkeypatch.setattr(dr, "_networks_accepted", lambda page, card: [])
        page = _CapturePage()
        return dr.capture(page, Q, lambda label: f"{label}.png")

    def test_an_unopened_profile_is_not_described_as_one_that_showed_no_npi(self, monkeypatch):
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",)}, open_ok={1: False})
        cap = self._capture(dr, monkeypatch)
        assert cap.status is PortalStatus.UNKNOWN
        assert "did not render an NPI" not in cap.note
        assert "could not be opened" in cap.note
        assert "Michelle D Mon" in cap.note, "name the provider we failed to check"

    def test_every_profile_read_and_no_match_is_still_an_out_of_network(self, monkeypatch):
        """The fix must not make absence unprovable: a fully-read set still answers."""
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ("9999999999",)})
        cap = self._capture(dr, monkeypatch)
        assert cap.status is PortalStatus.OUT_OF_NETWORK

    def test_a_match_is_in_network(self, monkeypatch):
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ("1639148703",)})
        cap = self._capture(dr, monkeypatch)
        assert cap.status is PortalStatus.IN_NETWORK

    def test_the_matched_name_is_the_portals_name_not_the_one_we_searched_for(self, monkeypatch):
        """The profile URL's JSON state carries `"name":"MICHELLE MON"` — that is the SEARCH TERM
        this walk typed, not the provider. Reading it back reported our own input as the portal's
        answer ("the portal listed 'MICHELLE MON'") when the portal's own word for her is
        "Michelle D Mon, MD". Evidence has to be the portal's, right down to the name."""
        dr = _ScriptedDriver(npis_by_index={0: ("1760845168",), 1: ("1639148703",)})
        cap = self._capture(dr, monkeypatch)
        assert cap.matched_name == "Michelle D Mon, MD"
        assert "MICHELLE MON" not in cap.note


class _CapturePage(_ScriptedPage):
    def goto(self, *a, **k):
        return None


class TestRestoreIsVerifiedAndRetried:
    """`_back_to` used to navigate once, wait, and return nothing at all. Measured live, one restore
    in this session repainted zero cards and stayed empty 15s later, while the very next navigation
    brought both cards back in 20s. So it must be checked, and it must be retried."""

    def test_a_restore_that_repaints_nothing_is_retried_and_recovers(self, d, monkeypatch):
        page = _RestorePage(cards_per_goto=[0, 2])
        monkeypatch.setattr(d, "_wait_results", lambda p, timeout_s=60.0: bool(p.cards))
        assert d._back_to(page, "https://x/search") is True
        assert page.gotos == 2

    def test_a_restore_that_never_comes_back_reports_failure(self, d, monkeypatch):
        page = _RestorePage(cards_per_goto=[0, 0, 0])
        monkeypatch.setattr(d, "_wait_results", lambda p, timeout_s=60.0: bool(p.cards))
        assert d._back_to(page, "https://x/search") is False

    def test_the_first_good_restore_costs_no_extra_navigation(self, d, monkeypatch):
        page = _RestorePage(cards_per_goto=[2, 2])
        monkeypatch.setattr(d, "_wait_results", lambda p, timeout_s=60.0: bool(p.cards))
        assert d._back_to(page, "https://x/search") is True
        assert page.gotos == 1


class _RestorePage(_ScriptedPage):
    def __init__(self, cards_per_goto: list[int]):
        self.plan = list(cards_per_goto)
        self.cards = 0
        self.gotos = 0

    def goto(self, url, **kw):  # noqa: ARG002
        self.gotos += 1
        self.cards = self.plan.pop(0) if self.plan else 0

    def locator(self, sel):  # noqa: ARG002
        return _Count(self.cards)


class _Count:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n

    @property
    def first(self):
        return self
