"""reconcile(): merge the 271-derived status with the provider-network verdict.

Re-ranked: the provider-network verdict (credentialing / TiC / directory / enrollment) is the
AUTHORITY on provider network. A 271 gives coverage + the plan's OON tier — NOT reliable
provider-specific network — so a decisive verdict wins outright and a 271/verdict disagreement is
NOT a conflict worth REVIEW. Genuine provider-source conflicts (e.g. credentialing vs TiC) already
arrive as verdict.status == REVIEW. The 271 status is only a fallback when the verdict is silent.
"""

from network_probe.domain.eligibility import reconcile
from network_probe.domain.models import NetworkStatus, NetworkVerdict


def _verdict(status, corr=None):
    return NetworkVerdict(
        status=status, matched_provider=None, plan_or_network_checked="X",
        source_url="http://x", confidence="high", notes="", corroboration=corr,
    )


def test_provider_verdict_in_overrides_stedi_out():
    # provider-network sources are the authority; the 271 never forces a REVIEW
    status, _ = reconcile(NetworkStatus.OUT_OF_NETWORK, _verdict(NetworkStatus.IN_NETWORK))
    assert status == NetworkStatus.IN_NETWORK


def test_provider_verdict_out_overrides_stedi_in():
    # Perry/Munar: credentialing/TiC "OON" must win over the 271's unreliable "IN" indicator
    status, _ = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.OUT_OF_NETWORK))
    assert status == NetworkStatus.OUT_OF_NETWORK


def test_stedi_unknown_takes_verdict():
    status, _ = reconcile(NetworkStatus.UNKNOWN, _verdict(NetworkStatus.IN_NETWORK))
    assert status == NetworkStatus.IN_NETWORK


def test_genuine_provider_conflict_is_preserved_as_review():
    # a real credentialing-vs-TiC conflict arrives as verdict.status == REVIEW and must survive
    status, _ = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.REVIEW))
    assert status == NetworkStatus.REVIEW


def test_verdict_unknown_falls_back_to_stedi():
    # provider sources silent -> the (weak) 271 status is the last-resort fallback
    status, _ = reconcile(NetworkStatus.OUT_OF_NETWORK, _verdict(NetworkStatus.UNKNOWN))
    assert status == NetworkStatus.OUT_OF_NETWORK


def test_agreement_keeps_status_and_passes_corroboration():
    status, corr = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.IN_NETWORK, corr=[{"source": "s"}]))
    assert status == NetworkStatus.IN_NETWORK and corr == [{"source": "s"}]


def test_no_verdict_keeps_stedi_status():
    status, corr = reconcile(NetworkStatus.OUT_OF_NETWORK, None)
    assert status == NetworkStatus.OUT_OF_NETWORK and corr == []
