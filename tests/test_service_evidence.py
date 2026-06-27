"""service.check_network attaches an evidence block: the raw payer-directory snapshot
(pre-finalize) plus the per-source display signals."""
from __future__ import annotations

from network_probe import service as svc
from network_probe.corroboration import Signal
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery


class _FakeAdapter:
    client = None
    def check_network(self, q):
        return NetworkVerdict(status=NetworkStatus.IN_NETWORK,
                              matched_provider={"npi": q.npi, "name": "Kyle A Herron"},
                              plan_or_network_checked="oscar / net 066", source_url="http://dir",
                              confidence="high", notes="found in directory.")


class _FakeStedi:
    name = "Stedi"
    def check(self, q, v):
        return Signal("Stedi", "inconclusive", "no provider-specific signal")


def _patch(monkeypatch):
    monkeypatch.setattr(svc, "get_adapter", lambda payer, **kw: _FakeAdapter())
    # keep it offline: replace the source set used inside check_network
    monkeypatch.setattr("network_probe.corroboration.default_sources", lambda client=None: [_FakeStedi()])


def test_evidence_has_raw_directory_snapshot(monkeypatch):
    _patch(monkeypatch)
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron")
    v = svc.check_network(q)
    assert v.evidence["payer_directory"]["status"] == "IN_NETWORK"
    assert v.evidence["payer_directory"]["matched_provider"]["npi"] == "1679766943"


def test_evidence_has_signals(monkeypatch):
    _patch(monkeypatch)
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron")
    v = svc.check_network(q)
    sources = {s["source"] for s in v.evidence["signals"]}
    assert "Stedi" in sources
