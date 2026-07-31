"""Molina's profile page names every Plan/Program a provider is in.

Discovered live 2026-07-31 on Clinton Twaddell (NPI 1437131901). The result card states only the ONE
network the walk pinned — `In "TX - Texas STAR" Plan/Program` — but clicking through to the provider
profile lists all of them under a handle the portal itself calls `profile-networks-accepted.network`:

    Molina Medicare Complete Care (HMO D-SNP)
    Molina Medicare Complete Care Plus (HMO D-SNP)
    Texas STAR
    Texas STAR+PLUS

Four, where HANDOFF-2026-07-29 row 8 recorded only the two STAR plans. Same provider-first read as AZ
Blue's "Plans accepted" dialog, and it matters more here: managed Medicaid is TiC-exempt, so this
portal and the PDEX FHIR directory are the ONLY network sources for the row.

Driven against a fake page — the parsing and the guards are what regress.
"""

from network_probe.portal.drivers.molina_provider_search import MolinaProviderSearchDriver

TWADDELL_4 = [
    "Molina Medicare Complete Care (HMO D-SNP)",
    "Molina Medicare Complete Care Plus (HMO D-SNP)",
    "Texas STAR",
    "Texas STAR+PLUS",
]


class _Loc:
    def __init__(self, items=(), clickable=True):
        self._items = list(items)
        self._clickable = clickable

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


class _Page:
    def __init__(self, nets, link=True, body="Clinton W Twaddell, MD\nPlans"):
        self.nets = nets
        self.link = link
        self.body = body

    def locator(self, sel):
        if "profile-networks-accepted.network" in sel:
            return _Loc(self.nets)
        if "result" in sel:
            return _Loc(["Clinton W Twaddell, MD"] if self.link else [])
        return _Loc()

    def inner_text(self, sel):
        return self.body

    def wait_for_timeout(self, ms):
        return None

    def evaluate(self, js):
        return 0

    def wait_for_load_state(self, state, timeout=None):
        return None

    def go_back(self, **kw):
        return None


def test_reads_every_plan_program_on_the_profile():
    got = MolinaProviderSearchDriver()._networks_accepted(_Page(TWADDELL_4))
    assert list(got) == TWADDELL_4


def test_no_profile_link_yields_empty():
    """() means 'not asked', never 'in no networks' — the caller may only read absence from a
    NON-empty list."""
    assert MolinaProviderSearchDriver()._networks_accepted(_Page(TWADDELL_4, link=False)) == ()


def test_drops_blank_and_oversized_rows():
    noisy = ["Texas STAR", "", "   ", "x" * 200, "Texas STAR+PLUS", "Texas STAR"]
    got = MolinaProviderSearchDriver()._networks_accepted(_Page(noisy))
    assert list(got) == ["Texas STAR", "Texas STAR+PLUS"], "blank, oversized and duplicate rows drop"


def test_never_raises_into_a_settled_verdict():
    """This runs after the verdict is established; a moved selector costs the extra evidence only."""

    class Boom(_Page):
        def locator(self, sel):
            raise RuntimeError("portal moved")

    assert MolinaProviderSearchDriver()._networks_accepted(Boom(TWADDELL_4)) == ()
