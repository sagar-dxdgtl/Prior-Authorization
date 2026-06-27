from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import text

from network_probe.auth import jwt_tokens as jt
from network_probe.auth.deps import get_context_pwchange
from network_probe.auth.passwords import DUMMY_HASH, check_policy, hash_password, verify_password
from network_probe.core.context import RequestContext
from network_probe.db.base import app_engine
from network_probe.db.models import User
from network_probe.db.session import tenant_session

router = APIRouter(prefix="/api/auth", tags=["auth"])
LOCK_THRESHOLD, LOCK_MINUTES = 5, 15

def _lookup(username: str):
    """RLS-exempt minimal lookup via the SECURITY DEFINER function (app role has EXECUTE)."""
    with app_engine().connect() as conn:
        return conn.execute(text("SELECT * FROM auth_lookup_user(:u)"), {"u": username}).mappings().first()

def _user_payload(row, username: str = "") -> dict:
    return {"id": str(row["id"]), "username": username, "name": username, "role": row["role"],
            "tenant_id": str(row["tenant_id"])}

@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    now = datetime.now(UTC)
    row = _lookup(form.username)
    if row and row["locked_until"] and row["locked_until"] > now:
        raise HTTPException(status_code=429, detail={"message": "account temporarily locked"})
    # constant-time: unknown user still pays a bcrypt verify (against DUMMY_HASH)
    ok = verify_password(form.password, row["password_hash"]) if row else verify_password(form.password, DUMMY_HASH)
    if not row or not ok:
        if row:
            # ATOMIC failure increment + lock (single statement; both CASEs read the pre-update value).
            with tenant_session(row["tenant_id"]) as s:
                s.execute(text(
                    "UPDATE users SET "
                    "failed_logins = CASE WHEN failed_logins + 1 >= :thr THEN 0 ELSE failed_logins + 1 END, "
                    "locked_until = CASE WHEN failed_logins + 1 >= :thr THEN :until ELSE locked_until END "
                    "WHERE id = :id"),
                    {"thr": LOCK_THRESHOLD, "until": now + timedelta(minutes=LOCK_MINUTES), "id": row["id"]})
        raise HTTPException(status_code=401, detail={"message": "invalid credentials"})
    # success: reset counters
    with tenant_session(row["tenant_id"]) as s:
        s.execute(text("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = :id"), {"id": row["id"]})
    access, expires_in = jt.issue_access(row["id"], row["tenant_id"], row["role"], row["token_version"])
    refresh = jt.issue_refresh(row["id"], row["tenant_id"], row["role"], row["token_version"])
    if row["must_change_password"]:
        return {"must_change_password": True, "tokens": {"access": access}, "user": _user_payload(row, form.username)}
    return {"access_token": access, "expires_in": expires_in, "refresh_token": refresh, "user": _user_payload(row, form.username)}

class RefreshReq(BaseModel):
    refresh_token: str

@router.post("/refresh")
def refresh(req: RefreshReq):
    try:
        c = jt.decode_token(req.refresh_token, expected_typ="refresh")
    except jt.TokenError:
        raise HTTPException(status_code=401, detail={"message": "invalid refresh token"})
    uid, tid = uuid.UUID(c["sub"]), uuid.UUID(c["tid"])
    with tenant_session(tid) as s:
        u = s.get(User, uid)
        if not u or u.token_version != c.get("tv"):
            raise HTTPException(status_code=401, detail={"message": "invalid refresh token"})
        access, expires_in = jt.issue_access(u.id, u.tenant_id, u.role, u.token_version)
    return {"access_token": access, "expires_in": expires_in}   # no new refresh per frontend contract

class ChangePwReq(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

@router.post("/change-password/")
def change_password(req: ChangePwReq, ctx: RequestContext = Depends(get_context_pwchange)):
    if req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail={"message": "passwords do not match", "success": False})
    try:
        check_policy(req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e), "success": False})
    with tenant_session(ctx.tenant_id) as s:
        u = s.get(User, ctx.actor_id)
        if not u or not verify_password(req.current_password, u.password_hash):
            raise HTTPException(status_code=400, detail={"message": "current password incorrect", "success": False})
        u.password_hash = hash_password(req.new_password)
        u.must_change_password = False
        u.token_version += 1
        s.commit()
    return {"success": True}
