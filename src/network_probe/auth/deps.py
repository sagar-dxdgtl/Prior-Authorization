from __future__ import annotations

import uuid

from fastapi import Header, HTTPException

from network_probe.auth.jwt_tokens import TokenError, decode_token
from network_probe.core.context import RequestContext
from network_probe.db.models import User
from network_probe.db.session import tenant_session


def _load_user(user_id, tenant_id):
    with tenant_session(tenant_id) as s:
        return s.get(User, user_id)


def context_from_token(token: str, allow_password_change: bool = False) -> RequestContext:
    try:
        c = decode_token(token, expected_typ="access")
    except TokenError:
        raise HTTPException(status_code=401, detail={"message": "invalid token"})
    tid, uid = uuid.UUID(c["tid"]), uuid.UUID(c["sub"])
    u = _load_user(uid, tid)
    if not u or u.token_version != c.get("tv"):
        raise HTTPException(status_code=401, detail={"message": "invalid token"})
    if getattr(u, "must_change_password", False) and not allow_password_change:
        raise HTTPException(status_code=403, detail={"message": "password change required"})
    return RequestContext(tenant_id=tid, actor_id=uid, role=c.get("role", "user"))


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"message": "missing bearer token"})
    return authorization.split(" ", 1)[1].strip()


def get_context(authorization: str | None = Header(default=None)) -> RequestContext:
    return context_from_token(_bearer(authorization), allow_password_change=False)


def get_context_pwchange(authorization: str | None = Header(default=None)) -> RequestContext:
    return context_from_token(_bearer(authorization), allow_password_change=True)
