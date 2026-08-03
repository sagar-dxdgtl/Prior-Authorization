"""A 271 on its own can never assert IN_NETWORK.

Caught on the demo path 2026-08-03 (Susan Smith, Aetna Medicare Prime Extra, NPI 1780175349). The
row rendered **In-Network · high confidence** while every provider-level source said it did not know:

    Stedi 271            "a 271 gives plan-tier only — provider-specific network is UNKNOWN here"
    Credentialing matrix NO_RECORD
    TiC MRF              N/A  (Medicare Advantage is federally exempt, so no MRF exists)
    PECOS                ENROLLED — "necessary, not sufficient — enrolled != in-network"
    Payer directory      UNAVAILABLE (ValueError)
    network_verdict      None

`reconcile` fell through to the 271's own network indicator, and that indicator said in-network — so
a plan-tier flag became a confident provider-level verdict backed by nothing. It is the exact false
IN this codebase exists to remove, and the one that bills wrong: the member is on an ARIZONA
Medicare HMO and the provider practises in Illinois.

The asymmetry is deliberate. A 271-derived OUT_OF_NETWORK still stands as a weak fallback: it is the
conservative direction, and a false OON is recoverable where a false IN is billed and denied.
"""

from network_probe.domain.eligibility import reconcile
from network_probe.domain.models import NetworkStatus, NetworkVerdict


def _verdict(status):
    return NetworkVerdict(status=status, matched_provider=None, plan_or_network_checked="x",
                          source_url="u", confidence="high", notes="n", corroboration=[])


def test_a_271_in_network_with_no_provider_verdict_is_not_in_network():
    """The reported bug: verdict is None because the directory errored, and the 271 said IN."""
    status, _ = reconcile(NetworkStatus.IN_NETWORK, None)
    assert status != NetworkStatus.IN_NETWORK
    assert status == NetworkStatus.UNKNOWN


def test_a_271_in_network_with_an_unknown_verdict_is_not_in_network():
    status, _ = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.UNKNOWN))
    assert status == NetworkStatus.UNKNOWN


def test_a_271_out_of_network_still_stands_as_a_weak_fallback():
    """Conservative direction, kept: a false OON is recoverable, a false IN is billed and denied."""
    assert reconcile(NetworkStatus.OUT_OF_NETWORK, None)[0] == NetworkStatus.OUT_OF_NETWORK
    assert reconcile(NetworkStatus.OUT_OF_NETWORK, _verdict(NetworkStatus.UNKNOWN))[0] == (
        NetworkStatus.OUT_OF_NETWORK)


def test_a_real_provider_verdict_still_wins_outright():
    """Credentialing / TiC / directory remain the authority — unchanged."""
    for s in (NetworkStatus.IN_NETWORK, NetworkStatus.OUT_OF_NETWORK, NetworkStatus.REVIEW):
        assert reconcile(NetworkStatus.UNKNOWN, _verdict(s))[0] == s


def test_a_provider_verdict_of_in_network_is_not_suppressed_by_a_silent_271():
    """Only the 271-ALONE case changes. A real IN from a provider source is untouched."""
    assert reconcile(NetworkStatus.UNKNOWN, _verdict(NetworkStatus.IN_NETWORK))[0] == (
        NetworkStatus.IN_NETWORK)
