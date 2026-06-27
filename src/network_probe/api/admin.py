from __future__ import annotations

from fastapi import APIRouter, Depends

from network_probe.auth.deps import require_role
from network_probe.core.context import RequestContext
from network_probe.db.session import tenant_session
from network_probe.db.models import User, EligibilityCheck

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
def list_users(ctx: RequestContext = Depends(require_role("admin"))):
    with tenant_session(ctx.tenant_id) as s:
        users = s.query(User).order_by(User.created_at).all()
        return [
            {
                "id": str(u.id),
                "username": u.username,
                "role": u.role,
                "must_change_password": u.must_change_password,
                "locked_until": u.locked_until.isoformat() if u.locked_until else None,
            }
            for u in users
        ]  # NO password_hash


@router.get("/audit")
def list_audit(limit: int = 50, ctx: RequestContext = Depends(require_role("admin"))):
    with tenant_session(ctx.tenant_id) as s:
        rows = (
            s.query(EligibilityCheck)
            .order_by(EligibilityCheck.created_at.desc())
            .limit(min(max(limit, 1), 500))
            .all()
        )
        return [
            {
                "id": str(r.id),
                "action": r.action,
                "payer_key": r.payer_key,
                "npi": r.npi,
                "status": r.status,
                "member_id_hash": r.member_id_hash,
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "request_id": r.request_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]  # NO *_enc / result_jsonb PHI in the list view
