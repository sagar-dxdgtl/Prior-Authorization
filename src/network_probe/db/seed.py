from __future__ import annotations

import uuid

from sqlalchemy import text

from network_probe.auth.passwords import hash_password

DEMO_SLUG = "demo"
DEMO_ADMIN_USERNAME = "admin"

def ensure_demo_tenant_admin(conn, initial_password: str = "ChangeMe-Admin-2026") -> None:
    """Idempotently create the demo tenant + admin user (must_change_password=True).
    Runs on an OWNER connection (RLS-exempt). Safe to call repeatedly. `conn` is a SQLAlchemy Connection."""
    tid = conn.execute(text("SELECT id FROM tenants WHERE slug=:s"), {"s": DEMO_SLUG}).scalar()
    if tid is None:
        tid = uuid.uuid4()
        conn.execute(text("INSERT INTO tenants (id, name, slug, created_at) VALUES (:id,'Demo Practice',:s, now())"),
                     {"id": str(tid), "s": DEMO_SLUG})
    exists = conn.execute(text("SELECT 1 FROM users WHERE lower(username)=lower(:u)"),
                          {"u": DEMO_ADMIN_USERNAME}).scalar()
    if not exists:
        conn.execute(text(
            "INSERT INTO users (id, tenant_id, username, password_hash, role, must_change_password, "
            "token_version, failed_logins, created_at) "
            "VALUES (:id,:tid,:u,:ph,'admin',true,0,0, now())"),
            {"id": str(uuid.uuid4()), "tid": str(tid), "u": DEMO_ADMIN_USERNAME,
             "ph": hash_password(initial_password)})
