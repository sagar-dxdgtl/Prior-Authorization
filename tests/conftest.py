import os
import uuid

import pytest

# Required settings — set BEFORE importing network_probe so the cached Settings picks them up.
# (pure tests run without a dev .env; db tests target preauth_test.)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:sagar@localhost:5432/preauth_test")
os.environ.setdefault("APP_DB_URL", "postgresql+psycopg://preauth_app:sagar@localhost:5432/preauth_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-bytes-long!!")
from cryptography.fernet import Fernet

os.environ.setdefault("FERNET_KEYS", Fernet.generate_key().decode())
os.environ.setdefault("MEMBER_ID_PEPPER", "t" * 40)

_DATA_TABLES = "tenants, users, payers, eligibility_checks, overrides, review_cases, review_notes"


def _owner():
    from network_probe.db.base import owner_engine

    return owner_engine()


@pytest.fixture(autouse=True)
def _clean_db(request):
    """Before each db-marked test, truncate data tables in preauth_test for isolation.
    No-op for pure tests so they never touch a database."""
    if request.node.get_closest_marker("db") is None:
        yield
        return
    from sqlalchemy import text

    with _owner().begin() as c:
        c.execute(text(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def demo_tenant():
    from sqlalchemy.orm import Session

    from network_probe.db.models import Tenant

    tid = uuid.uuid4()
    with Session(_owner()) as s:
        s.add(Tenant(id=tid, name="Demo", slug=f"demo-{tid.hex[:8]}"))
        s.commit()
    return tid


@pytest.fixture
def seed_admin(demo_tenant):
    from sqlalchemy.orm import Session

    from network_probe.auth.passwords import hash_password
    from network_probe.db.models import User

    with Session(_owner()) as s:
        s.add(
            User(
                tenant_id=demo_tenant,
                username="admin",
                password_hash=hash_password("Initial-pw-1234"),
                role="admin",
                must_change_password=True,
            )
        )
        s.commit()
    return {"tenant_id": demo_tenant, "username": "admin", "password": "Initial-pw-1234"}


def _make_user_header(tenant_id, role):
    import uuid as _uuid
    from sqlalchemy.orm import Session

    from network_probe.auth import jwt_tokens as jt
    from network_probe.auth.passwords import hash_password
    from network_probe.db.models import User

    uid = _uuid.uuid4()
    with Session(_owner()) as s:
        s.add(
            User(
                id=uid,
                tenant_id=tenant_id,
                username=f"{role}-{uid.hex[:6]}",
                password_hash=hash_password("x" * 12),
                role=role,
                must_change_password=False,
                token_version=0,
            )
        )
        s.commit()
    tok, _ = jt.issue_access(uid, tenant_id, role, 0)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def auth_header(demo_tenant):
    return _make_user_header(demo_tenant, "user")


@pytest.fixture
def admin_header(demo_tenant):
    return _make_user_header(demo_tenant, "admin")


@pytest.fixture
def reviewer_header(demo_tenant):
    return _make_user_header(demo_tenant, "reviewer")


@pytest.fixture
def seed_payers():
    from sqlalchemy.orm import Session

    from network_probe.db.models import Payer

    with Session(_owner()) as s:
        s.add(Payer(key="oscar", label="Oscar", stedi_payer_id="OSCAR", enrollment_status="supported"))
        s.commit()
