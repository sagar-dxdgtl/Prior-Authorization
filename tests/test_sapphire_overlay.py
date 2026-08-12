"""The "Narrow Your Search Results" popover — trap 6, and it silently covered the search box.

Caught live 2026-08-12 on the New Port Richey FL 34655 walk (sheet row 3). After the location
commits and the network pins, Zelis raises a card over the provider search box:

    Narrow Your Search Results
    Log in now or enter the first characters of your member ID to see providers in your network.
    [ Dismiss ]  [ Enter Card ID ]

The input still passes `is_visible()` — it has a bounding box and is not display:none — so the
driver read it as reachable. It is not:

    search input visible: True
    elementFromPoint at the input's centre: P||Log in now or enter the first characters of your m

The paragraph of the popover is what is actually on top. `focus()` is a JS call with no hit-test, so
the walk sailed past this and typed into a covered control.

⚠ "ENTER CARD ID" IS NEVER THE ANSWER. It opens a member-ID entry field, and no member identifier
ever reaches a portal (HANDOFF §7) — that rule is why this driver searches by provider name and
clinic ZIP alone. Dismiss is the only acceptable action, and a test below pins that.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.sapphire_shopping import SapphireShoppingDriver


class _Btn:
    def __init__(self, page, label, cy="button-stroked", visible=True):
        self.page, self.label, self.cy, self._visible = page, label, cy, visible

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self.label

    def get_attribute(self, name):
        return self.cy if name == "data-cy" else None

    def click(self, timeout=None):  # noqa: ARG002
        self.page.clicked.append(self.label)
        if self.label == "Dismiss":
            self.page.overlay = False


class _Loc:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    @property
    def first(self):
        return self.items[0] if self.items else None


class _Page:
    """A page whose popover covers the search box until Dismiss is clicked."""

    url = "https://shoppingforcare.sapphirethreesixtyfive.com/?ci=BCBSSC"

    def __init__(self, overlay=True):
        self.overlay = overlay
        self.clicked: list[str] = []

    def wait_for_timeout(self, ms):  # noqa: ARG002
        return None

    def inner_text(self, sel):  # noqa: ARG002
        return ("Narrow Your Search Results Log in now or enter the first characters of your "
                "member ID to see providers in your network. Dismiss Enter Card ID"
                if self.overlay else "Browse or search to find the care you need.")

    def locator(self, sel):
        if not self.overlay:
            return _Loc([])
        if "tooltip" in sel or "dialog" in sel:
            return _Loc([_Btn(self, "Narrow Your Search Results …", cy="tooltip-card")])
        if "button" in sel.lower() or sel == "button":
            return _Loc([_Btn(self, "Dismiss"), _Btn(self, "Enter Card ID", cy="button-flat")])
        return _Loc([])


@pytest.fixture
def d():
    return SapphireShoppingDriver()


class TestTheOverlayIsDismissed:
    def test_dismiss_is_clicked_and_the_overlay_goes_away(self, d):
        page = _Page()
        assert d._dismiss_overlays(page) is True
        assert "Dismiss" in page.clicked
        assert page.overlay is False

    def test_enter_card_id_is_NEVER_clicked(self, d):
        """It opens a member-ID field. No member identifier may reach a portal."""
        page = _Page()
        d._dismiss_overlays(page)
        assert "Enter Card ID" not in page.clicked

    def test_a_page_with_no_overlay_is_left_alone(self, d):
        page = _Page(overlay=False)
        assert d._dismiss_overlays(page) is False
        assert page.clicked == []

    def test_it_is_safe_to_call_twice(self, d):
        page = _Page()
        d._dismiss_overlays(page)
        d._dismiss_overlays(page)
        assert page.clicked.count("Dismiss") == 1


class TestAnObstructedBoxIsNotSearched:
    """A control the driver cannot really reach must not produce a result set. Typing into a covered
    box yields zero cards, and zero cards one step later is indistinguishable from an absence."""

    def test_a_still_covered_search_box_gives_an_inconclusive_read_not_an_absence(self, d, monkeypatch):
        from network_probe.portal.models import PortalQuery

        q = PortalQuery(payer_key="bcbs-south-carolina-fl-tampa", npi="1861933087",
                        provider_first_name="Romina", provider_last_name="Deldar", zip_code="34655")
        monkeypatch.setattr(d, "_dismiss_overlays", lambda page: False)
        monkeypatch.setattr(d, "_visible", lambda page, sel, limit=8: object())
        monkeypatch.setattr(d, "_obstruction", lambda page, box: "Narrow Your Search Results")
        trail: list[str] = []
        rs, term = d._search(_Page(), q, "28.2,-82.7", trail)
        assert rs.surfaced is False, "an unreachable search box must never look like a result set"
        assert any("obstruct" in s.lower() or "cover" in s.lower() for s in trail), trail

    def test_a_clear_box_searches_normally(self, d, monkeypatch):
        from network_probe.portal.models import PortalQuery

        q = PortalQuery(payer_key="p", npi="1", provider_last_name="Deldar", zip_code="34655")
        typed: list[str] = []

        class _Box:
            def focus(self):
                typed.append("focus")

        class _P(_Page):
            class keyboard:  # noqa: N801
                @staticmethod
                def type(t, delay=None):  # noqa: ARG004
                    typed.append(t)

                @staticmethod
                def press(k):
                    typed.append(k)

        monkeypatch.setattr(d, "_dismiss_overlays", lambda page: False)
        monkeypatch.setattr(d, "_visible", lambda page, sel, limit=8: _Box())
        monkeypatch.setattr(d, "_obstruction", lambda page, box: None)
        monkeypatch.setattr(d, "_wait_results", lambda page, timeout_s=60.0: True)
        monkeypatch.setattr(d, "_read_results", lambda page: __import__(
            "network_probe.portal.drivers.sapphire_shopping", fromlist=["ResultSet"]
        ).ResultSet(surfaced=True))
        rs, term = d._search(_P(overlay=False), q, "28.2,-82.7", [])
        assert rs.surfaced is True
        assert "Deldar" in typed and "Enter" in typed


class TestTheSurnameFallbackTheDocstringPromised:
    """MEASURED 2026-08-12: the portal answered 'MICHELLE MON' with its own "No results for MICHELLE
    MON" header — for a provider it had listed twice earlier the same day, at the same ZIP, on the
    same network. A no-results header is the portal ANSWERING, so it is the most dangerous kind of
    empty: it looks authoritative. The code comment already said "the surname alone is the fallback"
    and the code never fell back — `term` was computed once and used once."""

    def _driver(self, monkeypatch, script):
        from network_probe.portal.drivers import sapphire_shopping as ss

        d = ss.SapphireShoppingDriver()
        seen: list[str] = []

        class _Box:
            def focus(self):
                pass

        class _P(_Page):
            class keyboard:  # noqa: N801
                @staticmethod
                def type(t, delay=None):  # noqa: ARG004
                    seen.append(t)

                @staticmethod
                def press(k):
                    pass

        monkeypatch.setattr(d, "_dismiss_overlays", lambda page: False)
        monkeypatch.setattr(d, "_visible", lambda page, sel, limit=8: _Box())
        monkeypatch.setattr(d, "_obstruction", lambda page, box: None)
        monkeypatch.setattr(d, "_wait_results", lambda page, timeout_s=60.0: True)
        monkeypatch.setattr(d, "_read_results", lambda page: script(seen[-1] if seen else ""))
        return d, seen, _P(overlay=False)

    def test_a_no_results_header_on_the_full_name_retries_the_surname(self, monkeypatch):
        from network_probe.portal.drivers.sapphire_shopping import Card, ResultSet
        from network_probe.portal.models import PortalQuery

        def script(term):
            if term == "MICHELLE MON":
                return ResultSet(surfaced=True, none_header="No results for MICHELLE MON")
            return ResultSet(surfaced=True, cards=[Card(text="Michelle D Mon, MD\nSurgery")])

        d, seen, page = self._driver(monkeypatch, script)
        # npi="" on purpose: the NPI is searched first now (see TestTheNpiIsSearchedFirst), and
        # this test is about the NAME fallback in isolation.
        q = PortalQuery(payer_key="p", npi="",
                        provider_first_name="MICHELLE", provider_last_name="MON", zip_code="30307")
        trail: list[str] = []
        rs, term = d._search(page, q, "33.7,-84.3", trail)
        assert seen == ["MICHELLE MON", "MON"], f"surname never tried: {seen}"
        assert rs.populated, "the fallback found the provider and the walk must use it"
        assert term == "MON"

    def test_a_populated_full_name_search_does_NOT_run_the_fallback(self, monkeypatch):
        from network_probe.portal.drivers.sapphire_shopping import Card, ResultSet
        from network_probe.portal.models import PortalQuery

        d, seen, page = self._driver(
            monkeypatch, lambda term: ResultSet(surfaced=True, cards=[Card(text="Michelle D Mon, MD")]))
        q = PortalQuery(payer_key="p", npi="", provider_first_name="MICHELLE",
                        provider_last_name="MON", zip_code="30307")
        d._search(page, q, "33.7,-84.3", [])
        assert seen == ["MICHELLE MON"], "a good first search must not cost a second one"

    def test_both_terms_empty_still_reports_an_empty_set_not_an_absence(self, monkeypatch):
        from network_probe.portal.drivers.sapphire_shopping import ResultSet
        from network_probe.portal.models import PortalQuery

        d, seen, page = self._driver(
            monkeypatch, lambda term: ResultSet(surfaced=True, none_header=f"No results for {term}"))
        q = PortalQuery(payer_key="p", npi="", provider_first_name="MICHELLE",
                        provider_last_name="MON", zip_code="30307")
        rs, _ = d._search(page, q, "33.7,-84.3", [])
        assert seen == ["MICHELLE MON", "MON"]
        assert rs.surfaced and not rs.populated


class TestTheNpiIsSearchedFirst:
    """LIVE-PROVEN 2026-08-12 on ci=BCBSSC, New Port Richey 34655:

        '1861933087'    -> 1 card: Romina Deldar, MD   "Matched on: PROVIDER IDENTIFIER"
        'ROMINA CROSBY' -> 0 cards: "No results for ROMINA CROSBY"
        'Romina Deldar' -> 1 card                      "Matched on: NAME"

    The name search is a coin flip because the NAMES DISAGREE ACROSS SOURCES. NPPES calls sheet row
    3's provider ROMINA CROSBY and the portal calls her Romina Deldar; the app resolves the provider
    name from NPPES (the form has no provider-name field), so the UI searched a name this directory
    has never heard of and got an authoritative-looking "No results".

    Searching the NPI removes the whole class: it cannot be spelled differently, it is what the
    sweep identifies by anyway, and it returns ONE card instead of two to ten — so the profile sweep
    needs no return trip to the result list, which is the step trap 5 says fails 4 times in 12.
    """

    def _driver(self, monkeypatch, script):
        from network_probe.portal.drivers import sapphire_shopping as ss

        d = ss.SapphireShoppingDriver()
        seen: list[str] = []

        class _Box:
            def focus(self):
                pass

        class _P(_Page):
            class keyboard:  # noqa: N801
                @staticmethod
                def type(t, delay=None):  # noqa: ARG004
                    seen.append(t)

                @staticmethod
                def press(k):
                    pass

        monkeypatch.setattr(d, "_dismiss_overlays", lambda page: False)
        monkeypatch.setattr(d, "_visible", lambda page, sel, limit=8: _Box())
        monkeypatch.setattr(d, "_obstruction", lambda page, box: None)
        monkeypatch.setattr(d, "_wait_results", lambda page, timeout_s=60.0: True)
        monkeypatch.setattr(d, "_read_results", lambda page: script(seen[-1] if seen else ""))
        return d, seen, _P(overlay=False)

    def _q(self, **kw):
        from network_probe.portal.models import PortalQuery

        base = dict(payer_key="bcbs-south-carolina-fl-tampa", npi="1861933087",
                    provider_first_name="ROMINA", provider_last_name="CROSBY", zip_code="34655")
        base.update(kw)
        return PortalQuery(**base)

    def test_the_npi_is_tried_before_any_name(self, monkeypatch):
        from network_probe.portal.drivers.sapphire_shopping import Card, ResultSet

        d, seen, page = self._driver(
            monkeypatch, lambda t: ResultSet(surfaced=True, cards=[Card(text="Romina Deldar, MD")]))
        rs, term = d._search(page, self._q(), "28.2,-82.7", [])
        assert seen == ["1861933087"], f"the NPI must be the first and only search: {seen}"
        assert term == "1861933087"
        assert rs.populated

    def test_a_wrong_name_from_nppes_no_longer_sinks_the_walk(self, monkeypatch):
        """The exact live case: the NPI hits, the NPPES name would have returned nothing."""
        from network_probe.portal.drivers.sapphire_shopping import Card, ResultSet

        def script(term):
            if term == "1861933087":
                return ResultSet(surfaced=True, cards=[Card(text="Romina Deldar, MD\nSurgery")])
            return ResultSet(surfaced=True, none_header=f"No results for {term}")

        d, seen, page = self._driver(monkeypatch, script)
        rs, _ = d._search(page, self._q(), "28.2,-82.7", [])
        assert rs.populated and seen == ["1861933087"]

    def test_an_npi_that_finds_nothing_still_falls_back_to_the_names(self, monkeypatch):
        """A portal that does not index this NPI must not end the walk — the names are still worth
        asking, and only a populated-and-missing set may ever support an absence."""
        from network_probe.portal.drivers.sapphire_shopping import Card, ResultSet

        def script(term):
            if term == "1861933087":
                return ResultSet(surfaced=True, none_header="No results for 1861933087")
            if term == "ROMINA CROSBY":
                return ResultSet(surfaced=True, none_header="No results for ROMINA CROSBY")
            return ResultSet(surfaced=True, cards=[Card(text="Romina Deldar, MD")])

        d, seen, page = self._driver(monkeypatch, script)
        rs, term = d._search(page, self._q(), "28.2,-82.7", [])
        assert seen == ["1861933087", "ROMINA CROSBY", "CROSBY"], seen
        assert rs.populated and term == "CROSBY"

    def test_a_query_with_no_npi_still_searches_by_name(self, monkeypatch):
        from network_probe.portal.drivers.sapphire_shopping import Card, ResultSet

        d, seen, page = self._driver(
            monkeypatch, lambda t: ResultSet(surfaced=True, cards=[Card(text="X")]))
        d._search(page, self._q(npi=""), "28.2,-82.7", [])
        assert seen == ["ROMINA CROSBY"]
