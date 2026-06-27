from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
import jwt
from ..config import get_settings

ALG = "HS256"

class TokenError(Exception): ...

def _issue(typ: str, ttl: int, user_id, tenant_id, role, token_version):
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "tid": str(tenant_id), "role": role,
               "typ": typ, "tv": token_version,
               "iat": now, "exp": now + timedelta(seconds=ttl)}
    return jwt.encode(payload, s.jwt_secret, algorithm=ALG), ttl

def issue_access(user_id, tenant_id, role, token_version):
    return _issue("access", get_settings().jwt_access_ttl, user_id, tenant_id, role, token_version)

def issue_refresh(user_id, tenant_id, role, token_version):
    tok, _ = _issue("refresh", get_settings().jwt_refresh_ttl, user_id, tenant_id, role, token_version)
    return tok

def decode_token(token: str, expected_typ: str) -> dict:
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALG],
                            options={"require": ["exp", "iat", "sub"]})
    except Exception as e:
        raise TokenError(str(e))
    if claims.get("typ") != expected_typ:
        raise TokenError("wrong token type")
    return claims
