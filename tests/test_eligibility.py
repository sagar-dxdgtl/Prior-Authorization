import pytest
from network_probe.models import ProviderQuery, NetworkStatus, NetworkVerdict
from network_probe.benefits import EligibilityResult
from network_probe import eligibility as elig

class FakeCat:
    def __init__(self, pid): self._pid = pid
    def resolve(self, key):
        class P: pass
        P.stedi_payer_id = self._pid
        return P() if self._pid is not None else None

class FakeStedi:
    def __init__(self, result): self.result = result
    def check(self, q): return self.result

def _res(status):
    return EligibilityResult(coverage_active=True, plan_name=None, group=None, coverage_dates={},
        network_status=status, benefits=[], pcp_required=None, prior_auth_required=None,
        referral_required=None, cob=None, network_verdict=None, corroboration=[], source_audit={})

def _verdict(status):
    return NetworkVerdict(status=status, matched_provider=None, plan_or_network_checked="x",
                          source_url="u", confidence="high", notes="n", corroboration=[])

def test_directory_in_stedi_oon_is_review(monkeypatch):
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.IN_NETWORK))
    r = elig.check_eligibility(ProviderQuery(payer="oscar", plan_hint=""),
                               catalogue=FakeCat("OSCAR"), stedi=FakeStedi(_res(NetworkStatus.OUT_OF_NETWORK)))
    assert r.network_status == NetworkStatus.REVIEW and r.network_verdict is not None

def test_directory_oon_stedi_in_is_review(monkeypatch):
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.OUT_OF_NETWORK))
    r = elig.check_eligibility(ProviderQuery(payer="oscar", plan_hint=""),
                               catalogue=FakeCat("OSCAR"), stedi=FakeStedi(_res(NetworkStatus.IN_NETWORK)))
    assert r.network_status == NetworkStatus.REVIEW

def test_stedi_unknown_adopts_directory(monkeypatch):
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.IN_NETWORK))
    r = elig.check_eligibility(ProviderQuery(payer="oscar", plan_hint=""),
                               catalogue=FakeCat("OSCAR"), stedi=FakeStedi(_res(NetworkStatus.UNKNOWN)))
    assert r.network_status == NetworkStatus.IN_NETWORK

def test_no_directory_adapter_keeps_stedi(monkeypatch):
    def boom(q, **k): raise ValueError("No adapter")
    monkeypatch.setattr(elig, "check_network", boom)
    r = elig.check_eligibility(ProviderQuery(payer="mystery", plan_hint=""),
                               catalogue=FakeCat(None), stedi=FakeStedi(_res(NetworkStatus.OUT_OF_NETWORK)))
    assert r.network_status == NetworkStatus.OUT_OF_NETWORK and r.network_verdict is None

def test_base_url_threaded_into_check_network(monkeypatch):
    captured = {}
    def cn(q, **k):
        captured.update(k); return _verdict(NetworkStatus.UNKNOWN)
    monkeypatch.setattr(elig, "check_network", cn)
    elig.check_eligibility(ProviderQuery(payer="fhir", plan_hint=""), base_url="https://fhir.example/api",
                           catalogue=FakeCat(None), stedi=FakeStedi(_res(NetworkStatus.UNKNOWN)))
    assert captured.get("base_url") == "https://fhir.example/api"
