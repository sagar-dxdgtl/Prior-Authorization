import pytest
from sqlalchemy import text


@pytest.mark.db
def test_ensure_demo_admin_idempotent():
    from network_probe.auth.passwords import verify_password
    from network_probe.db.base import owner_engine
    from network_probe.db.seed import ensure_demo_tenant_admin
    with owner_engine().begin() as c:
        ensure_demo_tenant_admin(c, "ChangeMe-Admin-2026")
        ensure_demo_tenant_admin(c, "ChangeMe-Admin-2026")   # repeat = no-op
        assert c.execute(text("SELECT count(*) FROM users WHERE lower(username)='admin'")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM tenants WHERE slug='demo'")).scalar() == 1
        row = c.execute(text("SELECT password_hash, must_change_password FROM users WHERE lower(username)='admin'")).mappings().first()
        assert verify_password("ChangeMe-Admin-2026", row["password_hash"]) and row["must_change_password"] is True
