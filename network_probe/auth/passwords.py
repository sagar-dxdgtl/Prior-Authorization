from __future__ import annotations
from passlib.context import CryptContext

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

def hash_password(pw: str) -> str:
    return _ctx.hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ctx.verify(pw, hashed)
    except Exception:
        return False

def check_policy(pw: str) -> None:
    if len(pw or "") < 12:
        raise ValueError("password must be at least 12 characters")

# Pre-computed hash of a value no user can have — verified against in login when the
# username is unknown, so existent vs non-existent users take comparable time (anti-enumeration).
DUMMY_HASH = hash_password("not-a-real-password-constant-time-guard")
