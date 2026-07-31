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
