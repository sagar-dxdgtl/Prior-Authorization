# Stedi Eligibility + Benefits (Slice A) Implementation Plan — v2 (post-review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v2** folds in the adversarial-review findings (critical login/RLS fix, server-side `must_change_password`, real SSRF guard, member-keyed flow, atomic lockout, constant-time login, fail-fast crypto config, PHI-free `result_jsonb`, full audit coverage, real `met`) and replaces the external-frontend integration with **our own React+Vite+AntD app in `web/`**.

**Goal:** Turn the production Stedi 270/271 feed into a primary eligibility + benefits source (INN/OON cost-share, PCP/auth/referral, COB), landed in a multi-tenant Postgres datastore with row-level security, PHI encryption, PHI-redacted audit logging, our own OAuth2/JWT login, and our own React frontend.

**Architecture:** FastAPI backend. Auth (`/api/auth/*`) issues JWTs carrying `tenant_id`/`role`/`token_version`; `get_context` turns a Bearer token into a `RequestContext` and **enforces `must_change_password`**. Every protected request opens a tenant-scoped transaction setting `app.tenant_id` for Postgres RLS. The **pre-tenant** username lookup uses a `SECURITY DEFINER` SQL function so the `NOBYPASSRLS` app role never has to bypass RLS. `StediEligibilityClient` (an `EligibilitySource`) POSTs a 270; `parse_271_benefits` builds an `EligibilityResult`; the directory engine still owns provider-specific network status; a merge step corroborates. Secrets/crypto sit behind `SecretsProvider`/`CryptoProvider` protocols. The UI is our own React+Vite+AntD app in `web/`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic, Postgres 18, `psycopg[binary]`, `pyjwt`, `passlib[bcrypt]`, `cryptography` (Fernet), `pydantic-settings`, `boto3`, `httpx`, pytest; React 19 + Vite + Ant Design (TypeScript) for `web/`.

**Conventions (every task):**
- **TDD:** failing test → watch fail → minimal impl → watch pass → commit.
- **SOLID:** depend on the protocols (Tasks 2–3, 15, 18), not concretions.
- **Security is per-task.** **No PHI** in logs, `.cache/`, JWT claims, `result_jsonb`/`source_audit`, or error responses — ever.
- **Per-commit gate:** `pytest -m "not live and not db"` must stay green (existing 41 tests included). DB tests: `pytest -m db` (needs local Postgres). Live: `pytest -m live` (needs prod Stedi key).
- DB URLs: owner `postgresql+psycopg://postgres:sagar@localhost:5432/preauth[_test]`; app role `postgresql+psycopg://preauth_app:sagar@localhost:5432/preauth[_test]`.

---

## File structure (created/modified in this slice)

```
network_probe/
  config.py            Settings + fail-fast validators (jwt/fernet/pepper)
  secrets_provider.py  SecretsProvider protocol + EnvSecrets + AwsSecrets
  crypto.py            CryptoProvider + FernetCrypto + peppered member-id HMAC
  context.py           RequestContext(tenant_id, actor_id, role)
  db/{base,session,models,repo}.py   owner+app engines, RLS session, ORM, repos
  auth/{passwords,jwt_tokens,deps,routes}.py   bcrypt, JWT, get_context, OAuth2 routes
  ratelimit.py         per-tenant quota/rate-limit headers middleware
  benefits.py          Network/Category/Level/BenefitLine/EligibilityResult
  stedi/{client,parse_271}.py   270 POST (no-PHI cache) + 271→benefits parser
  payers/{catalogue,roster_seed}.py   PayerCatalogue + roster
  netutil.py           SSRF guard (is_global, no-redirect, threaded)
  validation.py        NPI Luhn, DOB
  eligibility.py       check_eligibility() Stedi primary + directory merge
  audit.py             write_audit(action, ...) PHI-redacted
  api.py               (modify) mount auth, /api/eligibility, lock down /api/check*, CORS, errors
alembic/, alembic.ini  migrations (schema+RLS+SECURITY DEFINER, payer seed, admin seed)
scripts/{resolve_payer_ids,migrate_overrides}.py
tests/conftest.py      test env + DB fixtures (seed tenant/admin/payers, auth_header)
tests/test_*.py        one module per component
web/                   our React+Vite+AntD app (login + benefits matrix)
.env.example, .gitignore, requirements.txt, pytest.ini   (modify)
```

---

## Task 0: Bootstrap — deps, gitignore, env (real keys), DBs, app role, markers

**Files:** Modify `requirements.txt`, `.gitignore`, `pytest.ini`; Create `.env.example`, `network_probe/{db,auth,stedi,payers}/__init__.py`

- [ ] **Step 1:** Append to `requirements.txt`:
```
sqlalchemy==2.0.36
alembic==1.14.0
psycopg[binary]==3.2.3
cryptography==44.0.0
pydantic-settings==2.7.0
pyjwt==2.10.1
passlib[bcrypt]==1.7.4
boto3==1.35.80
```
- [ ] **Step 2:** `pip install -r requirements.txt`; verify `python -c "import sqlalchemy,alembic,psycopg,cryptography,jwt,passlib,boto3,pydantic_settings;print('ok')"` → `ok`.
- [ ] **Step 3:** Append to `.gitignore`:
```
.env
.env.*
!.env.example
.cache/
.overrides/
__MACOSX/
__pycache__/
*.pyc
web/node_modules/
web/dist/
```
- [ ] **Step 4:** Register markers + clean config in `pytest.ini`:
```ini
[pytest]
markers =
    live: hits real external services (needs prod Stedi key)
    db: needs a local Postgres (preauth_test)
filterwarnings =
    ignore::DeprecationWarning
```
- [ ] **Step 5:** Generate a real Fernet key and write `.env.example` with it inlined (it is example-only, not a prod secret):
```
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
```
`.env.example`:
```dotenv
APP_ENV=dev
DATABASE_URL=postgresql+psycopg://postgres:sagar@localhost:5432/preauth        # owner (migrations/seed)
APP_DB_URL=postgresql+psycopg://preauth_app:sagar@localhost:5432/preauth        # NOBYPASSRLS app role
JWT_SECRET=dev-only-change-me-this-is-32-bytes-minimum
JWT_ACCESS_TTL=1800
JWT_REFRESH_TTL=1209600
FERNET_KEYS=<paste the generated key here>
MEMBER_ID_PEPPER=dev-only-pepper-change-me-to-32plus-bytes
STEDI_API_KEY=
STEDI_ELIGIBILITY_URL=https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3
CORS_ORIGINS=http://localhost:5173
AWS_DEFAULT_REGION=us-east-1
```
- [ ] **Step 6:** Create the DBs + non-superuser app role (psql as `postgres`):
```sql
CREATE DATABASE preauth;  CREATE DATABASE preauth_test;
CREATE ROLE preauth_app LOGIN PASSWORD 'sagar' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE preauth, preauth_test TO preauth_app;
```
Verify `\du preauth_app` shows no Superuser / Bypass RLS attributes.
- [ ] **Step 7:** Create the four `__init__.py` files.
- [ ] **Step 8: Commit** `chore: slice-A deps, secret-safe gitignore, env template, DBs, markers`.

Threat note: app connects as `preauth_app` (NOBYPASSRLS) so RLS is genuinely enforced; only Alembic/seed use the owner URL.

---

## Task 1: Config with fail-fast validators (`config.py`)

**Files:** Create `network_probe/config.py`, `tests/test_config.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_config.py
import pytest
from network_probe.config import Settings

BASE = dict(DATABASE_URL="postgresql+psycopg://u:p@localhost/db", JWT_SECRET="x"*32,
            FERNET_KEYS="", MEMBER_ID_PEPPER="p"*32, APP_ENV="dev")

def _mk(env, monkeypatch):
    for k, v in env.items(): monkeypatch.setenv(k, v)
    return Settings()

def test_dev_allows_empty_fernet(monkeypatch):
    s = _mk(BASE, monkeypatch); assert s.app_env == "dev"

def test_prod_requires_valid_fernet(monkeypatch):
    from cryptography.fernet import Fernet
    env = {**BASE, "APP_ENV": "prod", "FERNET_KEYS": ""}
    with pytest.raises(ValueError):
        _mk(env, monkeypatch)
    env2 = {**BASE, "APP_ENV": "prod", "FERNET_KEYS": Fernet.generate_key().decode(),
            "MEMBER_ID_PEPPER": "s"*32}
    _mk(env2, monkeypatch)   # no raise

def test_prod_rejects_default_pepper(monkeypatch):
    from cryptography.fernet import Fernet
    env = {**BASE, "APP_ENV": "prod", "FERNET_KEYS": Fernet.generate_key().decode(),
           "MEMBER_ID_PEPPER": "dev-only-pepper-change-me-to-32plus-bytes"}
    with pytest.raises(ValueError):
        _mk(env, monkeypatch)
```
- [ ] **Step 2: Run → FAIL.  Step 3: Implement**
```python
# network_probe/config.py
from __future__ import annotations
from functools import lru_cache
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet

_DEV = {"dev", "test", "local"}
_DEFAULT_PEPPERS = {"dev-pepper", "dev-only-pepper-change-me-to-32plus-bytes"}

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_env: str = "dev"
    database_url: str
    app_db_url: str | None = None
    jwt_secret: str
    jwt_access_ttl: int = 1800
    jwt_refresh_ttl: int = 1209600
    fernet_keys: str = ""
    member_id_pepper: str = "dev-pepper"
    stedi_api_key: str | None = None
    stedi_eligibility_url: str = "https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3"
    cors_origins: list[str] = ["http://localhost:5173"]
    aws_default_region: str = "us-east-1"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split(cls, v): return [x.strip() for x in v.split(",")] if isinstance(v, str) else v

    @field_validator("jwt_secret")
    @classmethod
    def _strong(cls, v): 
        if len(v) < 32: raise ValueError("JWT_SECRET must be >= 32 chars")
        return v

    @property
    def fernet_key_list(self) -> list[str]:
        return [k for k in self.fernet_keys.split(",") if k]

    @property
    def effective_app_db_url(self) -> str:
        return self.app_db_url or self.database_url

    @model_validator(mode="after")
    def _phi_crypto_required_outside_dev(self):
        if self.app_env in _DEV:
            return self
        keys = self.fernet_key_list
        if not keys: raise ValueError("FERNET_KEYS required outside dev")
        for k in keys: Fernet(k.encode())   # raises if not a valid 32-byte urlsafe key
        if len(self.member_id_pepper) < 32 or self.member_id_pepper in _DEFAULT_PEPPERS:
            raise ValueError("MEMBER_ID_PEPPER must be strong and non-default outside dev")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
```
- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: fail-fast config validation for PHI-crypto secrets`.

---

## Task 2: Secrets provider (`secrets_provider.py`)
*(unchanged from v1 — included for completeness)*

**Files:** Create `network_probe/secrets_provider.py`, `tests/test_secrets.py`

- [ ] **Step 1: Test** env wins, missing → None, AWS only when creds present. **Step 2: FAIL.**
- [ ] **Step 3: Implement** `SecretsProvider` Protocol + `EnvSecrets` + `AwsSecrets` (boto3 Secrets Manager, prefix `preauth/`) + `get_secret(name)` = `os.environ.get(name) or _provider().get_secret(name)`; `_provider()` returns `AwsSecrets()` only when `AWS_ACCESS_KEY_ID` set, else `EnvSecrets()`. **Step 4: PASS. Step 5: Commit** `feat: SecretsProvider (env + AWS seam)`.

---

## Task 3: Crypto (`crypto.py`)
*(unchanged from v1)*

**Files:** Create `network_probe/crypto.py`, `tests/test_crypto.py`

- [ ] Tests: Fernet roundtrip, key rotation via `MultiFernet`, `hash_member_id` case-normalized + pepper-dependent + 64 hex chars. Implement `CryptoProvider` Protocol, `FernetCrypto(keys)`, `hash_member_id(member_id, pepper)` = HMAC-SHA256(pepper, normalized). **Commit** `feat: Fernet PHI crypto + peppered member-id HMAC`.

```python
def hash_member_id(member_id: str, pepper: str) -> str:
    import hashlib, hmac, re
    norm = re.sub(r"\s+", "", (member_id or "")).upper()
    return hmac.new(pepper.encode(), norm.encode(), hashlib.sha256).hexdigest()
```

---

## Task 4: DB engines + ORM models (`context.py`, `db/base.py`, `db/models.py`)

**Files:** Create `network_probe/context.py`, `network_probe/db/base.py`, `network_probe/db/models.py`

- [ ] **Step 1: `context.py`**
```python
# network_probe/context.py
from dataclasses import dataclass
import uuid
@dataclass(frozen=True)
class RequestContext:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    role: str
```
- [ ] **Step 2: `db/base.py` — TWO engines** (owner for migrations/seed; app for RLS-bound runtime)
```python
# network_probe/db/base.py
from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from ..config import get_settings

class Base(DeclarativeBase): pass

_app_engine = _owner_engine = None
def app_engine():
    global _app_engine
    if _app_engine is None:
        _app_engine = create_engine(get_settings().effective_app_db_url, pool_pre_ping=True, future=True)
    return _app_engine
def owner_engine():
    global _owner_engine
    if _owner_engine is None:
        _owner_engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _owner_engine

SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False, future=True)
```
- [ ] **Step 3: `db/models.py`** — UUID PKs (anti-IDOR), global-unique username, `action`+`name_enc` on checks.

```python
# network_probe/db/models.py
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, ForeignKey, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

def _uuid(): return uuid.uuid4()
def _now(): return datetime.now(timezone.utc)

class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    username: Mapped[str] = mapped_column(String(150))           # globally unique via lower() index (migration)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="user")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class Payer(Base):
    __tablename__ = "payers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    label: Mapped[str] = mapped_column(String(200))
    benefit_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    stedi_payer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enrollment_status: Mapped[str] = mapped_column(String(30), default="unknown")
    network_indicator_supported: Mapped[bool] = mapped_column(Boolean, default=False)

class EligibilityCheck(Base):
    __tablename__ = "eligibility_checks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20), index=True)   # eligibility|network|override|report_ingest
    payer_key: Mapped[str] = mapped_column(String(120))
    member_id_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    member_id_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    dob_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    name_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    result_jsonb: Mapped[dict] = mapped_column(JSONB, default=dict)   # provably PHI-free
    source_audit: Mapped[dict] = mapped_column(JSONB, default=dict)   # provably PHI-free
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

class OverrideRow(Base):
    __tablename__ = "overrides"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    payer: Mapped[str] = mapped_column(String(120)); npi: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20)); verified_by: Mapped[str] = mapped_column(String(120))
    verified_at: Mapped[str] = mapped_column(String(40))
    network: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str] = mapped_column(String, default="")
```
- [ ] **Step 4: Commit** `feat: ORM models (UUID PKs, action+name_enc, two engines)`.

---

## Task 5: TOCTOU-safe RLS session (`db/session.py`)

**Files:** Create `network_probe/db/session.py`, `tests/test_db_session.py` (`db`-marked; uses `conftest` from Task 7)

- [ ] **Step 1: Implement**
```python
# network_probe/db/session.py
from __future__ import annotations
import uuid
from contextlib import contextmanager
from sqlalchemy import text
from .base import SessionLocal, app_engine

@contextmanager
def tenant_session(tenant_id: uuid.UUID):
    """One transaction; bind RLS tenant id LOCAL to it via a BOUND PARAM (anti-SQLi),
    scoped so a pooled connection can't carry tenant A's id into tenant B's request (TOCTOU)."""
    session = SessionLocal(bind=app_engine())
    try:
        session.begin()
        session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
        yield session
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
```
- [ ] **Step 2: Test** (after Task 7 conftest exists): `set_config` is transaction-local and doesn't leak to the next pooled session. **Commit** `feat: TOCTOU-safe RLS tenant session`.

---

## Task 6: Alembic schema + RLS + SECURITY DEFINER auth lookup

**Files:** Create `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_init.py`

- [ ] **Step 1:** `alembic init alembic`; point `env.py` at `Base.metadata` and the **owner** URL:
```python
from network_probe.db.base import Base
from network_probe.db import models  # noqa: F401
from network_probe.config import get_settings
target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```
- [ ] **Step 2:** `alembic revision --autogenerate -m "init schema"`; review the generated tables.
- [ ] **Step 3:** Append to `upgrade()` (hand-written): global-unique username, RLS, FORCE RLS, policies, grants, and the SECURITY DEFINER login lookup:
```python
def _post(op):
    op.create_index("uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True)
    for t in ("users", "payers", "eligibility_checks", "overrides"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    for t in ("users", "eligibility_checks", "overrides"):
        op.execute(f"CREATE POLICY {t}_isolation ON {t} USING "
                   f"(tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute("CREATE POLICY payers_isolation ON payers USING "
               "(tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO preauth_app")
    # Pre-tenant login lookup, RLS-exempt by SECURITY DEFINER (owner-run), minimal columns only:
    op.execute("""
        CREATE OR REPLACE FUNCTION auth_lookup_user(p_username text)
        RETURNS TABLE(id uuid, tenant_id uuid, password_hash text, role text,
                      token_version int, must_change_password boolean,
                      failed_logins int, locked_until timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            SELECT id, tenant_id, password_hash, role, token_version,
                   must_change_password, failed_logins, locked_until
            FROM users WHERE lower(username) = lower(p_username) LIMIT 1;
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION auth_lookup_user(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_lookup_user(text) TO preauth_app")
```
(`import sqlalchemy as sa` at top.) Call `_post(op)` at the end of `upgrade()`; drop the function + policies in `downgrade()`.
- [ ] **Step 4:** `alembic upgrade head` against `preauth` and `preauth_test`.
- [ ] **Step 5: Commit** `feat: schema + FORCE RLS + SECURITY DEFINER auth lookup`.

Threat note: the app role never bypasses RLS; the only RLS-exempt surface is one function that returns a single user's auth fields by username — it cannot be used for arbitrary cross-tenant reads.

---

## Task 7: Test harness — `tests/conftest.py`

**Files:** Create `tests/conftest.py`

- [ ] **Step 1: Implement** (sets required env BEFORE app import; provides DB fixtures the `db` tests need)
```python
# tests/conftest.py
import os, uuid
import pytest
# Required settings for the PURE suite (no .env on CI):
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:sagar@localhost:5432/preauth_test")
os.environ.setdefault("APP_DB_URL", "postgresql+psycopg://preauth_app:sagar@localhost:5432/preauth_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-bytes-long!!")
from cryptography.fernet import Fernet
os.environ.setdefault("FERNET_KEYS", Fernet.generate_key().decode())
os.environ.setdefault("MEMBER_ID_PEPPER", "t"*40)

def _owner():
    from network_probe.db.base import owner_engine
    return owner_engine()

@pytest.fixture
def demo_tenant():
    from network_probe.db.models import Tenant
    from sqlalchemy.orm import Session
    tid = uuid.uuid4()
    with Session(_owner()) as s:
        s.add(Tenant(id=tid, name="Demo", slug=f"demo-{tid.hex[:8]}")); s.commit()
    return tid

@pytest.fixture
def seed_admin(demo_tenant):
    from network_probe.db.models import User
    from network_probe.auth.passwords import hash_password
    from sqlalchemy.orm import Session
    with Session(_owner()) as s:
        s.add(User(tenant_id=demo_tenant, username="admin", password_hash=hash_password("Initial-pw-1234"),
                   role="admin", must_change_password=True)); s.commit()
    return {"tenant_id": demo_tenant, "username": "admin", "password": "Initial-pw-1234"}

@pytest.fixture
def auth_header(demo_tenant):
    """A ready-to-use Bearer header for a non-pw-change user in demo_tenant."""
    from network_probe.db.models import User
    from network_probe.auth.passwords import hash_password
    from network_probe.auth import jwt_tokens as jt
    from sqlalchemy.orm import Session
    uid = uuid.uuid4()
    with Session(_owner()) as s:
        s.add(User(id=uid, tenant_id=demo_tenant, username=f"u-{uid.hex[:6]}",
                   password_hash=hash_password("x"*12), role="user",
                   must_change_password=False, token_version=0)); s.commit()
    tok, _ = jt.issue_access(uid, demo_tenant, "user", 0)
    return {"Authorization": f"Bearer {tok}"}

@pytest.fixture
def seed_payers():
    from network_probe.db.models import Payer
    from sqlalchemy.orm import Session
    with Session(_owner()) as s:
        if not s.query(Payer).filter_by(key="oscar").first():
            s.add(Payer(key="oscar", label="Oscar", stedi_payer_id="OSCAR",
                        enrollment_status="supported")); s.commit()
```
- [ ] **Step 2: Commit** `test: conftest with env + DB fixtures`.

Note: fixtures seed **parent rows as the owner** so FK constraints hold; tenant isolation is still tested via `tenant_session` in app code (Task 9).

---

## Task 8: Repositories (`db/repo.py`) — *unchanged from v1*

- [ ] `OverrideRepo`/`EligibilityCheckRepo` take `(session, tenant_id)`; `add(**kw)` hard-binds `tenant_id=self.tid` (anti mass-assignment); reads rely on RLS for the tenant filter. Test + commit `feat: tenant-scoped repositories`.

---

## Task 9: RLS isolation test

**Files:** `tests/test_rls_isolation.py`

- [ ] **Test** (`db`): seed tenants A and B via owner; write an `OverrideRow` under A through `tenant_session(A)`; assert `tenant_session(B)` sees zero rows. Commit `test: RLS cross-tenant isolation holds`.
```python
@pytest.mark.db
def test_tenant_cannot_read_other_tenants_rows():
    import uuid
    from sqlalchemy.orm import Session
    from network_probe.db.base import owner_engine
    from network_probe.db.models import Tenant, OverrideRow
    from network_probe.db.session import tenant_session
    a, b = uuid.uuid4(), uuid.uuid4()
    with Session(owner_engine()) as s:
        s.add_all([Tenant(id=a, name="A", slug=f"a-{a.hex[:6]}"),
                   Tenant(id=b, name="B", slug=f"b-{b.hex[:6]}")]); s.commit()
    with tenant_session(a) as s:
        s.add(OverrideRow(tenant_id=a, payer="oscar", npi="1", status="IN_NETWORK",
                          verified_by="t", verified_at="2026-01-01"))
    with tenant_session(b) as s:
        assert s.query(OverrideRow).all() == []
```

---

## Task 10: Passwords (`auth/passwords.py`) — *unchanged from v1* + dummy hash constant

- [ ] `hash_password`/`verify_password` (bcrypt rounds=12)/`check_policy` (≥12). **Add** a module-level pre-computed `DUMMY_HASH = hash_password("not-a-real-password")` for constant-time login. Test + commit `feat: bcrypt passwords + dummy hash for constant-time login`.

---

## Task 11: JWT (`auth/jwt_tokens.py`) — *unchanged from v1*

- [ ] `issue_access`/`issue_refresh`/`decode_token(token, expected_typ)` with `algorithms=["HS256"]`, `typ` separation, `tv` claim, `require=[exp,iat,sub]`. Tests (roundtrip, refresh≠access, tamper). Commit `feat: alg-pinned JWT with typ separation`.

---

## Task 12: Auth context + must_change_password enforcement (`auth/deps.py`)

**Files:** Create `network_probe/auth/deps.py`, `tests/test_auth_deps.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_auth_deps.py
import uuid, pytest
from fastapi import HTTPException
from network_probe.auth import jwt_tokens as jt
from network_probe.auth.deps import context_from_token

class FakeUser:
    def __init__(self, tv=0, mcp=False):
        self.id=uuid.uuid4(); self.tenant_id=uuid.uuid4(); self.role="user"
        self.token_version=tv; self.must_change_password=mcp

def test_stale_tv_rejected(monkeypatch):
    u=FakeUser(tv=5); tok,_=jt.issue_access(u.id,u.tenant_id,"user",4)
    monkeypatch.setattr("network_probe.auth.deps._load_user", lambda i,t: FakeUser(tv=5))
    with pytest.raises(HTTPException) as e: context_from_token(tok)
    assert e.value.status_code==401

def test_must_change_password_blocks_data_routes(monkeypatch):
    u=FakeUser(tv=0, mcp=True); tok,_=jt.issue_access(u.id,u.tenant_id,"user",0)
    monkeypatch.setattr("network_probe.auth.deps._load_user", lambda i,t: u)
    with pytest.raises(HTTPException) as e: context_from_token(tok, allow_password_change=False)
    assert e.value.status_code==403
    # but the change-password path is allowed:
    assert context_from_token(tok, allow_password_change=True).role=="user"
```
- [ ] **Step 2: FAIL.  Step 3: Implement**
```python
# network_probe/auth/deps.py
from __future__ import annotations
import uuid
from fastapi import Depends, HTTPException, Header
from .jwt_tokens import decode_token, TokenError
from ..context import RequestContext
from ..db.session import tenant_session
from ..db.models import User

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
```
- [ ] **Step 4: PASS.  Step 5: Commit** `feat: get_context enforces must_change_password (403)`.

---

## Task 13: Auth routes (`auth/routes.py`) — RLS-safe login, atomic lockout, constant-time

**Files:** Create `network_probe/auth/routes.py`, `tests/test_auth_routes.py`

- [ ] **Step 1: Failing test**
```python
# tests/test_auth_routes.py
import pytest
from fastapi.testclient import TestClient
from network_probe.api import app

@pytest.mark.db
def test_login_firstlogin_then_change(seed_admin):
    c = TestClient(app)
    r = c.post("/api/auth/login", data={"grant_type":"password","username":"admin","password":"Initial-pw-1234"})
    assert r.status_code==200 and r.json()["must_change_password"] is True
    acc = r.json()["tokens"]["access"]
    # first-login token cannot hit data routes:
    assert c.get("/api/eligibility/ping", headers={"Authorization":f"Bearer {acc}"}).status_code==403
    r2 = c.post("/api/auth/change-password/", headers={"Authorization":f"Bearer {acc}"},
                json={"current_password":"Initial-pw-1234","new_password":"Brand-new-pw-456","confirm_password":"Brand-new-pw-456"})
    assert r2.status_code==200 and r2.json()["success"] is True
    # re-login now returns full tokens:
    r3 = c.post("/api/auth/login", data={"grant_type":"password","username":"admin","password":"Brand-new-pw-456"})
    assert r3.status_code==200 and r3.json()["access_token"]

@pytest.mark.db
def test_bad_password_generic_and_lockout(seed_admin):
    c = TestClient(app)
    for _ in range(5):
        r = c.post("/api/auth/login", data={"grant_type":"password","username":"admin","password":"nope"})
        assert r.status_code==401 and "username" not in r.json()["message"].lower()
    # 6th attempt is locked even with the RIGHT password:
    r = c.post("/api/auth/login", data={"grant_type":"password","username":"admin","password":"Initial-pw-1234"})
    assert r.status_code==429
```
- [ ] **Step 2: FAIL.  Step 3: Implement**
```python
# network_probe/auth/routes.py
from __future__ import annotations
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import text
from .base_compat import app_engine_exec  # tiny helper below (or inline)
from ..db.base import app_engine
from ..db.session import tenant_session
from ..db.models import User
from .passwords import verify_password, hash_password, check_policy, DUMMY_HASH
from . import jwt_tokens as jt
from .deps import get_context_pwchange
from ..context import RequestContext

router = APIRouter(prefix="/api/auth", tags=["auth"])
LOCK_THRESHOLD, LOCK_MINUTES = 5, 15

def _lookup(username: str):
    """RLS-exempt minimal lookup via the SECURITY DEFINER function (app role may EXECUTE it)."""
    with app_engine().connect() as conn:
        row = conn.execute(text("SELECT * FROM auth_lookup_user(:u)"), {"u": username}).mappings().first()
    return row

def _user_payload(row) -> dict:
    return {"id": str(row["id"]), "username": "", "name": "", "role": row["role"],
            "tenant_id": str(row["tenant_id"])}

@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    now = datetime.now(timezone.utc)
    row = _lookup(form.username)
    if row and row["locked_until"] and row["locked_until"] > now:
        raise HTTPException(status_code=429, detail={"message": "account temporarily locked"})
    ok = verify_password(form.password, row["password_hash"]) if row else verify_password(form.password, DUMMY_HASH)
    if not row or not ok:
        if row:  # atomic failure increment + lock, inside the user's tenant (RLS-permitted)
            with tenant_session(row["tenant_id"]) as s:
                s.execute(text(
                    "UPDATE users SET failed_logins = failed_logins + 1, "
                    "locked_until = CASE WHEN failed_logins + 1 >= :thr THEN :until ELSE locked_until END, "
                    "failed_logins = CASE WHEN failed_logins + 1 >= :thr THEN 0 ELSE failed_logins + 1 END "
                    "WHERE id = :id"),
                    {"thr": LOCK_THRESHOLD, "until": now + timedelta(minutes=LOCK_MINUTES), "id": row["id"]})
        raise HTTPException(status_code=401, detail={"message": "invalid credentials"})
    with tenant_session(row["tenant_id"]) as s:   # reset counters atomically
        s.execute(text("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = :id"), {"id": row["id"]})
    access, expires_in = jt.issue_access(row["id"], row["tenant_id"], row["role"], row["token_version"])
    refresh = jt.issue_refresh(row["id"], row["tenant_id"], row["role"], row["token_version"])
    if row["must_change_password"]:
        return {"must_change_password": True, "tokens": {"access": access}, "user": _user_payload(row)}
    return {"access_token": access, "expires_in": expires_in, "refresh_token": refresh, "user": _user_payload(row)}

class RefreshReq(BaseModel): refresh_token: str

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
    return {"access_token": access, "expires_in": expires_in}

class ChangePwReq(BaseModel):
    current_password: str; new_password: str; confirm_password: str

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
        u.password_hash = hash_password(req.new_password); u.must_change_password = False; u.token_version += 1
        s.commit()
    return {"success": True}
```
(Drop the `base_compat` import — call `app_engine()` directly as shown.)
- [ ] **Step 4: PASS.  Step 5: Commit** `feat: RLS-safe OAuth2 login (SECURITY DEFINER lookup, atomic lockout, constant-time)`.

Threat notes: login never bypasses RLS broadly; unknown user still pays a bcrypt verify (no timing oracle); lockout is one atomic UPDATE (no read-modify-write race); password change bumps `token_version` (revoke-all).

---

## Task 14: Rate-limit / quota headers middleware (`ratelimit.py`)

**Files:** Create `network_probe/ratelimit.py`, `tests/test_ratelimit.py`

- [ ] **Step 1: Test** that a response to an authed request carries `x-ratelimit-limit`, `x-ratelimit-remaining`, `x-quota-monthly-*`, `x-quota-daily-*`.
- [ ] **Step 2: Implement** a Starlette `BaseHTTPMiddleware` keeping a simple per-tenant in-memory counter (decode the Bearer token if present to get `tid`; never throw) and setting the headers the frontend reads. Real enforcement is Slice B; Slice A just surfaces honest counters.
- [ ] **Step 3: Commit** `feat: per-tenant quota/rate-limit response headers`.

---

## Task 15: Benefits model (`benefits.py`) — *unchanged from v1*

- [ ] `Network`/`BenefitCategory`/`CoverageLevel` enums, `BenefitLine`, `EligibilityResult` with `to_dict()` (Decimals → str; no PHI). Test (incl. `to_dict()` has no PHI). Commit `feat: benefits domain model`.

---

## Task 16: 271 parser (`stedi/parse_271.py`) — real `met`, redacted COB/errors

**Files:** Create `network_probe/stedi/parse_271.py`, `tests/test_parse_271.py`, fixture `tests/fixtures/stedi-271-inn-oon.json`

- [ ] **Step 1: Fixture** — like v1 plus a paired deductible (calendar-year total + remaining) and a COB block carrying a member id, to prove redaction:
```json
{"benefitsInformation": [
  {"code":"1","name":"Active Coverage","serviceTypeCodes":["30"]},
  {"code":"B","serviceTypeCodes":["98"],"coverageLevelCode":"IND","inPlanNetworkIndicatorCode":"Y","benefitAmount":"30","timeQualifierCode":"27"},
  {"code":"B","serviceTypeCodes":["98"],"coverageLevelCode":"IND","inPlanNetworkIndicatorCode":"N","benefitAmount":"60","timeQualifierCode":"27"},
  {"code":"C","serviceTypeCodes":["30"],"coverageLevelCode":"FAM","inPlanNetworkIndicatorCode":"N","benefitAmount":"4000","timeQualifierCode":"23"},
  {"code":"C","serviceTypeCodes":["30"],"coverageLevelCode":"FAM","inPlanNetworkIndicatorCode":"N","benefitAmount":"1500","timeQualifierCode":"29"},
  {"code":"A","serviceTypeCodes":["30"],"coverageLevelCode":"IND","inPlanNetworkIndicatorCode":"N","benefitPercent":"0.4"},
  {"code":"F","serviceTypeCodes":["30"],"additionalInformation":[{"description":"Prior authorization required"}]}
],
"coordinationOfBenefits":{"primaryPayer":"Aetna","sequence":"primary","subscriberMemberId":"SHOULD-NOT-LEAK"}}
```
- [ ] **Step 2: Failing test** — INN/OON copays; family OON deductible `met=2500`,`remaining=1500` (total 4000 − remaining 1500); prior_auth True; **COB redacted** (no `SHOULD-NOT-LEAK`); AAA→UNKNOWN with **codes-only** source_audit.
```python
def test_met_paired_and_cob_redacted():
    r = parse_271_benefits(DATA)
    ded = next(b for b in r.benefits if b.category.value=="deductible" and b.time_period=="calendar year")
    assert str(ded.met)=="2500" and str(ded.remaining)=="1500"
    assert "SHOULD-NOT-LEAK" not in str(r.to_dict())
    assert r.cob and "primaryPayer" in r.cob and "subscriberMemberId" not in r.cob

def test_errors_redacted_to_codes():
    r = parse_271_benefits({"errors":[{"code":"42","description":"member id ABC123 not found"}]})
    from network_probe.models import NetworkStatus
    assert r.network_status==NetworkStatus.UNKNOWN
    assert "ABC123" not in str(r.source_audit)
```
- [ ] **Step 3: Implement** — like v1, plus:
  - `_COB_ALLOW = {"primaryPayer","secondaryPayer","planSponsor","ipa","sequence"}`; `cob = {k:v for k,v in (data.get("coordinationOfBenefits") or {}).items() if k in _COB_ALLOW} or None`.
  - On `errors`: `source_audit = {"error_codes": [e.get("code") for e in data["errors"]], "note": "payer could not respond"}` (drop free-text/descriptions).
  - After building lines, a `_pair_met()` pass: for each `(category in {deductible,oop_max}, network, level)`, if there is a `time_period=="calendar year"` total line and a `time_period=="remaining"` line, set the total line's `remaining = remaining_line.amount` and `met = total - remaining` (when both present); drop the standalone remaining line (or keep it flagged). Leave `met=None` when unpaired.
- [ ] **Step 4: PASS.  Step 5: Commit** `feat: 271 parser with real met-pairing + PHI-redacted COB/errors`.

---

## Task 17: SSRF guard + validation (`netutil.py`, `validation.py`)

**Files:** Create `network_probe/netutil.py`, `network_probe/validation.py`, `tests/test_netutil.py`, `tests/test_validation.py`

- [ ] **Step 1: Tests** — block `169.254.169.254`, `127.0.0.1`, `10.x`, `192.168.x`, `0.0.0.0`, `::1`, `file://`; allow public https; `valid_npi` Luhn; `normalize_dob`.
- [ ] **Step 2: Implement** `assert_safe_url` using `not ip.is_global` over **all** resolved addresses; return the URL. Provide `guarded_get_json(client_factory, url, headers)` that callers use for attacker-influenced URLs: it builds a client with `follow_redirects=False`, and if a 3xx with `Location` is returned, re-runs `assert_safe_url(location)` before following manually (bounded to 3 hops). Document that connect-time IP-pinning (full DNS-rebind defense) is a Slice-B hardening.
```python
# network_probe/netutil.py
from __future__ import annotations
import ipaddress, socket
from urllib.parse import urlparse

def assert_safe_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs allowed")
    if not p.hostname:
        raise ValueError("no host")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except Exception:
        raise ValueError("host does not resolve")
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.ipv4_mapped: ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("URL resolves to a non-public address")
    return url
```
- [ ] **Step 3: Commit** `feat: is_global SSRF guard (+ redirect re-validation) and NPI/DOB validation`.

---

## Task 18: Stedi client (`stedi/client.py`) — *as v1, no-PHI cache*

- [ ] `EligibilitySource` Protocol; `StediEligibilityClient(api_key, client, payer_id, service_type_codes)` forces `CachedClient(cache_dir=None)` (no PHI to disk); `check(q)` → `parse_271_benefits` or honest UNKNOWN when key/payer missing or call fails. Tests (parses; no PHI in URL; no key → UNKNOWN). Commit `feat: StediEligibilityClient (no-PHI cache)`.

---

## Task 19: Payer catalogue + roster seed

**Files:** Create `network_probe/payers/catalogue.py`, `network_probe/payers/roster_seed.py`, `alembic/versions/0002_seed_payers.py`, `tests/test_catalogue.py`

- [ ] **Step 1:** `roster_seed.ROSTER` — transcribe **every row** of the AZ/CO/FL/NY roster from the spec as `(label, benefit_type, state, stedi_payer_id|None, enrollment_status)`; known ids: Oscar `OSCAR`, Devoted `DEVOT`, Humana `61101`, Cigna `62308`, UHC `87726` = `supported`; unknown = `None`/`needs_payer_id`.
- [ ] **Step 2:** `DbPayerCatalogue.resolve(payer_key)` (PayerCatalogue Protocol) — slug-match `key`/`label` over global rows.
- [ ] **Step 3:** Migration `0002` inserts each ROSTER row as a global payer (`tenant_id=NULL`, `key=slug(label)+"-"+state`) with `INSERT … ON CONFLICT DO NOTHING` (idempotent, TOCTOU-safe).
- [ ] **Step 4: Tests** (`db`, with `seed_payers`/migration): resolve known → `OSCAR`; unknown → None. **Commit** `feat: payer catalogue + AZ/CO/FL/NY roster seed`.

---

## Task 20: Demo tenant/admin seed migration + payer-id resolver

**Files:** Create `alembic/versions/0003_seed_admin.py`, `scripts/resolve_payer_ids.py`

- [ ] **Step 1:** Migration `0003` (runs as owner, RLS-exempt) inserts a demo `Tenant` + admin `User` (bcrypt-hashed initial password, `must_change_password=True`) via `INSERT … ON CONFLICT DO NOTHING`. This is what Task 24/the running app log in as (not test fixtures).
- [ ] **Step 2:** `scripts/resolve_payer_ids.py` — gated by `STEDI_API_KEY`; calls Stedi's payer-search API, fills `stedi_payer_id`/`network_indicator_supported` on `needs_payer_id` rows, prints a report. No-ops without the key.
- [ ] **Step 3: Commit** `feat: demo seed migration + Stedi payer-id resolver script`.

---

## Task 21: Eligibility engine (`eligibility.py`) — thread base_url, merge

**Files:** Create `network_probe/eligibility.py`, `tests/test_eligibility.py`

- [ ] **Implement** `check_eligibility(q, base_url=None, catalogue=None)`: resolve payer → `StediEligibilityClient(payer_id=...)` → result; then `check_network(q, **({"base_url": base_url} if base_url else {}))` (the **validated** base_url is threaded here), set `network_verdict`/`corroboration`, and merge (directory IN + Stedi OON → REVIEW; Stedi UNKNOWN + directory known → adopt directory). Wrap directory call in try/except (payer may have no adapter). Test the merge matrix with a fake catalogue + monkeypatched `check_network`. Commit `feat: check_eligibility (Stedi primary + directory merge, base_url threaded)`.

---

## Task 22: Audit writer (`audit.py`) — action-tagged, PHI-redacted

**Files:** Create `network_probe/audit.py`, `tests/test_audit.py`

- [ ] **Implement** `write_audit(ctx, action, q, result, request_id)`:
  - `mid_hash = hash_member_id(q.member_id, pepper)` if present; encrypt `member_id`/`dob`/`name` into `*_enc` only when present (skip crypto entirely if all None).
  - `record(action=action, payer_key=q.payer, member_id_hash=..., *_enc=..., npi=q.npi, status=result.network_status.value, result_jsonb=result.to_dict(), source_audit=result.source_audit, request_id=...)`.
  - structured `log.info` with the **hash**, never raw member id.
- [ ] **Test** (`db`, with `demo_tenant`): a check with a member id stores `member_id_hash` + non-null `member_id_enc`, and **no plaintext member id** appears in `result_jsonb`/`source_audit`/log. Commit `feat: action-tagged PHI-redacted audit writer`.

---

## Task 23: API wiring — auth, eligibility, **lock down existing routes**, CORS, errors

**Files:** Modify `network_probe/api.py`; `tests/test_api_eligibility.py`

- [ ] **Step 1:** Add to `CheckRequest`: `member_id`, `dob`, (already has names) — and validate/normalize `dob`.
- [ ] **Step 2:** Wire app: CORS allowlist (`get_settings().cors_origins`), `ratelimit` middleware, include `auth_router`, add the `HTTPException`→`{message}` handler and the catch-all `Exception`→`{message, request_id}` handler (server-logs the detail).
- [ ] **Step 3: `/api/eligibility`** (Bearer via `get_context`): if `base_url`, `try: assert_safe_url(...) except ValueError → 400`; validate NPI; build `ProviderQuery` **with member_id/dob/names**; `result = check_eligibility(q, base_url=req.base_url)`; `write_audit(ctx, "eligibility", q, result, rid)`; return `{request_id, **result.to_dict()}`.
- [ ] **Step 4: HARDEN existing routes (edit the handlers directly):**
  - `/api/check`: add `ctx: RequestContext = Depends(get_context)`; `try/except` SSRF on `base_url`; on error return `{"message":"...", "request_id": rid}` (log detail) — **remove `str(exc)`**; `write_audit(ctx, "network", q, <wrap verdict>, rid)`.
  - `/api/check-from-report`: add `get_context`; **cap upload at 10 MB** (read with a size guard, 413 over limit); `write_audit(ctx, "report_ingest", q, result, rid)`; remove `str(exc)` leak.
  - `/api/override`: add `get_context`; persist via `OverrideRepo` under `ctx.tenant_id`; `write_audit(ctx, "override", ...)`.
- [ ] **Step 5: Add `/api/eligibility/ping`** (Bearer) for the must_change_password test.
- [ ] **Step 6: Tests**
```python
@pytest.mark.db
def test_routes_require_auth():
    c=TestClient(app)
    assert c.post("/api/eligibility", json={"payer":"oscar"}).status_code==401
    assert c.post("/api/check", json={"payer":"oscar","plan":""}).status_code==401

@pytest.mark.db
def test_eligibility_ssrf_400(auth_header):
    r=TestClient(app).post("/api/eligibility", json={"payer":"fhir","base_url":"http://169.254.169.254/"}, headers=auth_header)
    assert r.status_code==400

@pytest.mark.db
def test_check_no_exc_leak(auth_header):
    r=TestClient(app).post("/api/check", json={"payer":"does-not-exist","plan":""}, headers=auth_header)
    assert r.status_code in (400,500) and "Traceback" not in r.text and "No adapter" not in r.text
```
- [ ] **Step 7: PASS. Commit** `feat: secure API surface (auth+SSRF+audit on all PHI routes, no exc leak)`.

---

## Task 24: Override migration JSON→DB

**Files:** Create `scripts/migrate_overrides.py`, `tests/test_override_migrate.py`; modify `overrides.py` (DB-first read)

- [ ] Idempotent migrator imports legacy `.overrides/overrides.json` into `overrides` under the demo tenant; `OverrideStore`/finalize read from DB within `tenant_session`, JSON fallback retained. Test (`db`) + commit `feat: migrate overrides JSON→Postgres`.

---

## Task 25: Our React + Vite + Ant Design frontend (`web/`)

**Files:** Create `web/` (Vite scaffold), `web/.env`, `web/src/services/auth.ts`, `web/src/pages/Login.tsx`, `web/src/pages/Eligibility.tsx`, `web/src/App.tsx`

- [ ] **Step 1:** Scaffold: `npm create vite@latest web -- --template react-ts` then `cd web && npm i antd react-router-dom react-toastify`. Add `web/.env`:
```dotenv
VITE_API_AUTH=http://localhost:8000/api/auth
VITE_API=http://localhost:8000/api
```
- [ ] **Step 2:** `web/src/services/auth.ts` — port the reference design's token handling (localStorage `auth_token`/`refresh_token`/`auth_expires_at`, a `fetch` wrapper attaching `Authorization: Bearer`, 401→refresh→retry). This is **our** code modeled on the reference; no import from `physician_app_frontend`.
- [ ] **Step 3:** `web/src/pages/Login.tsx` — AntD form posting `application/x-www-form-urlencoded` to `${VITE_API_AUTH}/login`; handle `must_change_password` → change-password screen (`/change-password/`); styled like the reference (split-panel, brand left, form right). On success store tokens and route to `/`.
- [ ] **Step 4:** `web/src/pages/Eligibility.tsx` — authed form (payer, npi, member_id, dob, names) → `POST ${VITE_API}/eligibility`; render the **IN vs OON cost-share matrix** (table grouped by service type: copay/coinsurance/deductible/OOP columns × IN/OON), plus active/plan/PCP/auth/referral badges and the network verdict.
- [ ] **Step 5:** `App.tsx` — router with a guard redirecting to `/login` when no valid token; `react-toastify` for errors.
- [ ] **Step 6: Verify manually** (documented): terminal A `uvicorn network_probe.api:app`; run `alembic upgrade head` (creates demo admin); terminal B `cd web && npm run dev`; log in as the seeded admin, complete must-change-password, run an eligibility check, see the matrix render. Lint passes (`npm run build`).
- [ ] **Step 7: Commit** `feat: our React+Vite+AntD frontend (login + benefits matrix)`.

Note: the benefits-matrix layout is the one genuinely visual piece — if you want, request a browser mockup before building Step 4.

---

## Task 26: Docs + full regression

**Files:** Modify `ARCHITECTURE.md`, `README.md`

- [ ] Update `ARCHITECTURE.md` §10/§11 (Stedi now primary eligibility+benefits; persistence/auth/tenancy/audit layer; our `web/` frontend; Slice B/C boundary) and `README.md` (run steps: `alembic upgrade head`, `uvicorn`, `web` dev server; test markers). Run `pytest -m "not live and not db"` → green, then `pytest -m db` against `preauth_test` → green. Commit `docs: architecture + run/test docs for slice A`.

---

## Task 27: (LAST — gated) Live Stedi production verification

**Files:** Create `tests/test_stedi_live.py` (`live`)

- [ ] With production `STEDI_API_KEY` + a real test member, run one `-m live` test that POSTs a 270 and asserts the 271 parses; **inspect the raw 271** and reconcile EB field-paths in `parse_271.py`. Run `scripts/resolve_payer_ids.py` to fill catalogue ids. Commit `test: live Stedi prod verification + payer-id reconciliation`.

---

## Self-review (v2, against the revised spec)

- **Critical login/RLS fix:** Task 6 SECURITY DEFINER `auth_lookup_user` + Task 13 login over `app_engine` (EXECUTE granted), lockout inside `tenant_session` → login works under FORCE RLS as the NOBYPASSRLS role. Global-unique username (Task 6 `uq_users_username_lower`) removes tenant ambiguity. ✔
- **must_change_password enforced:** Task 12 `get_context` 403s; Task 13 test asserts first-login token → 403 on `/api/eligibility/ping`. ✔
- **Existing routes hardened + audited:** Task 23 Step 4 edits `/api/check`, `/api/check-from-report`, `/api/override` directly (auth + SSRF + upload cap + no `str(exc)` + `write_audit`). ✔
- **SSRF real:** Task 17 `is_global` + redirect re-validation; Task 21 threads `base_url`; Task 23 wraps ValueError→400. ✔
- **Member-keyed flow:** Task 23 adds member_id/dob to `CheckRequest` and `ProviderQuery`; Task 22 encrypts them. ✔
- **Atomic lockout / constant-time / fail-fast crypto / PHI-free jsonb / real met / undefined fixtures / markers / ratelimit / seed / resolver / React frontend:** Tasks 13, 13, 1, 16, 16, 7, 0, 14, 20, 20, 25. ✔
- **Placeholders:** none — live EB-path reconciliation (Task 27) and ROSTER transcription (Task 19) are explicit, dated, mechanical steps. Self-review references resolve to real task numbers (≤27).
- **Type consistency:** `RequestContext`, `EligibilityResult`/`BenefitLine`, `parse_271_benefits`, `StediEligibilityClient.check`, `tenant_session`, `get_context`/`get_context_pwchange`/`context_from_token`, `auth_lookup_user`, `write_audit(ctx, action, q, result, rid)`, `check_eligibility(q, base_url=...)` — consistent across tasks.
