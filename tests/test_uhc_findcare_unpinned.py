"""UHC Find Care must not return a decisive IN_NETWORK off the UN-PINNED directory.

Caught live against the Test 2 answer key. Row 2 (Bui, UHC AZ Dual Complete, NPI 1245461292) came
back **IN_NETWORK** where the staff determination is **OON**. The walk trail shows why:

    coverage: Employer and Individual  →  ...  →  no plan matched None in the plan list

No plan was given, so the driver took the COMMERCIAL coverage path for a Dual Complete member,
never pinned a plan, matched on surname, and still returned a confident IN — reporting
`matched_name='search'`, which is not a provider name at all.

The driver's own comment called this deliberate ("presence in UHC's directory at all is a real
signal"), but the un-pinned directory is a union of every network UHC sells. AZ Blue's driver
already refuses exactly this: "presence there does not put the provider in the member's network".
The two drivers disagreed; the live answer key settles it.

Presence still counts — it is just not a *verdict*. It becomes UNKNOWN carrying the presence as
evidence, which the determination layer renders as "Likely In-Network — not confirmed".
"""

from network_probe.portal.drivers.uhc_findcare import UhcFindCareDriver
from network_probe.portal.models import PortalStatus


def test_unpinned_presence_is_not_a_decisive_in_network():
    d = UhcFindCareDriver()
    status, note = d.presence_verdict(plan_confirmed=False, kind="surname", term="Bui",
                                      matched="Bui, Stephanie", npi="1245461292", zip_code="85032",
                                      plan=None)
    assert status == PortalStatus.UNKNOWN
    assert "not pinned" in note.lower() or "un-pinned" in note.lower()
    assert "Bui, Stephanie" in note  # the presence is still reported, not discarded


def test_presence_in_a_confirmed_plan_is_still_decisive():
    d = UhcFindCareDriver()
    status, note = d.presence_verdict(plan_confirmed=True, kind="NPI", term="1902811656",
                                      matched="Conrad Chang Manayan", npi="1902811656",
                                      zip_code="30144", plan="UHC Medicare Advantage GA-2 (PPO)")
    assert status == PortalStatus.IN_NETWORK
    assert "1902811656" in note


def test_the_note_names_which_gate_fired():
    """§8: every gate is phrased so a reader sees which guard fired."""
    d = UhcFindCareDriver()
    _, note = d.presence_verdict(plan_confirmed=False, kind="surname", term="Bui",
                                 matched="Bui, Stephanie", npi="1245461292", zip_code="85032",
                                 plan=None)
    assert "union" in note.lower() or "every network" in note.lower()
