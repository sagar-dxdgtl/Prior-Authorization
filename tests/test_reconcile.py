from network_probe.domain.eligibility import reconcile
from network_probe.domain.models import NetworkStatus, NetworkVerdict


def _verdict(status, corr=None):
    return NetworkVerdict(
        status=status, matched_provider=None, plan_or_network_checked="X",
        source_url="http://x", confidence="medium", notes="", corroboration=corr,
    )


def test_directory_in_vs_stedi_out_is_review():
    status, corr = reconcile(NetworkStatus.OUT_OF_NETWORK, _verdict(NetworkStatus.IN_NETWORK))
    assert status == NetworkStatus.REVIEW


def test_directory_out_vs_stedi_in_is_review():
    status, _ = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.OUT_OF_NETWORK))
    assert status == NetworkStatus.REVIEW


def test_stedi_unknown_takes_directory():
    status, _ = reconcile(NetworkStatus.UNKNOWN, _verdict(NetworkStatus.IN_NETWORK))
    assert status == NetworkStatus.IN_NETWORK


def test_agreement_keeps_status_and_passes_corroboration():
    status, corr = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.IN_NETWORK, corr=[{"source": "s"}]))
    assert status == NetworkStatus.IN_NETWORK and corr == [{"source": "s"}]


def test_no_verdict_keeps_stedi_status():
    status, corr = reconcile(NetworkStatus.OUT_OF_NETWORK, None)
    assert status == NetworkStatus.OUT_OF_NETWORK and corr == []
