"""AZ Blue names every network a provider is in, behind the card's "N in network" link.

Discovered live 2026-07-31 on Arthur Maydell (NPI 1992078745): the link opens a "Plans accepted"
dialog with two collapsed accordions, Medical Networks and Medicare Networks, and expanding both
lists all 14 by name. That is a provider-FIRST read — one walk answers the question for every network
the payer sells, instead of one walk per network.

These tests drive a fake page rather than the portal: the parsing and the guards are what regress,
and the live behaviour is recorded in the driver's docstring.
"""

from network_probe.portal.drivers.healthsparq import HealthSparqDriver

# Exactly what the dialog listed for Maydell, in the order it listed them.
MAYDELL_14 = [
    "Statewide/National PPO/EPO",
    "Statewide/National PPO + Prosano",
    "Statewide PPO",
    "CHS Arizona PPO (No access outside of Arizona)",
    "Indemnity",
    "Statewide HMO",
    "Alliance PPO/EPO",
    "Alliance PPO + Prosano",
    "Alliance HMO",
    "BlueHPN National EPO (in AZ: Alliance Network)",
    "Neighborhood",
    "Workers Compensation",
    "Blue Best Life - Classic/Plus",
    "Senior Preferred Medicare Supplement",
]


class _Loc:
    def __init__(self, items=(), clickable=True):
        self._items = list(items)
        self._clickable = clickable
        self.clicks = 0

    def count(self):
        return len(self._items)

    def nth(self, i):
        return _Loc([self._items[i]])

    @property
    def first(self):
        return self

    def inner_text(self):
        return self._items[0] if self._items else ""

    def click(self, **kw):
        if not self._clickable:
            raise RuntimeError("not clickable")
        self.clicks += 1

    def get_by_text(self, label, exact=False):
        return _Loc([label])

    def locator(self, sel):
        return _Loc(self._items)


class _Page:
    """Enough Page surface for _plans_accepted: a trigger, a dialog, and rows inside it."""

    def __init__(self, rows, trigger=True):
        self.rows = rows
        self.trigger = trigger

    def locator(self, sel):
        if "plans-accepted-trigger" in sel:
            return _Loc(["14\xa0in network"] if self.trigger else [])
        if "dialog" in sel:
            return _DialogLoc(self.rows)
        return _Loc()

    def wait_for_timeout(self, ms):
        return None

    def evaluate(self, js):
        return 0

    def wait_for_load_state(self, state, timeout=None):
        return None


class _DialogLoc(_Loc):
    def __init__(self, rows):
        super().__init__(["Plans accepted"])
        self.rows = rows

    def count(self):
        return 3

    def nth(self, i):
        return self

    def locator(self, sel):
        return _Loc(self.rows) if sel == "li" else _Loc()

    def inner_text(self):
        return "Plans accepted\n" + "\n".join(self.rows)


def test_reads_every_network_the_dialog_lists():
    got = HealthSparqDriver()._plans_accepted(_Page(MAYDELL_14))
    assert list(got) == MAYDELL_14
    assert len(got) == 14


def test_no_trigger_yields_empty_not_an_assertion_of_none():
    """A portal that does not offer the link must produce (), and the caller must read that as
    'not asked' — never as 'this provider is in no networks'."""
    assert HealthSparqDriver()._plans_accepted(_Page(MAYDELL_14, trigger=False)) == ()


def test_drops_chrome_rows_that_are_not_network_names():
    """The dialog's own furniture sits in the same list markup as the networks."""
    noisy = ["Find a plan", "Medical Networks", "Statewide PPO", "", "   ", "Alliance HMO",
             "x" * 200]
    got = HealthSparqDriver()._plans_accepted(_Page(noisy))
    assert list(got) == ["Statewide PPO", "Alliance HMO"]


def test_never_raises_into_the_walk():
    """This is evidence gathered after the verdict is already established — it must never be able to
    fail a capture that has already succeeded."""

    class Boom(_Page):
        def locator(self, sel):
            raise RuntimeError("portal moved")

    assert HealthSparqDriver()._plans_accepted(Boom(MAYDELL_14)) == ()


# --- the second pass: an OON verdict still gets the provider's network list --------------------

class _Site:
    def __init__(self, generic="BCBSAZBLUE"):
        self.generic_brand_code = generic
        self.host = "azblue.healthsparq.com"
        self.insurer_code = "BCBSAZ_I"
        self.state = "AZ"


def _driver_with(monkeypatch, *, nets=("ACA Health Choice",), located=True, searched=True,
                 nav_raises=False):
    """A driver whose page-driving helpers are stubbed, so only the second pass's own logic runs."""
    d = HealthSparqDriver()
    monkeypatch.setattr(d, "_generic_launch", lambda site: "https://example.invalid/unpinned")
    monkeypatch.setattr(d, "_refusal", lambda page: None)
    monkeypatch.setattr(d, "_dismiss_overlays", lambda page: None)
    monkeypatch.setattr(d, "_settle", lambda page, pause_ms=0: None)
    monkeypatch.setattr(d, "_location_term", lambda q: "85382")
    monkeypatch.setattr(d, "_set_location", lambda page, where: located)
    monkeypatch.setattr(d, "_open_name_search", lambda page: searched)
    monkeypatch.setattr(d, "_search_terms", lambda q: [("DESIR", "surname")])
    monkeypatch.setattr(d, "_run_search", lambda page, term, q: (True, 1, "Hedson R. Desir, MD", 1))
    monkeypatch.setattr(d, "_plans_accepted", lambda page: tuple(nets))
    monkeypatch.setattr(d, "_pinned_network", lambda page: "Choose a network")

    class P:
        url = "https://example.invalid/unpinned"

        def goto(self, *a, **k):
            if nav_raises:
                raise RuntimeError("nav died")

    return d, P()


class _Q:
    npi = "1346866332"
    provider_first_name = "HEDSON"
    provider_last_name = "DESIR"
    zip_code = "85382"
    city = None
    state = "AZ"
    plan = "Statewide / National PPO"


def test_second_pass_returns_the_networks_from_the_unpinned_directory(monkeypatch):
    d, page = _driver_with(monkeypatch)
    assert d._read_networks_on(page, _Q(), _Site(), []) == ("ACA Health Choice",)


def test_second_pass_is_skipped_when_there_is_no_unpinned_directory(monkeypatch):
    """Not every HealthSparq tenant publishes a 'Do Not Know My Network' entry."""
    d, page = _driver_with(monkeypatch)
    assert d._networks_via_unpinned(page, _Q(), _Site(generic=None), []) == ()


def test_second_pass_never_costs_the_verdict(monkeypatch):
    """It runs AFTER a decisive OON. Navigation dying, the location refusing, or the search never
    opening must all yield () rather than propagate — the OON is already established."""
    for kw in ({"nav_raises": True}, {"located": False}, {"searched": False}):
        d, page = _driver_with(monkeypatch, **kw)
        assert d._read_networks_on(page, _Q(), _Site(), []) == (), kw


def test_second_pass_records_its_steps_in_the_trail(monkeypatch):
    """The trail is the audit record; a second visit to the payer must be visible in it."""
    d, page = _driver_with(monkeypatch)
    trail = []
    d._read_networks_on(page, _Q(), _Site(), trail)
    assert any("un-pinned" in s for s in trail), trail


def test_second_pass_uses_a_FRESH_context_not_the_walk_s_page(monkeypatch):
    """The pin survives a reload and a cookie/storage wipe — measured 2026-07-31 — and only a new
    context comes back un-pinned. Re-using the walk's page silently re-searches the very network we
    already proved they are absent from, which returns nothing and costs a minute."""
    d, page = _driver_with(monkeypatch)
    seen = {}

    class _Ctx:
        def __enter__(self_inner):
            return page

        def __exit__(self_inner, *a):
            return False

    def fake_portal_page(browser, portal_key=None, reuse_session=True):
        seen["reuse_session"] = reuse_session
        return _Ctx()

    monkeypatch.setattr("network_probe.portal.browser.portal_page", fake_portal_page)

    class _Browser:
        pass

    class _PageCtx:
        browser = _Browser()

    page.context = _PageCtx()
    d._networks_via_unpinned(page, _Q(), _Site(), [])
    assert seen.get("reuse_session") is False, "second pass must not inherit the pinned session"
