import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from network_probe.api import app
from network_probe.core.config import get_settings
from network_probe.core.context import RequestContext
from network_probe.core.crypto import hash_member_id
from network_probe.db.base import owner_engine
from network_probe.db.models import ReviewCase
from network_probe.domain.audit import write_audit
from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, ProviderQuery


def _review_result(status: NetworkStatus = NetworkStatus.REVIEW) -> EligibilityResult:
    return EligibilityResult(
        coverage_active=None,
        plan_name=None,
        group=None,
        coverage_dates={},
        network_status=status,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=None,
        corroboration=[],
        source_audit={"source": "stedi"},
    )


def _open_case(tenant_id, member_id="PLAINTEXT-MBR-999", npi="1679766943", request_id="req-rev-1"):
    """Drive a REVIEW verdict through write_audit so a case auto-opens for `tenant_id`."""
    ctx = RequestContext(tenant_id=tenant_id, actor_id=uuid.uuid4(), role="user")
    q = ProviderQuery(payer="oscar", plan_hint="", npi=npi, member_id=member_id, dob="1980-01-02")
    write_audit(ctx, "eligibility", q, _review_result(), request_id)
    return ctx, q


def _reviewer_header(tenant_id):
    """Mint a reviewer JWT for an arbitrary tenant (mirrors conftest._make_user_header)."""
    from network_probe.auth import jwt_tokens as jt
    from network_probe.auth.passwords import hash_password
    from network_probe.db.models import User

    uid = uuid.uuid4()
    with Session(owner_engine()) as s:
        s.add(
            User(
                id=uid,
                tenant_id=tenant_id,
                username=f"reviewer-{uid.hex[:6]}",
                password_hash=hash_password("x" * 12),
                role="reviewer",
                must_change_password=False,
                token_version=0,
            )
        )
        s.commit()
    tok, _ = jt.issue_access(uid, tenant_id, "reviewer", 0)
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.db
def test_review_verdict_auto_opens_case(demo_tenant):
    _open_case(demo_tenant, member_id="PLAINTEXT-MBR-999")
    with Session(owner_engine()) as s:
        rows = s.query(ReviewCase).filter_by(tenant_id=demo_tenant).all()
        assert len(rows) == 1
        case = rows[0]
        assert case.status == "open"
        assert case.payer_key == "oscar"
        assert case.eligibility_check_id is not None
        # PHI: only the keyed hash is stored, never the plaintext member id.
        assert case.member_id_hash == hash_member_id("PLAINTEXT-MBR-999", get_settings().member_id_pepper)
        blob = " ".join(
            str(v) for v in (case.member_id_hash, case.payer_key, case.npi, case.status, case.resolution)
        )
        assert "PLAINTEXT-MBR-999" not in blob


@pytest.mark.db
def test_non_review_verdict_does_not_open_case(demo_tenant):
    ctx = RequestContext(tenant_id=demo_tenant, actor_id=uuid.uuid4(), role="user")
    q = ProviderQuery(payer="oscar", plan_hint="", npi="1679766943")
    write_audit(ctx, "eligibility", q, _review_result(NetworkStatus.IN_NETWORK), "req-in")
    with Session(owner_engine()) as s:
        assert s.query(ReviewCase).filter_by(tenant_id=demo_tenant).count() == 0


@pytest.mark.db
def test_reviewer_flow_list_claim_note_resolve(demo_tenant, reviewer_header):
    _open_case(demo_tenant)
    c = TestClient(app, raise_server_exceptions=False)

    # list open cases
    r = c.get("/api/review/cases", params={"status": "open"}, headers=reviewer_header)
    assert r.status_code == 200
    cases = r.json()
    assert len(cases) == 1
    case_id = cases[0]["id"]
    assert cases[0]["member_id_hash"] and "PLAINTEXT-MBR-999" not in r.text

    # claim
    r = c.post(f"/api/review/cases/{case_id}/claim", headers=reviewer_header)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_review"
    assert body["assignee_id"] is not None

    # add a note
    r = c.post(
        f"/api/review/cases/{case_id}/notes",
        json={"text": "Called payer, awaiting callback."},
        headers=reviewer_header,
    )
    assert r.status_code == 200
    note = r.json()
    assert note["text"] == "Called payer, awaiting callback."
    assert note["author_id"] is not None

    # get case shows the note
    r = c.get(f"/api/review/cases/{case_id}", headers=reviewer_header)
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == case_id
    assert len(detail["notes"]) == 1
    assert detail["notes"][0]["text"] == "Called payer, awaiting callback."

    # resolve
    r = c.post(
        f"/api/review/cases/{case_id}/resolve",
        json={"resolution": "Confirmed IN_NETWORK by payer rep."},
        headers=reviewer_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "Confirmed IN_NETWORK by payer rep."


@pytest.mark.db
def test_get_unknown_case_404(demo_tenant, reviewer_header):
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(f"/api/review/cases/{uuid.uuid4()}", headers=reviewer_header)
    assert r.status_code == 404


@pytest.mark.db
def test_user_role_forbidden_on_review(demo_tenant, auth_header):
    _open_case(demo_tenant)
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/review/cases", headers=auth_header).status_code == 403
    cid = uuid.uuid4()
    assert c.get(f"/api/review/cases/{cid}", headers=auth_header).status_code == 403
    assert c.post(f"/api/review/cases/{cid}/claim", headers=auth_header).status_code == 403
    assert c.post(f"/api/review/cases/{cid}/notes", json={"text": "x"}, headers=auth_header).status_code == 403
    assert c.post(f"/api/review/cases/{cid}/resolve", json={"resolution": "x"}, headers=auth_header).status_code == 403


@pytest.mark.db
def test_tenant_isolation_review_cases(demo_tenant):
    from network_probe.db.models import Tenant

    # case belongs to tenant A (demo_tenant)
    _open_case(demo_tenant)

    # tenant B + its reviewer
    tid_b = uuid.uuid4()
    with Session(owner_engine()) as s:
        s.add(Tenant(id=tid_b, name="B", slug=f"b-{tid_b.hex[:8]}"))
        s.commit()
    header_b = _reviewer_header(tid_b)

    c = TestClient(app, raise_server_exceptions=False)
    # reviewer B sees none of A's cases
    r = c.get("/api/review/cases", headers=header_b)
    assert r.status_code == 200 and r.json() == []

    # and cannot fetch A's case by id (404 — not in B's tenant)
    with Session(owner_engine()) as s:
        a_case_id = s.query(ReviewCase).filter_by(tenant_id=demo_tenant).one().id
    r = c.get(f"/api/review/cases/{a_case_id}", headers=header_b)
    assert r.status_code == 404
