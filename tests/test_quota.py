import uuid

import pytest
from fastapi.testclient import TestClient

from network_probe.api import app
from network_probe.db.base import owner_engine

# payer 'mystery' has no adapter + no catalogue id -> Stedi UNKNOWN, check_network raises -> NO live call.
# No member id either, so this stays pure-local while still flowing through the metered route.
_ELIG = {"payer": "mystery", "npi": "1679766943"}


def _post_elig(c, headers):
    return c.post("/api/eligibility", json=_ELIG, headers=headers)


def _fresh_tenant_header(role: str = "user"):
    """Create a brand-new tenant + user and mint an access token (mirrors conftest._make_user_header)."""
    from sqlalchemy.orm import Session

    from network_probe.auth import jwt_tokens as jt
    from network_probe.auth.passwords import hash_password
    from network_probe.db.models import Tenant, User

    tid = uuid.uuid4()
    uid = uuid.uuid4()
    with Session(owner_engine()) as s:
        s.add(Tenant(id=tid, name="QuotaB", slug=f"qb-{tid.hex[:8]}"))
        s.add(
            User(
                id=uid,
                tenant_id=tid,
                username=f"{role}-{uid.hex[:6]}",
                password_hash=hash_password("x" * 12),
                role=role,
                must_change_password=False,
                token_version=0,
            )
        )
        s.commit()
    tok, _ = jt.issue_access(uid, tid, role, 0)
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.db
def test_quota_enforced_returns_429_over_limit(monkeypatch, auth_header):
    monkeypatch.setattr("network_probe.api.quota._quota_limits", lambda: (2, 100))
    c = TestClient(app, raise_server_exceptions=False)
    assert _post_elig(c, auth_header).status_code == 200
    assert _post_elig(c, auth_header).status_code == 200
    r = _post_elig(c, auth_header)
    assert r.status_code == 429
    assert r.json() == {"message": "quota exceeded"}


@pytest.mark.db
def test_quota_headers_reflect_real_db_counts(monkeypatch, auth_header):
    monkeypatch.setattr("network_probe.api.quota._quota_limits", lambda: (5, 100))
    c = TestClient(app, raise_server_exceptions=False)
    r1 = _post_elig(c, auth_header)
    assert r1.status_code == 200
    assert r1.headers["x-quota-daily-limit"] == "5"
    assert r1.headers["x-quota-daily-used"] == "1"
    assert r1.headers["x-quota-daily-remaining"] == "4"
    r2 = _post_elig(c, auth_header)
    assert r2.headers["x-quota-daily-used"] == "2"
    assert r2.headers["x-quota-daily-remaining"] == "3"
    # monthly counter advances in lockstep with its own DB row
    assert r2.headers["x-quota-monthly-used"] == "2"


@pytest.mark.db
def test_quota_isolated_per_tenant(monkeypatch, auth_header):
    monkeypatch.setattr("network_probe.api.quota._quota_limits", lambda: (2, 100))
    c = TestClient(app, raise_server_exceptions=False)
    # tenant A exhausts its daily quota
    assert _post_elig(c, auth_header).status_code == 200
    assert _post_elig(c, auth_header).status_code == 200
    assert _post_elig(c, auth_header).status_code == 429
    # tenant B is entirely unaffected by A hitting its limit
    header_b = _fresh_tenant_header()
    rb = _post_elig(c, header_b)
    assert rb.status_code == 200
    assert rb.headers["x-quota-daily-used"] == "1"


@pytest.mark.db
def test_unmetered_routes_never_429(monkeypatch, auth_header):
    monkeypatch.setattr("network_probe.api.quota._quota_limits", lambda: (1, 1))
    c = TestClient(app, raise_server_exceptions=False)
    # blow past the metered quota
    assert _post_elig(c, auth_header).status_code == 200
    assert _post_elig(c, auth_header).status_code == 429
    # unmetered routes keep serving regardless of quota state
    for _ in range(3):
        assert c.get("/api/payers").status_code == 200
        assert c.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code != 429
