"""TiC provider-network signal: a billing TIN found in a payer's real in-network MRF is decisive
proof of IN_NETWORK. Absence is NOT proof of OON — MRFs are famously incomplete — so a miss is
UNKNOWN, never OUT_OF_NETWORK. (The asymmetry mirrors the directory-confidence rule.)"""

import re
from types import SimpleNamespace

from network_probe.domain.models import NetworkStatus
from network_probe.domain.tic_network import (
    tic_network_status,
    tic_roster_networks,
    tic_tin_in_roster,
)
from network_probe.domain.tin_crosswalk import TinCrosswalk

_CW = TinCrosswalk(records=[
    {"payer": "ambetter-centene-tx-houston", "npi": "1710305735", "tin": "933510922"},
    {"payer": "cigna-healthcare-co-denver", "npi": "1629339312", "tin": "475181686"},
])
_EMPTY_CW = TinCrosswalk(records=[])


class _FactsStore:
    """Test double for ProviderNetworkStore — same facts_for() contract, no database.

    Rows are (payer_key, npi, tin, in_network, source) tuples.
    """

    def __init__(self, rows):
        self._rows = [
            SimpleNamespace(payer_key=p, npi=n, tin=t, in_network=i, source=s) for (p, n, t, i, s) in rows
        ]

    def facts_for(self, payer_key, npi=None, tin=None):
        def norm(v):
            return re.sub(r"\D", "", str(v or ""))

        return [
            f
            for f in self._rows
            if f.payer_key == payer_key
            and (not npi or f.npi == str(npi).strip())
            and (not tin or norm(f.tin) == norm(tin))
        ]


# The real ingested Oscar FL MRF rows (16 in-network NPIs at TIN 463812940); NPI 1568423168
# (Clarke, Desiree) is the one absent from that roster — staff determination "Physician OON".
_OSCAR = "oscar-fl-south-florida"
_OSCAR_TIN = "463812940"
_OSCAR_STORE = _FactsStore(
    [(_OSCAR, npi, _OSCAR_TIN, True, "tic") for npi in ("1699931030", "1760430029", "1790953933")]
)


def test_billing_tin_in_mrf_is_in_network():
    status, tins = tic_network_status("ambetter-centene-tx-houston", "1710305735", "933510922", crosswalk=_CW)
    assert status == NetworkStatus.IN_NETWORK
    assert "933510922" in tins


def test_tin_normalization_matches_dashed():
    status, _ = tic_network_status("ambetter-centene-tx-houston", "1710305735", "93-3510922", crosswalk=_CW)
    assert status == NetworkStatus.IN_NETWORK


def test_provider_present_but_different_tin_is_unknown_not_oon():
    # the crosswalk knows this NPI under one TIN; a claim under a DIFFERENT TIN isn't proven OON
    status, tins = tic_network_status("ambetter-centene-tx-houston", "1710305735", "999999999", crosswalk=_CW)
    assert status == NetworkStatus.UNKNOWN
    assert "933510922" in tins  # we still surface the TINs we DO know for context


def test_npi_absent_from_mrf_is_unknown():
    status, tins = tic_network_status("ambetter-centene-tx-houston", "9999999999", "933510922", crosswalk=_CW)
    assert status == NetworkStatus.UNKNOWN
    assert tins == []


def test_no_billing_tin_is_unknown():
    status, _ = tic_network_status("ambetter-centene-tx-houston", "1710305735", None, crosswalk=_CW)
    assert status == NetworkStatus.UNKNOWN


def test_wrong_payer_is_unknown():
    # a TIN in Cigna's MRF says nothing about the same provider under a different payer
    status, _ = tic_network_status("ambetter-centene-tx-houston", "1629339312", "475181686", crosswalk=_CW)
    assert status == NetworkStatus.UNKNOWN


# ---- P2: ingested MRF rows live in provider_network_facts, not the in-code seed ----------
#
# tic_network_status used to read ONLY the crosswalk seed, so every MRF ingest landed
# somewhere the signal could not see it. See HANDOFF-2026-07-28.md §1 P2.


def test_ingested_mrf_fact_is_visible_to_the_signal():
    status, tins = tic_network_status(
        _OSCAR, "1699931030", _OSCAR_TIN, crosswalk=_EMPTY_CW, store=_OSCAR_STORE
    )
    assert status == NetworkStatus.IN_NETWORK
    assert _OSCAR_TIN in tins


def test_known_tins_union_crosswalk_and_store():
    store = _FactsStore([("cigna-healthcare-co-denver", "1629339312", "880000001", True, "tic")])
    _, tins = tic_network_status(
        "cigna-healthcare-co-denver", "1629339312", None, crosswalk=_CW, store=store
    )
    assert tins == ["475181686", "880000001"]  # seed TIN + ingested TIN


def test_out_of_network_fact_is_not_proof_of_in_network():
    store = _FactsStore([(_OSCAR, "1568423168", _OSCAR_TIN, False, "tic")])
    status, tins = tic_network_status(
        _OSCAR, "1568423168", _OSCAR_TIN, crosswalk=_EMPTY_CW, store=store
    )
    assert status == NetworkStatus.UNKNOWN
    assert tins == []


def test_non_tic_source_is_not_mrf_proof():
    # a directory read persisted as a fact is not a Transparency-in-Coverage MRF hit
    store = _FactsStore([(_OSCAR, "1699931030", _OSCAR_TIN, True, "directory")])
    status, _ = tic_network_status(_OSCAR, "1699931030", _OSCAR_TIN, crosswalk=_EMPTY_CW, store=store)
    assert status == NetworkStatus.UNKNOWN


# ---- the roster check: the ONLY TiC-derived OON is the physician gap ---------------------


def test_tin_in_roster_under_other_npis_is_a_physician_gap():
    present, others = tic_tin_in_roster(
        _OSCAR, _OSCAR_TIN, npi="1568423168", crosswalk=_EMPTY_CW, store=_OSCAR_STORE
    )
    assert present is True
    assert "1568423168" not in others
    assert len(others) == 3


def test_tin_absent_from_roster_is_not_a_gap():
    present, others = tic_tin_in_roster(
        _OSCAR, "999999999", npi="1568423168", crosswalk=_EMPTY_CW, store=_OSCAR_STORE
    )
    assert present is False
    assert others == []


def test_our_own_npi_does_not_count_as_another_npi():
    """A roster holding ONLY our NPI is an IN, not a group-contracted-physician-absent gap."""
    store = _FactsStore([(_OSCAR, "1568423168", _OSCAR_TIN, True, "tic")])
    present, others = tic_tin_in_roster(
        _OSCAR, _OSCAR_TIN, npi="1568423168", crosswalk=_EMPTY_CW, store=store
    )
    assert present is True
    assert others == []


# ---- the roster's own scope must be visible in the verdict --------------------------------
#
# A payer key can span several networks (Oscar GA sells 063 Guided Care AND 065 Open Access).
# A TIN in one network's roster says nothing about the other, so the evidence must name the
# roster it came from rather than read as "contracted with Oscar GA".


def test_roster_networks_names_the_network_the_evidence_came_from():
    store = _FactsStore([("oscar-ga-atlanta", "1619937786", "921600050", True, "tic")])
    store._rows[0].network_name = "Individual Georgia HMO Open Access (net 065)"
    assert tic_roster_networks("oscar-ga-atlanta", "921600050", store=store) == [
        "Individual Georgia HMO Open Access (net 065)"
    ]


def test_roster_networks_is_empty_when_facts_carry_no_label():
    assert tic_roster_networks(_OSCAR, _OSCAR_TIN, store=_OSCAR_STORE) == []
