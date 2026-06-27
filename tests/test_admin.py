import pytest
from fastapi.testclient import TestClient
from network_probe.api import app


@pytest.mark.db
def test_user_forbidden_on_admin(auth_header):
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users", headers=auth_header).status_code == 403
    assert c.get("/api/admin/audit", headers=auth_header).status_code == 403


@pytest.mark.db
def test_admin_lists_users_no_hash(admin_header):
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/admin/users", headers=admin_header)
    assert r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) >= 1
    assert all("password_hash" not in u for u in r.json())


@pytest.mark.db
def test_admin_audit_redacted(admin_header, demo_tenant):
    # seed one audit row with PHI, then confirm the admin list view never exposes the encrypted PHI
    import uuid
    from network_probe.domain.audit import write_audit
    from network_probe.core.context import RequestContext
    from network_probe.domain.models import ProviderQuery, NetworkStatus
    from network_probe.domain.benefits import EligibilityResult

    ctx = RequestContext(tenant_id=demo_tenant, actor_id=uuid.uuid4(), role="admin")
    q = ProviderQuery(
        payer="oscar",
        plan_hint="",
        npi="1679766943",
        member_id="SECRET-M",
        dob="1980-01-02",
    )
    res = EligibilityResult(
        coverage_active=True,
        plan_name=None,
        group=None,
        coverage_dates={},
        network_status=NetworkStatus.REVIEW,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=None,
        corroboration=[],
        source_audit={},
    )
    write_audit(ctx, "eligibility", q, res, "req-admin-1")
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/admin/audit", headers=admin_header)
    assert r.status_code == 200
    body = r.text
    assert "SECRET-M" not in body and "member_id_enc" not in body and "1980-01-02" not in body
    assert any(row["request_id"] == "req-admin-1" and row["member_id_hash"] for row in r.json())
