import pytest
from fastapi.testclient import TestClient

from network_probe.api import app


@pytest.mark.db
def test_protected_routes_require_auth():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.post("/api/eligibility", json={"payer": "oscar"}).status_code == 401
    assert c.post("/api/check", json={"payer": "oscar", "plan": ""}).status_code == 401
    assert c.get("/api/eligibility/ping").status_code == 401


@pytest.mark.db
def test_eligibility_ping_ok(auth_header):
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/eligibility/ping", headers=auth_header)
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.db
def test_eligibility_rejects_ssrf(auth_header):
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/eligibility", json={"payer": "fhir", "base_url": "http://169.254.169.254/"}, headers=auth_header)
    assert r.status_code == 400


@pytest.mark.db
def test_eligibility_audits_with_member(auth_header):
    # payer 'mystery' has no adapter + no catalogue id -> Stedi UNKNOWN, check_network raises -> no live call
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post(
        "/api/eligibility",
        json={"payer": "mystery", "npi": "1679766943", "member_id": "MBR-9", "dob": "01/02/1980"},
        headers=auth_header,
    )
    assert r.status_code == 200
    rid = r.json()["request_id"]
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import EligibilityCheck

    with Session(owner_engine()) as s:
        row = s.query(EligibilityCheck).filter_by(request_id=rid).one()
        assert row.action == "eligibility" and row.member_id_hash is not None and row.member_id_enc is not None


@pytest.mark.db
def test_check_does_not_leak_internal_errors(auth_header):
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/check", json={"payer": "does-not-exist-xyz", "plan": ""}, headers=auth_header)
    assert r.status_code in (400, 500)
    body = r.text
    assert "Traceback" not in body and "No adapter" not in body and "str(exc)" not in body


@pytest.mark.db
def test_validation_error_is_generic(auth_header):
    from fastapi.testclient import TestClient

    from network_probe.api import app

    c = TestClient(app, raise_server_exceptions=False)
    # payer is required; omit it to trigger a 422 — body must NOT echo input or expose pydantic internals
    r = c.post("/api/eligibility", json={"member_id": "SECRET-MBR"}, headers=auth_header)
    assert r.status_code == 422 and r.json().get("message") == "invalid request"
    assert "SECRET-MBR" not in r.text and "detail" not in r.json()


@pytest.mark.db
def test_oversized_body_rejected(auth_header):
    from fastapi.testclient import TestClient

    from network_probe.api import app

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/check", content=b"x" * 5, headers={**auth_header, "content-length": str(13 * 1024 * 1024)})
    assert r.status_code == 413


@pytest.mark.db
def test_payers_search_roster_hits(auth_header, seed_payers, monkeypatch):
    # Roster-first: the seeded Oscar row is returned as a roster option. Stedi fallback is mocked
    # out so the test stays fast and never hits the live payer directory.
    import network_probe.payers.search as search_mod

    monkeypatch.setattr(search_mod, "search_stedi", lambda *a, **k: [])
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/payers/search", params={"q": "oscar"}, headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body and any(o["label"] == "Oscar" and o["source"] == "roster" for o in body)


@pytest.mark.db
def test_payers_search_requires_auth():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/payers/search", params={"q": "aetna"}).status_code == 401
