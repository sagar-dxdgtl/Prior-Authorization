from __future__ import annotations

import base64
import hashlib

import bcrypt


def _prepare(pw: str) -> bytes:
    # bcrypt only uses the first 72 bytes (5.x raises on longer). Pre-hash longer
    # inputs so the WHOLE password contributes; base64 keeps it printable and < 72 bytes.
    raw = (pw or "").encode("utf-8")
    if len(raw) > 72:
        raw = base64.b64encode(hashlib.sha256(raw).digest())
    return raw


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(_prepare(pw), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(pw), hashed.encode("ascii"))
    except Exception:
        return False


def check_policy(pw: str) -> None:
    if len(pw or "") < 12:
        raise ValueError("password must be at least 12 characters")


# Pre-computed hash of a value no user can have — verified against when the username is
# unknown so existent vs non-existent users take comparable time (anti-enumeration).
DUMMY_HASH = hash_password("not-a-real-password-constant-time-guard")
