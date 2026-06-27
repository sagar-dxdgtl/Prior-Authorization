"""NetworkVerdict carries an optional additive `evidence` block."""
from network_probe.domain.models import NetworkStatus, NetworkVerdict


def _v(**kw):
    base = dict(status=NetworkStatus.IN_NETWORK, matched_provider={"npi": "1"},
                plan_or_network_checked="x", source_url="u", confidence="high", notes="n")
    base.update(kw)
    return NetworkVerdict(**base)


def test_evidence_defaults_to_none_and_serializes():
    v = _v()
    assert v.evidence is None
    assert "evidence" in v.to_dict() and v.to_dict()["evidence"] is None


def test_evidence_roundtrips_in_to_dict():
    v = _v(evidence={"payer_directory": {"status": "IN_NETWORK"}, "signals": []})
    d = v.to_dict()
    assert d["evidence"]["payer_directory"]["status"] == "IN_NETWORK"
    assert d["evidence"]["signals"] == []
