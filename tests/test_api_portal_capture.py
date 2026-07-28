"""The async portal-capture endpoints.

A walk takes 40-210s live, so /api/portal/capture returns a job id at once and the UI polls.
No browser is driven here — the job store's runner is stubbed.
"""

import pytest
from fastapi.testclient import TestClient

from network_probe.api import app
from network_probe.portal.jobs import CaptureJobStore
from network_probe.portal.models import PortalCapture, PortalStatus

AZ = "bcbs-empire-anthem-elevance-az"
NPI = "1346866332"


def _client():
    return TestClient(app, raise_server_exceptions=False)


def _stub_store(monkeypatch, capture=None, boom=False):
    def runner(q):
        if boom:
            raise RuntimeError("browser died")
        return capture or PortalCapture(
            payer_key=q.payer_key, npi=q.npi, status=PortalStatus.OUT_OF_NETWORK,
            portal_name="AZ Blue / HealthSparq", portal_url="https://azblue.healthsparq.com/x",
            driver="azblue-healthsparq", note="stub", screenshot="shot.png",
        )

    store = CaptureJobStore(runner=runner)
    monkeypatch.setattr("network_probe.portal.jobs.default_capture_jobs", lambda: store)
    return store


@pytest.mark.db
def test_capture_routes_require_auth():
    c = _client()
    assert c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI}).status_code == 401
    assert c.get("/api/portal/capture/abc").status_code == 401


@pytest.mark.db
def test_start_returns_a_job_id_without_blocking(monkeypatch, auth_header):
    store = _stub_store(monkeypatch)
    r = _client().post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "provider_last_name": "Desir",
              "plan": "Statewide / National PPO", "zip": "85382"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["job_id"]
    store.wait(body["job_id"], timeout=5)


@pytest.mark.db
def test_poll_reports_the_portal_verdict_and_screenshot(monkeypatch, auth_header):
    store = _stub_store(monkeypatch)
    c = _client()
    job_id = c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI},
                    headers=auth_header).json()["job_id"]
    store.wait(job_id, timeout=5)
    body = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()
    assert body["status"] == "done"
    assert body["verdict"] == "OUT_OF_NETWORK"
    assert body["screenshot"] == "shot.png"


@pytest.mark.db
def test_a_dead_browser_is_an_error_job_not_a_500(monkeypatch, auth_header):
    store = _stub_store(monkeypatch, boom=True)
    c = _client()
    job_id = c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI},
                    headers=auth_header).json()["job_id"]
    store.wait(job_id, timeout=5)
    body = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()
    assert body["status"] == "error"
    assert "browser died" in body["error"]


@pytest.mark.db
def test_invalid_npi_and_unknown_payer_are_rejected(monkeypatch, auth_header):
    _stub_store(monkeypatch)
    c = _client()
    assert c.post("/api/portal/capture", json={"payer_key": AZ, "npi": "123"},
                  headers=auth_header).status_code == 400
    assert c.post("/api/portal/capture", json={"payer_key": "no-such-payer", "npi": NPI},
                  headers=auth_header).status_code == 404


@pytest.mark.db
def test_unknown_job_is_404(monkeypatch, auth_header):
    _stub_store(monkeypatch)
    assert _client().get("/api/portal/capture/deadbeef", headers=auth_header).status_code == 404


@pytest.mark.parametrize("name", ["../../../../etc/passwd", "..%2Fsecret.png", "notapng.txt", "a/b.png"])
def test_screenshot_endpoint_refuses_path_traversal(name):
    """Screenshots are served by name, so the name must never walk out of LIVE_SHOT_DIR."""
    r = _client().get(f"/api/portal/screenshot/{name}")
    assert r.status_code in (400, 404), f"{name} returned {r.status_code}"


@pytest.mark.db
def test_provider_name_is_resolved_from_nppes_not_supplied_by_the_caller(monkeypatch, auth_header):
    """A portal is searched by PROVIDER name + clinic ZIP. The eligibility form's first/last name
    fields hold the MEMBER's name (they sit between Member ID and DOB), and a member name reaching
    a payer's public search box is exactly what HANDOFF §7 forbids. So when no provider name is
    given, the server resolves it from NPPES by NPI rather than trusting the caller."""
    seen = {}

    def runner(q):
        seen["first"], seen["last"] = q.provider_first_name, q.provider_last_name
        return PortalCapture(
            payer_key=q.payer_key, npi=q.npi, status=PortalStatus.UNKNOWN,
            portal_name="AZ Blue / HealthSparq", portal_url="u", driver="d", note="n",
        )

    store = CaptureJobStore(runner=runner)
    monkeypatch.setattr("network_probe.portal.jobs.default_capture_jobs", lambda: store)
    monkeypatch.setattr(
        "network_probe.domain.report_ingest._nppes_name", lambda npi, client: ("Hedson", "Desir")
    )
    job_id = _client().post(
        "/api/portal/capture", json={"payer_key": AZ, "npi": NPI}, headers=auth_header
    ).json()["job_id"]
    store.wait(job_id, timeout=5)
    assert seen == {"first": "Hedson", "last": "Desir"}
