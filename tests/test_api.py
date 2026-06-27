"""API wiring tests (offline). The verdict logic is tested per-adapter; here we only
check that the HTTP shell maps requests to the service and serializes verdicts."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from network_probe import api as api_mod  # noqa: E402
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery  # noqa: E402

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


# Task 23: /api/check + /api/check-from-report now require auth and write an audit row on success,
# so these HTTP-shell tests are db-marked and pass an auth_header (the verdict logic stays mocked).
@pytest.mark.db
def test_check_maps_service_verdict(monkeypatch, auth_header):
    fake = NetworkVerdict(
        status=NetworkStatus.IN_NETWORK, matched_provider={"npi": "1679766943", "name": "Kyle A Herron"},
        plan_or_network_checked="humana-fhir / network 'Medicare PPO'",
        source_url="https://fhir.humana.com/api/Practitioner?identifier=1679766943",
        confidence="high", notes="matched",
    )
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "humana-fhir", "plan": "Medicare PPO", "npi": "1679766943"},
                    headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "IN_NETWORK" and body["payer"] == "humana-fhir"
    assert body["matched_provider"]["npi"] == "1679766943"


@pytest.mark.db
def test_check_errors_return_400(monkeypatch, auth_header):
    def boom(q, **kw):
        raise ValueError("no adapter for payer 'nope'")
    monkeypatch.setattr(api_mod, "check_network", boom)
    r = client.post("/api/check", json={"payer": "nope", "plan": "x"}, headers=auth_header)
    assert r.status_code == 400
    # leak-free: a generic message + request id, never the internal exception string
    body = r.json()
    assert body["message"] == "could not complete check" and "request_id" in body
    assert "no adapter" not in r.text and "error" not in body


@pytest.mark.db
def test_check_from_report(monkeypatch, auth_header):
    parsed = {"payer_key": "oscar", "payer_name": "Oscar Health EDI", "npi": "1679766943",
              "provider_first": "Kyle", "provider_last": "Herron", "plan_name": "BASE SILVER",
              "policy_type": "HMO", "state": "FL", "zip": "33409", "member_id": "OSC1"}
    fake = NetworkVerdict(status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                          plan_or_network_checked="Oscar 066", source_url="u", confidence="high", notes="n")
    monkeypatch.setattr(api_mod, "parse_report", lambda src: parsed)
    monkeypatch.setattr(api_mod, "report_to_query", lambda p, client=None:
                        ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron"))
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check-from-report", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")},
                    headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OUT_OF_NETWORK" and body["parsed"]["payer_key"] == "oscar"


@pytest.mark.db
def test_check_includes_ground_truth(monkeypatch, auth_header):
    fake = NetworkVerdict(status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                          plan_or_network_checked="devoted PPO", source_url="u",
                          confidence="high", notes="override")
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "devoted", "plan": "PPO", "npi": "1629339312"},
                    headers=auth_header)
    assert r.status_code == 200
    gt = r.json()["ground_truth"]
    assert gt and gt["truth"] == "OUT_OF_NETWORK"


@pytest.mark.db
def test_check_ground_truth_none_for_unknown(monkeypatch, auth_header):
    fake = NetworkVerdict(status=NetworkStatus.IN_NETWORK, matched_provider=None,
                          plan_or_network_checked="x", source_url="u", confidence="high", notes="n")
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "oscar", "plan": "x", "npi": "0000000000"},
                    headers=auth_header)
    assert r.json()["ground_truth"] is None


def test_uhc_sample_carries_billing_tin_and_ground_truth():
    fr = next(s for s in api_mod.SAMPLES if "Fradkin" in s["label"])
    assert fr.get("tin") == "933510922"  # TiC-verified billing TIN so TIN-scope runs for the UHC case
    gt = api_mod.GROUND_TRUTH[("uhc", "1972603934")]
    assert gt["truth"] == "IN_NETWORK" and "933510922" in gt["note"]


def test_benchmark_lists_four_cases_all_caught():
    r = client.get("/api/benchmark")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 4
    assert all(row["caught"] for row in rows)
    rod = next(row for row in rows if "Rodriguez" in row["case"])
    assert rod["our_status"] == "OUT_OF_NETWORK" and "override" in rod["how"].lower()


def test_index_has_evidence_markers():
    r = client.get("/")
    assert r.status_code == 200
    # the evidence lanes + ground-truth banner are present; the aggregate scorecard was removed
    assert "Evidence by source" in r.text
    assert "renderLanes" in r.text and "renderGroundTruth" in r.text
    assert 'id="scorecard"' not in r.text
    assert "runProbe" in r.text and "multi-source network probe" in r.text
