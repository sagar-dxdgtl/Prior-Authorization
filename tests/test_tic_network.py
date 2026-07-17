"""TiC provider-network signal: a billing TIN found in a payer's real in-network MRF is decisive
proof of IN_NETWORK. Absence is NOT proof of OON — MRFs are famously incomplete — so a miss is
UNKNOWN, never OUT_OF_NETWORK. (The asymmetry mirrors the directory-confidence rule.)"""

from network_probe.domain.models import NetworkStatus
from network_probe.domain.tic_network import tic_network_status
from network_probe.domain.tin_crosswalk import TinCrosswalk

_CW = TinCrosswalk(records=[
    {"payer": "ambetter-centene-tx-houston", "npi": "1710305735", "tin": "933510922"},
    {"payer": "cigna-healthcare-co-denver", "npi": "1629339312", "tin": "475181686"},
])


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
