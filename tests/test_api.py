"""API wiring tests (offline). The verdict logic is tested per-adapter; here we only
check that the HTTP shell maps requests to the service and serializes verdicts."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from network_probe import api as api_mod  # noqa: E402
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery  # noqa: E402

client = TestClient(api_mod.app)


def test_index_serves_ui():
    r = client.get("/")
    assert r.status_code == 200
    assert "Provider Network Verification" in r.text


def test_payers_lists_adapters():
    r = client.get("/api/payers")
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()}
    assert {"oscar", "devoted", "humana-fhir"} <= keys
    # the demo answers must not be telegraphed in the UI metadata
    assert all("expect" not in (p.get("example_label") or "").lower() for p in r.json())


def test_check_maps_service_verdict(monkeypatch):
    fake = NetworkVerdict(
        status=NetworkStatus.IN_NETWORK, matched_provider={"npi": "1679766943", "name": "Kyle A Herron"},
        plan_or_network_checked="humana-fhir / network 'Medicare PPO'",
        source_url="https://fhir.humana.com/api/Practitioner?identifier=1679766943",
        confidence="high", notes="matched",
    )
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "humana-fhir", "plan": "Medicare PPO", "npi": "1679766943"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "IN_NETWORK" and body["payer"] == "humana-fhir"
    assert body["matched_provider"]["npi"] == "1679766943"


def test_check_errors_return_400(monkeypatch):
    def boom(q, **kw):
        raise ValueError("no adapter for payer 'nope'")
    monkeypatch.setattr(api_mod, "check_network", boom)
    r = client.post("/api/check", json={"payer": "nope", "plan": "x"})
    assert r.status_code == 400
    assert "no adapter" in r.json()["error"]


def test_check_from_report(monkeypatch):
    parsed = {"payer_key": "oscar", "payer_name": "Oscar Health EDI", "npi": "1679766943",
              "provider_first": "Kyle", "provider_last": "Herron", "plan_name": "BASE SILVER",
              "policy_type": "HMO", "state": "FL", "zip": "33409", "member_id": "OSC1"}
    fake = NetworkVerdict(status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                          plan_or_network_checked="Oscar 066", source_url="u", confidence="high", notes="n")
    monkeypatch.setattr(api_mod, "parse_report", lambda src: parsed)
    monkeypatch.setattr(api_mod, "report_to_query", lambda p, client=None:
                        ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron"))
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check-from-report", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OUT_OF_NETWORK" and body["parsed"]["payer_key"] == "oscar"


def test_check_includes_ground_truth(monkeypatch):
    fake = NetworkVerdict(status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                          plan_or_network_checked="devoted PPO", source_url="u",
                          confidence="high", notes="override")
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "devoted", "plan": "PPO", "npi": "1629339312"})
    assert r.status_code == 200
    gt = r.json()["ground_truth"]
    assert gt and gt["truth"] == "OUT_OF_NETWORK"


def test_check_ground_truth_none_for_unknown(monkeypatch):
    fake = NetworkVerdict(status=NetworkStatus.IN_NETWORK, matched_provider=None,
                          plan_or_network_checked="x", source_url="u", confidence="high", notes="n")
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "oscar", "plan": "x", "npi": "0000000000"})
    assert r.json()["ground_truth"] is None


def test_uhc_sample_carries_billing_tin_and_ground_truth():
    fr = next(s for s in api_mod.SAMPLES if "Fradkin" in s["label"])
    assert fr.get("tin") == "933510922"  # TiC-verified billing TIN so TIN-scope runs for the UHC case
    gt = api_mod.GROUND_TRUTH[("uhc", "1972603934")]
    assert gt["truth"] == "IN_NETWORK" and "933510922" in gt["note"]


def test_uhc_houston_oon_sample_and_ground_truth():
    hou = next(s for s in api_mod.SAMPLES if "Srinivas Rao" in s["label"])
    assert hou["payer"] == "uhc" and hou["npi"] == "1972941318" and hou["tin"] == "412049581"
    assert "OON" in hou["label"]
    gt = api_mod.GROUND_TRUTH[("uhc", "1972941318")]
    assert gt["truth"] == "OUT_OF_NETWORK" and "412049581" in gt["note"]


def test_oon_alias_resolves_houston_to_cached_member(monkeypatch):
    # OON is the member's; the Houston rendering NPI aliases to the NPI Salman's benefits were
    # cached under, so the OON tab still populates for the OON example.
    from network_probe import oon_benefits as oon_mod
    cache = {"1972603934": {"npi": "1972603934", "patient": "Sobia Salman", "payer_key": "uhc",
                            "benefits": [{"network_code": "Y"}], "oon_count": 0}}
    monkeypatch.setattr(oon_mod, "load_oon", lambda npi=None: cache if npi is None else cache.get(npi))
    body = client.get("/api/oon", params={"npi": "1972941318"}).json()
    assert body["available"] is True and body["patient"] == "Sobia Salman" and body["npi"] == "1972603934"
    # a member with no cache entry and no alias stays unavailable
    miss = client.get("/api/oon", params={"npi": "0000000000"}).json()
    assert miss["available"] is False


def test_benchmark_lists_four_cases_all_caught():
    r = client.get("/api/benchmark")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 4
    assert all(row["caught"] for row in rows)
    rod = next(row for row in rows if "Rodriguez" in row["case"])
    # Rodriguez is now caught by Devoted's compliant FHIR directory (OON directly), not the override.
    assert rod["our_status"] == "OUT_OF_NETWORK" and "fhir" in rod["how"].lower()


def test_rodriguez_sample_uses_compliant_fhir_directory():
    rod = next(s for s in api_mod.SAMPLES if "Rodriguez" in s["label"])
    assert rod["payer"] == "devoted-fhir" and rod["npi"] == "1629339312"
    # ground truth exists for the FHIR-directory routing and cites the compliant directory
    gt = api_mod.GROUND_TRUTH[("devoted-fhir", "1629339312")]
    assert gt["truth"] == "OUT_OF_NETWORK" and "fhir.devoted.com" in gt["note"]
    # devoted-fhir is an offered payer bound to the public CMS FHIR endpoint
    from network_probe.adapters.fhir_pdex import KNOWN_ENDPOINTS
    assert KNOWN_ENDPOINTS["devoted-fhir"] == "https://fhir.devoted.com/fhir"


def test_index_has_evidence_markers():
    r = client.get("/")
    assert r.status_code == 200
    # the evidence lanes + ground-truth banner are present; the aggregate scorecard was removed
    assert "Evidence by source" in r.text
    assert "renderLanes" in r.text and "renderGroundTruth" in r.text
    assert 'id="scorecard"' not in r.text
    assert "runProbe" in r.text and "multi-source network probe" in r.text
