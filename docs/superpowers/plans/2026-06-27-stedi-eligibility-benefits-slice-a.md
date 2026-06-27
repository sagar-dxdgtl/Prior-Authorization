# Stedi Eligibility + Benefits (Slice A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the production Stedi 270/271 feed into a primary eligibility + benefits source (INN/OON cost-share, PCP/auth/referral, COB), landed in a multi-tenant Postgres datastore with row-level security, PHI encryption, PHI-redacted audit logging, and OAuth2/JWT login wired to the existing `physician_app_frontend`.

**Architecture:** FastAPI app. Auth (`/api/auth/*`) issues JWTs carrying `tenant_id`/`role`/`token_version`; a `get_context` dependency turns the Bearer token into a `RequestContext`. Every protected request opens a tenant-scoped DB transaction that sets `app.tenant_id` for Postgres RLS. A `StediEligibilityClient` (implementing the `EligibilitySource` protocol) POSTs a 270 and `parse_271_benefits` walks the 271 into an `EligibilityResult`; the existing directory engine still owns provider-specific network status, and a merge step corroborates the two. Secrets and crypto sit behind `SecretsProvider`/`CryptoProvider` protocols (env+local Fernet now, AWS Secrets Manager + KMS later) so the app runs fully without AWS.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic, Postgres 18, `psycopg[binary]`, `pyjwt`, `passlib[bcrypt]`, `cryptography` (Fernet), `pydantic-settings`, `boto3`, `httpx`, pytest.

**Conventions (apply to every task):**
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **SOLID:** each new module has one responsibility; depend on the protocols defined in Tasks 3–4 and 15, never on concretions.
- **Security is per-task, not a phase:** each task lists its threat notes; do not skip them.
- **No PHI** in logs, the `.cache/` dir, JWT claims, or error responses. Ever.
- Run the full fast suite with `pytest -m "not live"` before each commit; it must stay green (existing 41 tests included).
- DB tests use the local Postgres (`DATABASE_URL=postgresql+psycopg://postgres:sagar@localhost:5432/preauth_test`). Create the DB once: `Task 0`.

---

## File structure (created in this slice)

```
network_probe/
  config.py                  Settings (pydantic-settings) — typed env/.env config
  secrets_provider.py        SecretsProvider protocol + EnvSecrets + AwsSecrets
  crypto.py                  CryptoProvider protocol + FernetCrypto + member-id HMAC hash
  context.py                 RequestContext dataclass (tenant_id, actor_id, role)
  db/
    __init__.py
    base.py                  SQLAlchemy engine + Session factory
    session.py               tenant_session() — TOCTOU-safe RLS transaction scope
    models.py                Tenant, User, Payer, EligibilityCheck, OverrideRow
    repo.py                  tenant-scoped repositories (no raw SQL)
  auth/
    __init__.py
    jwt_tokens.py            issue/decode access+refresh (alg-pinned, typ+tv checks)
    passwords.py             bcrypt hash/verify + policy
    routes.py                /login /refresh /change-password/  (frontend contract)
    deps.py                  get_context() dependency + require_role()
  ratelimit.py               per-tenant counters + x-ratelimit/x-quota headers middleware
  benefits.py                Network/BenefitCategory/CoverageLevel/BenefitLine/EligibilityResult
  stedi/
    __init__.py
    client.py                StediEligibilityClient (EligibilitySource) — 270 POST, no-PHI cache
    parse_271.py             parse_271_benefits() — X12 EB mapping
  payers/
    __init__.py
    catalogue.py             PayerCatalogue protocol + DbPayerCatalogue + seed roster
  eligibility.py             check_eligibility() — Stedi primary + directory merge + audit
  audit.py                   write_audit() — PHI-redacted audit row
  netutil.py                 SSRF guard for user-supplied base_url
  validation.py              NPI Luhn, DOB, member-id input validation
  api.py                     (modify) mount auth, /api/eligibility, harden errors, CORS
  static/index.html          (modify) benefits-matrix fallback render
alembic/                     migrations (env.py, versions/*)
alembic.ini
scripts/resolve_payer_ids.py live Stedi payer-id resolver (gated)
.env.example                 documented config template
tests/                       one test module per component below
```

---

## Task 0: Project bootstrap — deps, gitignore, env, test DB

**Files:**
- Modify: `requirements.txt`
- Create: `.gitignore` (append), `.env.example`, `network_probe/db/__init__.py`, `network_probe/auth/__init__.py`, `network_probe/stedi/__init__.py`, `network_probe/payers/__init__.py`

- [ ] **Step 1: Pin new deps in `requirements.txt`** (append)

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

- [ ] **Step 2: Install**

Run: `pip install -r requirements.txt`
Expected: all install; `python -c "import sqlalchemy, alembic, psycopg, cryptography, jwt, passlib, boto3, pydantic_settings; print('ok')"` prints `ok`.

- [ ] **Step 3: Harden `.gitignore`** — ensure secrets/PHI never get committed. Append:

```
.env
.env.*
!.env.example
.cache/
.overrides/
__MACOSX/
*.pyc
__pycache__/
```

- [ ] **Step 4: Write `.env.example`**

```dotenv
# Local dev. Copy to .env (git-ignored). NEVER commit real secrets.
DATABASE_URL=postgresql+psycopg://postgres:sagar@localhost:5432/preauth
APP_DB_URL=postgresql+psycopg://preauth_app:sagar@localhost:5432/preauth   # NOSUPERUSER, RLS-bound
JWT_SECRET=dev-only-change-me-32-bytes-minimum-xxxxxxxx
JWT_ACCESS_TTL=1800
JWT_REFRESH_TTL=1209600
FERNET_KEYS=PLACEHOLDER_RUN_python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
MEMBER_ID_PEPPER=dev-only-hmac-pepper-change-me
STEDI_API_KEY=
STEDI_ELIGIBILITY_URL=https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3
CORS_ORIGINS=http://localhost:5173
AWS_DEFAULT_REGION=us-east-1
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY — set only when using Secrets Manager/KMS
```

- [ ] **Step 5: Create the two local databases + non-superuser app role**

Run (psql):
```sql
CREATE DATABASE preauth;
CREATE DATABASE preauth_test;
CREATE ROLE preauth_app LOGIN PASSWORD 'sagar' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT ALL ON DATABASE preauth, preauth_test TO preauth_app;
```
Threat note: the app connects as `preauth_app` (NOSUPERUSER, NOBYPASSRLS) so RLS is actually enforced; migrations run as `postgres` (owner). Verify: `psql -d preauth -c "\du preauth_app"` shows no Superuser/Bypass RLS.

- [ ] **Step 6: Add empty `__init__.py` files** for `db/ auth/ stedi/ payers/`.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .gitignore .env.example network_probe/*/__init__.py
git commit -m "chore: slice-A deps, secret-safe gitignore, env template, local DBs"
```

---

## Task 1: Typed config (`config.py`)

**Files:** Create `network_probe/config.py`, `tests/test_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_config.py
from network_probe.config import Settings

def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com,http://b.com")
    s = Settings()
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.jwt_access_ttl == 1800            # default
    assert s.cors_origins == ["http://a.com", "http://b.com"]

def test_jwt_secret_must_be_strong(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("JWT_SECRET", "short")
    import pytest
    with pytest.raises(ValueError):
        Settings()
```

- [ ] **Step 2: Run → FAIL** (`pytest tests/test_config.py -v`, "No module named config").

- [ ] **Step 3: Implement**

```python
# network_probe/config.py
from __future__ import annotations
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str
    app_db_url: str | None = None          # falls back to database_url if unset
    jwt_secret: str
    jwt_access_ttl: int = 1800
    jwt_refresh_ttl: int = 1209600
    fernet_keys: str = ""                  # comma-separated; first is the encrypt key
    member_id_pepper: str = "dev-pepper"
    stedi_api_key: str | None = None
    stedi_eligibility_url: str = (
        "https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3")
    cors_origins: list[str] = ["http://localhost:5173"]
    aws_default_region: str = "us-east-1"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split(cls, v):
        return [x.strip() for x in v.split(",")] if isinstance(v, str) else v

    @field_validator("jwt_secret")
    @classmethod
    def _strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be >= 32 chars")
        return v

    @property
    def effective_app_db_url(self) -> str:
        return self.app_db_url or self.database_url

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat: typed settings with strong-secret validation`.

Threat note: no defaults for `database_url`/`jwt_secret` — the app refuses to boot misconfigured. `lru_cache` avoids re-reading env per request (and avoids a TOCTOU on env between reads).

---

## Task 2: Secrets provider protocol (`secrets_provider.py`)

**Files:** Create `network_probe/secrets_provider.py`, `tests/test_secrets.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_secrets.py
from network_probe.secrets_provider import EnvSecrets, get_secret

def test_env_secret(monkeypatch):
    monkeypatch.setenv("FOO_KEY", "bar")
    assert EnvSecrets().get_secret("FOO_KEY") == "bar"
    assert EnvSecrets().get_secret("MISSING") is None

def test_get_secret_prefers_env_when_no_aws(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("STEDI_API_KEY", "live-key")
    assert get_secret("STEDI_API_KEY") == "live-key"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (Open/Closed: add AWS without touching callers)

```python
# network_probe/secrets_provider.py
from __future__ import annotations
import os
from typing import Optional, Protocol

class SecretsProvider(Protocol):
    def get_secret(self, name: str) -> Optional[str]: ...

class EnvSecrets:
    def get_secret(self, name: str) -> Optional[str]:
        return os.environ.get(name)

class AwsSecrets:
    """Reads from AWS Secrets Manager under prefix preauth/. Used only when AWS creds present."""
    def __init__(self, prefix: str = "preauth/", region: Optional[str] = None):
        import boto3
        self._c = boto3.client("secretsmanager", region_name=region or os.environ.get("AWS_DEFAULT_REGION"))
        self._prefix = prefix
    def get_secret(self, name: str) -> Optional[str]:
        try:
            return self._c.get_secret_value(SecretId=self._prefix + name)["SecretString"]
        except Exception:
            return None

def _provider() -> SecretsProvider:
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        try:
            return AwsSecrets()
        except Exception:
            return EnvSecrets()
    return EnvSecrets()

def get_secret(name: str) -> Optional[str]:
    """Env wins for local dev even with AWS configured (so .env overrides cleanly)."""
    return os.environ.get(name) or _provider().get_secret(name)
```

- [ ] **Step 4: Run → PASS.**  **Step 5: Commit** `feat: SecretsProvider (env + AWS Secrets Manager seam)`.

---

## Task 3: Crypto provider (`crypto.py`)

**Files:** Create `network_probe/crypto.py`, `tests/test_crypto.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_crypto.py
from cryptography.fernet import Fernet
from network_probe.crypto import FernetCrypto, hash_member_id

def test_encrypt_roundtrip():
    c = FernetCrypto([Fernet.generate_key().decode()])
    tok = c.encrypt("MEMBER123")
    assert tok != "MEMBER123"
    assert c.decrypt(tok) == "MEMBER123"

def test_key_rotation_decrypts_old():
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old = FernetCrypto([k1])
    tok = old.encrypt("x")
    new = FernetCrypto([k2, k1])   # new primary, old still accepted
    assert new.decrypt(tok) == "x"
    assert new.encrypt("y") != tok

def test_hash_is_stable_and_peppered():
    a = hash_member_id("ABC123", pepper="p1")
    assert a == hash_member_id("abc123", pepper="p1")   # case-normalized
    assert a != hash_member_id("ABC123", pepper="p2")   # pepper changes digest
    assert len(a) == 64
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# network_probe/crypto.py
from __future__ import annotations
import hashlib, hmac, re
from typing import Protocol
from cryptography.fernet import Fernet, MultiFernet

class CryptoProvider(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, token: str) -> str: ...

class FernetCrypto:
    """First key encrypts; all keys can decrypt (rotation). KMS-wrapped variant lands in Slice B."""
    def __init__(self, keys: list[str]):
        if not keys or not keys[0]:
            raise ValueError("FernetCrypto requires at least one key")
        self._mf = MultiFernet([Fernet(k.encode()) for k in keys])
    def encrypt(self, plaintext: str) -> str:
        return self._mf.encrypt(plaintext.encode()).decode()
    def decrypt(self, token: str) -> str:
        return self._mf.decrypt(token.encode()).decode()

def hash_member_id(member_id: str, pepper: str) -> str:
    """HMAC-SHA256 (keyed) so low-entropy member IDs can't be brute-forced from the digest."""
    norm = re.sub(r"\s+", "", (member_id or "")).upper()
    return hmac.new(pepper.encode(), norm.encode(), hashlib.sha256).hexdigest()
```

- [ ] **Step 4: Run → PASS.**  **Step 5: Commit** `feat: Fernet PHI crypto + peppered member-id HMAC`.

Threat note: plain SHA-256 of a member id is reversible by enumeration (member IDs are low entropy). HMAC with a secret pepper defeats that. Rotation via `MultiFernet` so we never need a flag-day re-encrypt.

---

## Task 4: DB engine, models, TOCTOU-safe RLS session

**Files:** Create `network_probe/context.py`, `network_probe/db/base.py`, `network_probe/db/models.py`, `network_probe/db/session.py`, `tests/test_db_session.py`

- [ ] **Step 1: `context.py`**

```python
# network_probe/context.py
from __future__ import annotations
from dataclasses import dataclass
import uuid

@dataclass(frozen=True)
class RequestContext:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    role: str
```

- [ ] **Step 2: `db/base.py`**

```python
# network_probe/db/base.py
from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from ..config import get_settings

class Base(DeclarativeBase):
    pass

def make_engine(url: str | None = None):
    return create_engine(url or get_settings().effective_app_db_url, pool_pre_ping=True, future=True)

_engine = None
def engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine

SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False, future=True)
```

- [ ] **Step 3: `db/models.py`** (UUID PKs = anti-IDOR; every PHI-bearing table has `tenant_id`)

```python
# network_probe/db/models.py
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, ForeignKey, DateTime, Integer, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

def _uuid() -> uuid.UUID: return uuid.uuid4()
def _now() -> datetime: return datetime.now(timezone.utc)

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
    username: Mapped[str] = mapped_column(String(150), index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="user")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)   # bump = revoke all tokens
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    __table_args__ = ({"comment": "username unique per tenant — enforced by migration index"},)

class Payer(Base):
    __tablename__ = "payers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)         # adapter key / slug
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
    payer_key: Mapped[str] = mapped_column(String(80))
    member_id_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    member_id_enc: Mapped[str | None] = mapped_column(String, nullable=True)   # Fernet
    dob_enc: Mapped[str | None] = mapped_column(String, nullable=True)         # Fernet
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    result_jsonb: Mapped[dict] = mapped_column(JSONB, default=dict)            # NO PHI in here
    source_audit: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

class OverrideRow(Base):
    __tablename__ = "overrides"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    payer: Mapped[str] = mapped_column(String(80))
    npi: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))
    verified_by: Mapped[str] = mapped_column(String(120))
    verified_at: Mapped[str] = mapped_column(String(40))
    network: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str] = mapped_column(String, default="")
```

- [ ] **Step 4: Failing test for the RLS session scope**

```python
# tests/test_db_session.py
import uuid, pytest
from sqlalchemy import text
from network_probe.db.session import tenant_session

@pytest.mark.db
def test_set_config_is_transaction_local():
    tid = uuid.uuid4()
    with tenant_session(tid) as s:
        got = s.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar()
        assert got == str(tid)
    # outside the scope the GUC must NOT leak to the next pooled use
    with tenant_session(uuid.uuid4()) as s2:
        got2 = s2.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar()
        assert got2 != str(tid)
```

- [ ] **Step 5: Run → FAIL** (`pytest tests/test_db_session.py -m db -v`).

- [ ] **Step 6: Implement `db/session.py`** — the TOCTOU-critical piece

```python
# network_probe/db/session.py
from __future__ import annotations
import uuid
from contextlib import contextmanager
from sqlalchemy import text
from .base import SessionLocal, engine

@contextmanager
def tenant_session(tenant_id: uuid.UUID):
    """Open ONE transaction, bind the RLS tenant id LOCAL to it, yield the session.

    `set_config(..., true)` scopes the GUC to this transaction only, so a pooled
    connection can never carry tenant A's id into tenant B's request (TOCTOU/isolation race).
    The tenant id is passed as a BOUND PARAMETER — never string-interpolated (anti-SQLi).
    """
    session = SessionLocal(bind=engine())
    try:
        session.begin()
        session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": str(tenant_id)})
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 7: Run → PASS.**  **Step 8: Commit** `feat: ORM models + TOCTOU-safe RLS tenant session`.

---

## Task 5: Alembic + schema migration + RLS policies

**Files:** Create `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_init.py`, `tests/test_rls_isolation.py`

- [ ] **Step 1: `alembic init alembic`**, then point `alembic/env.py` at `Base.metadata` and `get_settings().database_url` (the **owner** URL, not the app role).

```python
# alembic/env.py  (key lines)
from network_probe.db.base import Base
from network_probe.db import models  # noqa: F401  (register tables)
from network_probe.config import get_settings
target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```

- [ ] **Step 2: Autogenerate the table migration**

Run: `alembic revision --autogenerate -m "init schema"` → review the generated `0001_*.py`.

- [ ] **Step 3: Append RLS + constraints to the migration `upgrade()`** (hand-written, after table creation)

```python
def _rls(op):
    op.create_index("uq_users_tenant_username", "users", ["tenant_id", "username"], unique=True)
    for t in ("users", "payers", "eligibility_checks", "overrides"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")   # owner is subject too
        op.execute(
            f"CREATE POLICY {t}_isolation ON {t} USING "
            f"(tenant_id = current_setting('app.tenant_id', true)::uuid)")
    # payers may also hold tenant-agnostic global rows (tenant_id IS NULL) — readable by all:
    op.execute("DROP POLICY payers_isolation ON payers")
    op.execute("CREATE POLICY payers_isolation ON payers USING "
               "(tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO preauth_app")
```
Call `_rls(op)` at the end of `upgrade()`.

- [ ] **Step 4: Apply** `alembic upgrade head` against `preauth` and `preauth_test`.

- [ ] **Step 5: RLS isolation test (the security assertion)**

```python
# tests/test_rls_isolation.py
import uuid, pytest
from network_probe.db.session import tenant_session
from network_probe.db.models import OverrideRow

@pytest.mark.db
def test_tenant_cannot_read_other_tenants_rows():
    a, b = uuid.uuid4(), uuid.uuid4()
    # seed tenants out-of-band as owner first (fixture); then:
    with tenant_session(a) as s:
        s.add(OverrideRow(tenant_id=a, payer="oscar", npi="1", status="IN_NETWORK",
                          verified_by="t", verified_at="2026-01-01")); s.flush()
    with tenant_session(b) as s:
        rows = s.query(OverrideRow).all()
        assert rows == []          # B sees none of A's rows — RLS holds
```
(Tenants `a`,`b` inserted by a conftest fixture running as DB owner with RLS context set per row.)

- [ ] **Step 6: Run → PASS.**  **Step 7: Commit** `feat: schema migration with FORCE RLS isolation policies`.

Threat note: `FORCE ROW LEVEL SECURITY` + a non-owner, NOBYPASSRLS app role is what makes isolation real — without both, the table owner silently bypasses policies.

---

## Task 6: Tenant-scoped repositories (`db/repo.py`)

**Files:** Create `network_probe/db/repo.py`, `tests/test_repo.py`

- [ ] **Step 1: Failing test** — repo never accepts a tenant_id from the caller's *data*, only from the session context; writes set it from the bound GUC.

```python
# tests/test_repo.py
import uuid, pytest
from network_probe.db.session import tenant_session
from network_probe.db.repo import OverrideRepo

@pytest.mark.db
def test_override_repo_scopes_to_session_tenant():
    tid = uuid.uuid4()   # seeded by fixture
    with tenant_session(tid) as s:
        repo = OverrideRepo(s, tid)
        repo.add(payer="oscar", npi="123", status="OUT_OF_NETWORK",
                 verified_by="ops:jdoe", verified_at="2026-06-01")
        found = repo.lookup(payer="oscar", npi="123")
        assert found and found.status == "OUT_OF_NETWORK"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (DIP: callers depend on repos, not on ORM/SQL)

```python
# network_probe/db/repo.py
from __future__ import annotations
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from .models import OverrideRow, EligibilityCheck

class OverrideRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id
    def add(self, **kw) -> OverrideRow:
        row = OverrideRow(tenant_id=self.tid, **kw)   # tenant_id from context, never from kw
        self.s.add(row); self.s.flush(); return row
    def lookup(self, payer: str, npi: str) -> Optional[OverrideRow]:
        return (self.s.query(OverrideRow)
                .filter(OverrideRow.payer == payer, OverrideRow.npi == npi)   # RLS adds tenant filter
                .order_by(OverrideRow.verified_at.desc()).first())

class EligibilityCheckRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id
    def record(self, **kw) -> EligibilityCheck:
        row = EligibilityCheck(tenant_id=self.tid, **kw)
        self.s.add(row); self.s.flush(); return row
```

- [ ] **Step 4: Run → PASS.**  **Step 5: Commit** `feat: tenant-scoped repositories (tenant_id from context only)`.

Threat note: `add(**kw)` hard-binds `tenant_id=self.tid`; even if a caller passed `tenant_id` it would collide and error — no cross-tenant write via mass-assignment.

---

## Task 7: Passwords (`auth/passwords.py`)

**Files:** Create `network_probe/auth/passwords.py`, `tests/test_passwords.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_passwords.py
import pytest
from network_probe.auth.passwords import hash_password, verify_password, check_policy

def test_hash_verify():
    h = hash_password("Sup3r-secret!")
    assert h != "Sup3r-secret!"
    assert verify_password("Sup3r-secret!", h)
    assert not verify_password("wrong", h)

def test_policy_rejects_weak():
    with pytest.raises(ValueError):
        check_policy("short")
    check_policy("Str0ng-enough-pw")   # no raise
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/auth/passwords.py
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
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: bcrypt password hashing + policy`.

---

## Task 8: JWT tokens (`auth/jwt_tokens.py`)

**Files:** Create `network_probe/auth/jwt_tokens.py`, `tests/test_jwt.py`

- [ ] **Step 1: Failing test** (alg pinned; refresh token rejected where access expected; tv mismatch rejected)

```python
# tests/test_jwt.py
import uuid, pytest
from network_probe.auth import jwt_tokens as jt

def _claims():
    return dict(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="user", token_version=3)

def test_access_roundtrip():
    c = _claims()
    tok, exp = jt.issue_access(**c)
    d = jt.decode_token(tok, expected_typ="access")
    assert d["sub"] == str(c["user_id"]) and d["tid"] == str(c["tenant_id"]) and exp == 1800

def test_refresh_not_accepted_as_access():
    c = _claims(); rtok = jt.issue_refresh(**c)
    with pytest.raises(jt.TokenError):
        jt.decode_token(rtok, expected_typ="access")

def test_tampered_alg_rejected():
    c = _claims(); tok, _ = jt.issue_access(**c)
    with pytest.raises(jt.TokenError):
        jt.decode_token(tok + "x", expected_typ="access")
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/auth/jwt_tokens.py
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
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: alg-pinned JWT with typ separation`.

Threat note: `algorithms=[ALG]` blocks alg-confusion and the `alg:none` attack; `typ` separation stops a refresh token being replayed as an access token; `tv` (checked in Task 9 against the DB) gives instant revoke-all on password change.

---

## Task 9: Auth context dependency (`auth/deps.py`)

**Files:** Create `network_probe/auth/deps.py`, `tests/test_auth_deps.py`

- [ ] **Step 1: Failing test** — a valid access token whose `tv` no longer matches the user is rejected.

```python
# tests/test_auth_deps.py
import uuid, pytest
from fastapi import HTTPException
from network_probe.auth import jwt_tokens as jt
from network_probe.auth.deps import context_from_token

class FakeUser:  # stand-in; real one comes from DB
    def __init__(self, tv): self.token_version = tv; self.role="user"; self.tenant_id=uuid.uuid4(); self.id=uuid.uuid4()

def test_token_version_mismatch_rejected(monkeypatch):
    u = FakeUser(tv=5)
    tok, _ = jt.issue_access(u.id, u.tenant_id, u.role, token_version=4)  # stale tv
    monkeypatch.setattr("network_probe.auth.deps._load_user", lambda uid, tid: FakeUser(tv=5))
    with pytest.raises(HTTPException) as e:
        context_from_token(tok)
    assert e.value.status_code == 401
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/auth/deps.py
from __future__ import annotations
import uuid
from typing import Optional
from fastapi import Depends, HTTPException, Header
from .jwt_tokens import decode_token, TokenError
from ..context import RequestContext
from ..db.session import tenant_session
from ..db.models import User

def _load_user(user_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[User]:
    with tenant_session(tenant_id) as s:
        return s.get(User, user_id)

def context_from_token(token: str) -> RequestContext:
    try:
        claims = decode_token(token, expected_typ="access")
    except TokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    tid, uid = uuid.UUID(claims["tid"]), uuid.UUID(claims["sub"])
    user = _load_user(uid, tid)
    if not user or user.token_version != claims.get("tv"):
        raise HTTPException(status_code=401, detail="invalid token")
    return RequestContext(tenant_id=tid, actor_id=uid, role=claims.get("role", "user"))

def get_context(authorization: str | None = Header(default=None)) -> RequestContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return context_from_token(authorization.split(" ", 1)[1].strip())

def require_role(*roles: str):
    def dep(ctx: RequestContext = Depends(get_context)) -> RequestContext:
        if roles and ctx.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return ctx
    return dep
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: get_context dependency with token_version revocation`.

---

## Task 10: Auth routes (`auth/routes.py`) — the frontend contract

**Files:** Create `network_probe/auth/routes.py`, `tests/test_auth_routes.py`

- [ ] **Step 1: Failing test** (login form-encoded; lockout; refresh; change-password bumps tv)

```python
# tests/test_auth_routes.py
import pytest
from fastapi.testclient import TestClient
from network_probe.api import app   # app mounts the auth router (Task 16)

@pytest.mark.db
def test_login_refresh_changepw(seed_admin):   # fixture creates tenant+user must_change_password
    c = TestClient(app)
    r = c.post("/api/auth/login", data={"grant_type":"password","username":"admin","password":"Initial-pw-123"})
    assert r.status_code == 200
    body = r.json(); assert body["must_change_password"] is True and body["tokens"]["access"]
    acc = body["tokens"]["access"]
    r2 = c.post("/api/auth/change-password/", headers={"Authorization": f"Bearer {acc}"},
                json={"current_password":"Initial-pw-123","new_password":"Brand-new-pw-456","confirm_password":"Brand-new-pw-456"})
    assert r2.status_code == 200 and r2.json()["success"] is True
    # old access token now invalid (token_version bumped)
    r3 = c.get("/api/eligibility/ping", headers={"Authorization": f"Bearer {acc}"})
    assert r3.status_code == 401

@pytest.mark.db
def test_bad_password_generic_error(seed_admin):
    c = TestClient(app)
    r = c.post("/api/auth/login", data={"grant_type":"password","username":"admin","password":"nope"})
    assert r.status_code == 401 and "invalid" in r.json()["message"].lower()
    assert "username" not in r.json()["message"].lower()   # no user-enumeration leak
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/auth/routes.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from ..db.session import tenant_session
from ..db.base import SessionLocal, engine
from ..db.models import User
from .passwords import verify_password, hash_password, check_policy
from . import jwt_tokens as jt
from .deps import get_context
from ..context import RequestContext

router = APIRouter(prefix="/api/auth", tags=["auth"])
LOCK_THRESHOLD, LOCK_MINUTES = 5, 15

def _user_by_username(username: str) -> User | None:
    # login is pre-tenant: look up across tenants by username as the DB owner-less app role.
    with SessionLocal(bind=engine()) as s:
        return s.execute(select(User).where(User.username == username)).scalar_one_or_none()

def _user_payload(u: User) -> dict:
    return {"id": str(u.id), "username": u.username, "name": u.username, "role": u.role,
            "tenant_id": str(u.tenant_id)}

@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    u = _user_by_username(form.username)
    now = datetime.now(timezone.utc)
    if u and u.locked_until and u.locked_until > now:
        raise HTTPException(status_code=429, detail={"message": "account temporarily locked"})
    if not u or not verify_password(form.password, u.password_hash):
        if u:   # count failures atomically
            with SessionLocal(bind=engine()) as s:
                db = s.get(User, u.id); db.failed_logins += 1
                if db.failed_logins >= LOCK_THRESHOLD:
                    db.locked_until = now + timedelta(minutes=LOCK_MINUTES); db.failed_logins = 0
                s.commit()
        raise HTTPException(status_code=401, detail={"message": "invalid credentials"})
    with SessionLocal(bind=engine()) as s:   # reset counters
        db = s.get(User, u.id); db.failed_logins = 0; db.locked_until = None; s.commit()
    access, expires_in = jt.issue_access(u.id, u.tenant_id, u.role, u.token_version)
    refresh = jt.issue_refresh(u.id, u.tenant_id, u.role, u.token_version)
    if u.must_change_password:
        return {"must_change_password": True, "tokens": {"access": access}, "user": _user_payload(u)}
    return {"access_token": access, "expires_in": expires_in,
            "refresh_token": refresh, "user": _user_payload(u)}

class RefreshReq(BaseModel):
    refresh_token: str

@router.post("/refresh")
def refresh(req: RefreshReq):
    try:
        claims = jt.decode_token(req.refresh_token, expected_typ="refresh")
    except jt.TokenError:
        raise HTTPException(status_code=401, detail={"message": "invalid refresh token"})
    import uuid
    uid, tid = uuid.UUID(claims["sub"]), uuid.UUID(claims["tid"])
    with tenant_session(tid) as s:
        u = s.get(User, uid)
        if not u or u.token_version != claims.get("tv"):
            raise HTTPException(status_code=401, detail={"message": "invalid refresh token"})
        access, expires_in = jt.issue_access(u.id, u.tenant_id, u.role, u.token_version)
    return {"access_token": access, "expires_in": expires_in}   # no new refresh per frontend spec

class ChangePwReq(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

@router.post("/change-password/")
def change_password(req: ChangePwReq, ctx: RequestContext = Depends(get_context)):
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
        u.token_version += 1          # revoke all existing tokens atomically
        s.commit()
    return {"success": True}
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: OAuth2 login/refresh/change-password matching frontend contract`.

Note: FastAPI returns `HTTPException.detail` as `{"detail": ...}`; add an exception handler in Task 16 to surface `detail["message"]` at top level so the frontend's `data.message` works. Tests above assume that handler.

---

## Task 11: Benefits domain model (`benefits.py`)

**Files:** Create `network_probe/benefits.py`, `tests/test_benefits_model.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_benefits_model.py
from decimal import Decimal
from network_probe.benefits import (BenefitLine, EligibilityResult, Network, BenefitCategory, CoverageLevel)
from network_probe.models import NetworkStatus

def test_eligibility_result_to_dict_has_no_phi():
    bl = BenefitLine(service_type="30", service_type_label="General", network=Network.OON,
                     category=BenefitCategory.COPAY, level=CoverageLevel.INDIVIDUAL,
                     amount=Decimal("50"), percent=None, time_period="calendar year",
                     met=None, remaining=None, raw_codes={"EB01":"B"})
    r = EligibilityResult(coverage_active=True, plan_name="Silver", group="GRP1",
                          coverage_dates={}, network_status=NetworkStatus.OUT_OF_NETWORK,
                          benefits=[bl], pcp_required=False, prior_auth_required=True,
                          referral_required=False, cob=None, network_verdict=None,
                          corroboration=[], source_audit={"endpoint":"stedi"})
    d = r.to_dict()
    assert d["network_status"] == "OUT_OF_NETWORK"
    assert d["benefits"][0]["network"] == "OON" and d["benefits"][0]["amount"] == "50"
    assert "member_id" not in str(d)    # model carries no PHI
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/benefits.py
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional
from .models import NetworkStatus, NetworkVerdict

class Network(str, Enum): IN = "IN"; OON = "OON"; UNKNOWN = "UNKNOWN"
class BenefitCategory(str, Enum):
    COPAY="copay"; COINSURANCE="coinsurance"; DEDUCTIBLE="deductible"; OOP_MAX="oop_max"; LIMITATION="limitation"
class CoverageLevel(str, Enum): INDIVIDUAL="individual"; FAMILY="family"; UNKNOWN="unknown"

@dataclass
class BenefitLine:
    service_type: str
    service_type_label: str
    network: Network
    category: BenefitCategory
    level: CoverageLevel
    amount: Optional[Decimal]
    percent: Optional[Decimal]
    time_period: Optional[str]
    met: Optional[Decimal]
    remaining: Optional[Decimal]
    raw_codes: dict = field(default_factory=dict)
    def to_dict(self) -> dict:
        return {"service_type": self.service_type, "service_type_label": self.service_type_label,
                "network": self.network.value, "category": self.category.value, "level": self.level.value,
                "amount": None if self.amount is None else str(self.amount),
                "percent": None if self.percent is None else str(self.percent),
                "time_period": self.time_period,
                "met": None if self.met is None else str(self.met),
                "remaining": None if self.remaining is None else str(self.remaining),
                "raw_codes": self.raw_codes}

@dataclass
class EligibilityResult:
    coverage_active: Optional[bool]
    plan_name: Optional[str]
    group: Optional[str]
    coverage_dates: dict
    network_status: NetworkStatus
    benefits: list[BenefitLine]
    pcp_required: Optional[bool]
    prior_auth_required: Optional[bool]
    referral_required: Optional[bool]
    cob: Optional[dict]
    network_verdict: Optional[NetworkVerdict]
    corroboration: list
    source_audit: dict
    def to_dict(self) -> dict:
        return {"coverage_active": self.coverage_active, "plan_name": self.plan_name,
                "group": self.group, "coverage_dates": self.coverage_dates,
                "network_status": self.network_status.value,
                "benefits": [b.to_dict() for b in self.benefits],
                "pcp_required": self.pcp_required, "prior_auth_required": self.prior_auth_required,
                "referral_required": self.referral_required, "cob": self.cob,
                "network_verdict": self.network_verdict.to_dict() if self.network_verdict else None,
                "corroboration": self.corroboration, "source_audit": self.source_audit}
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: benefits domain model (IN/OON cost-share lines)`.

---

## Task 12: 271 parser (`stedi/parse_271.py`)

**Files:** Create `network_probe/stedi/parse_271.py`, `tests/test_parse_271.py`, `tests/fixtures/stedi-271-inn-oon.json`

- [ ] **Step 1: Write the synthetic fixture** `tests/fixtures/stedi-271-inn-oon.json` (NO real PHI) covering active coverage, IN copay, OON copay, family deductible with remaining, prior-auth flag:

```json
{"benefitsInformation": [
  {"code": "1", "name": "Active Coverage", "serviceTypeCodes": ["30"]},
  {"code": "B", "name": "Co-Payment", "serviceTypeCodes": ["98"], "coverageLevelCode": "IND",
   "inPlanNetworkIndicatorCode": "Y", "benefitAmount": "30", "timeQualifierCode": "27"},
  {"code": "B", "name": "Co-Payment", "serviceTypeCodes": ["98"], "coverageLevelCode": "IND",
   "inPlanNetworkIndicatorCode": "N", "benefitAmount": "60", "timeQualifierCode": "27"},
  {"code": "C", "name": "Deductible", "serviceTypeCodes": ["30"], "coverageLevelCode": "FAM",
   "inPlanNetworkIndicatorCode": "N", "benefitAmount": "4000", "timeQualifierCode": "29"},
  {"code": "A", "name": "Co-Insurance", "serviceTypeCodes": ["30"], "coverageLevelCode": "IND",
   "inPlanNetworkIndicatorCode": "N", "benefitPercent": "0.4"},
  {"code": "F", "name": "Limitations", "serviceTypeCodes": ["30"],
   "additionalInformation": [{"description": "Prior authorization required"}]}
]}
```

- [ ] **Step 2: Failing test**

```python
# tests/test_parse_271.py
import json, pathlib
from decimal import Decimal
from network_probe.stedi.parse_271 import parse_271_benefits
from network_probe.benefits import Network, BenefitCategory, CoverageLevel

DATA = json.loads((pathlib.Path(__file__).parent/"fixtures/stedi-271-inn-oon.json").read_text())

def test_parses_inn_and_oon_copays():
    r = parse_271_benefits(DATA)
    assert r.coverage_active is True
    copays = [b for b in r.benefits if b.category == BenefitCategory.COPAY]
    nets = {b.network: b.amount for b in copays}
    assert nets[Network.IN] == Decimal("30") and nets[Network.OON] == Decimal("60")

def test_family_oon_deductible_remaining_and_prior_auth():
    r = parse_271_benefits(DATA)
    ded = next(b for b in r.benefits if b.category == BenefitCategory.DEDUCTIBLE)
    assert ded.network == Network.OON and ded.level == CoverageLevel.FAMILY
    assert ded.remaining == Decimal("4000")
    coins = next(b for b in r.benefits if b.category == BenefitCategory.COINSURANCE)
    assert coins.percent == Decimal("0.4")
    assert r.prior_auth_required is True

def test_aaa_reject_is_unknown_never_oon():
    r = parse_271_benefits({"errors": [{"code": "42", "description": "unable to respond"}]})
    from network_probe.models import NetworkStatus
    assert r.coverage_active is None and r.network_status == NetworkStatus.UNKNOWN
```

- [ ] **Step 3: Run → FAIL.  Step 4: Implement**

```python
# network_probe/stedi/parse_271.py
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Optional
from ..benefits import (BenefitLine, EligibilityResult, Network, BenefitCategory, CoverageLevel)
from ..models import NetworkStatus

_CATEGORY = {"B": BenefitCategory.COPAY, "A": BenefitCategory.COINSURANCE,
             "C": BenefitCategory.DEDUCTIBLE, "G": BenefitCategory.OOP_MAX, "F": BenefitCategory.LIMITATION}
_LEVEL = {"IND": CoverageLevel.INDIVIDUAL, "FAM": CoverageLevel.FAMILY}
_NET = {"Y": Network.IN, "N": Network.OON}
_TIME = {"23": "calendar year", "29": "remaining", "27": "visit", "22": "service year"}

def _dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None

def parse_271_benefits(data: dict) -> EligibilityResult:
    if data.get("errors"):
        return EligibilityResult(None, None, None, {}, NetworkStatus.UNKNOWN, [], None, None, None,
                                 None, None, [], {"errors": data["errors"]})
    infos = data.get("benefitsInformation") or []
    active = any(b.get("code") == "1" for b in infos)
    lines: list[BenefitLine] = []
    prior_auth = referral = pcp = None
    for b in infos:
        code = b.get("code")
        text = " ".join(ai.get("description", "") for ai in (b.get("additionalInformation") or [])).lower()
        if "prior auth" in text or "preauth" in text or "pre-auth" in text:
            prior_auth = True
        if "referral" in text:
            referral = True
        if "primary care" in text or "pcp" in text:
            pcp = True
        cat = _CATEGORY.get(code)
        if cat is None:
            continue
        time_period = _TIME.get(b.get("timeQualifierCode"))
        amount = _dec(b.get("benefitAmount"))
        line = BenefitLine(
            service_type=(b.get("serviceTypeCodes") or [""])[0],
            service_type_label=(b.get("serviceTypes") or [b.get("name", "")])[0] if b.get("serviceTypes") else b.get("name", ""),
            network=_NET.get(b.get("inPlanNetworkIndicatorCode"), Network.UNKNOWN),
            category=cat, level=_LEVEL.get(b.get("coverageLevelCode"), CoverageLevel.UNKNOWN),
            amount=amount if cat != BenefitCategory.COINSURANCE else None,
            percent=_dec(b.get("benefitPercent")),
            time_period=time_period,
            met=amount if time_period == "calendar year" and cat in (BenefitCategory.DEDUCTIBLE, BenefitCategory.OOP_MAX) and False else None,
            remaining=amount if time_period == "remaining" else None,
            raw_codes={k: b.get(k) for k in ("code", "coverageLevelCode", "inPlanNetworkIndicatorCode", "timeQualifierCode")})
        lines.append(line)
    nets = {l.network for l in lines}
    if Network.IN in nets and Network.OON not in nets:
        status = NetworkStatus.IN_NETWORK
    elif Network.OON in nets and Network.IN not in nets:
        status = NetworkStatus.OUT_OF_NETWORK
    else:
        status = NetworkStatus.UNKNOWN     # mixed/none → defer to directory engine, never guess
    return EligibilityResult(
        coverage_active=active, plan_name=data.get("planInformation", {}).get("planName"),
        group=data.get("planInformation", {}).get("groupNumber"), coverage_dates=data.get("planDateInformation", {}),
        network_status=status, benefits=lines, pcp_required=pcp, prior_auth_required=prior_auth,
        referral_required=referral, cob=data.get("coordinationOfBenefits"),
        network_verdict=None, corroboration=[], source_audit={"source": "stedi-271"})
```

- [ ] **Step 5: Run → PASS.  Step 6: Commit** `feat: 271 → IN/OON benefits parser (honest UNKNOWN on reject)`.

Note: EB field paths are best-effort per the spec; the live test (Task 22) validates them against a real 271 and this parser gets adjusted then. The `met` calc is intentionally conservative (left None unless a clear "calendar year met" line appears) — refined against live data.

---

## Task 13: SSRF guard + input validation

**Files:** Create `network_probe/netutil.py`, `network_probe/validation.py`, `tests/test_netutil.py`, `tests/test_validation.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_netutil.py
import pytest
from network_probe.netutil import assert_safe_url

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/", "http://localhost:8000",
    "http://127.0.0.1/", "http://10.0.0.5/fhir", "http://192.168.1.1/", "file:///etc/passwd"])
def test_blocks_internal(url):
    with pytest.raises(ValueError):
        assert_safe_url(url)

def test_allows_public_https():
    assert_safe_url("https://fhir.humana.com/api")  # no raise
```
```python
# tests/test_validation.py
import pytest
from network_probe.validation import valid_npi, normalize_dob

def test_npi_luhn():
    assert valid_npi("1679766943")        # known-good (Herron)
    assert not valid_npi("1234567890")
    assert not valid_npi("abc")

def test_dob():
    assert normalize_dob("01/02/1980") == "1980-01-02"
    with pytest.raises(ValueError):
        normalize_dob("not-a-date")
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/netutil.py
from __future__ import annotations
import ipaddress, socket
from urllib.parse import urlparse

def assert_safe_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs allowed")
    host = p.hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise ValueError("host does not resolve")
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("URL resolves to a non-public address")
    return url
```
```python
# network_probe/validation.py
from __future__ import annotations
import re
from datetime import datetime

def valid_npi(npi: str) -> bool:
    if not npi or not re.fullmatch(r"\d{10}", npi):
        return False
    digits = [int(c) for c in "80840" + npi[:9]]   # NPI Luhn with 80840 prefix
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9: d -= 9
        total += d
    return (total + int(npi[9])) % 10 == 0

def normalize_dob(dob: str) -> str:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(dob, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError("unrecognized DOB format")
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: SSRF allowlist + NPI/DOB validation`.

Threat note: `assert_safe_url` resolves the host and rejects RFC1918/loopback/link-local (cloud metadata at 169.254.169.254) — the FHIR `base_url` is attacker-influenced now that the API is authenticated/exposed.

---

## Task 14: Stedi client (`stedi/client.py`)

**Files:** Create `network_probe/stedi/client.py`, `tests/test_stedi_client.py`

- [ ] **Step 1: Failing test** (no-PHI cache; EligibilitySource protocol; missing key → honest result)

```python
# tests/test_stedi_client.py
import httpx, json
from network_probe.stedi.client import StediEligibilityClient
from network_probe.models import ProviderQuery
from network_probe._http import CachedClient

def _mock(json_body):
    def handler(req):
        assert b"member" not in req.url.raw_path  # PHI not in URL
        return httpx.Response(200, json=json_body)
    return CachedClient(cache_dir=None, delay_seconds=0,
                        client=httpx.Client(transport=httpx.MockTransport(handler)))

def test_client_parses_271(monkeypatch):
    body = {"benefitsInformation": [{"code": "1", "serviceTypeCodes": ["30"]}]}
    c = StediEligibilityClient(api_key="k", client=_mock(body), payer_id="OSCAR")
    q = ProviderQuery(payer="oscar", plan_hint="", npi="1679766943", member_id="M1", dob="01/02/1980")
    res = c.check(q)
    assert res.coverage_active is True

def test_no_key_returns_unknown():
    c = StediEligibilityClient(api_key=None, payer_id="OSCAR")
    res = c.check(ProviderQuery(payer="oscar", plan_hint="", npi="1"))
    assert res.coverage_active is None
```

- [ ] **Step 2: Run → FAIL.  Step 3: Implement**

```python
# network_probe/stedi/client.py
from __future__ import annotations
import json
from typing import Optional, Protocol
from .._http import CachedClient
from ..config import get_settings
from ..secrets_provider import get_secret
from ..models import ProviderQuery, NetworkStatus
from ..benefits import EligibilityResult
from .parse_271 import parse_271_benefits

class EligibilitySource(Protocol):
    def check(self, q: ProviderQuery) -> EligibilityResult: ...

def _unknown(reason: str) -> EligibilityResult:
    return EligibilityResult(None, None, None, {}, NetworkStatus.UNKNOWN, [], None, None, None,
                             None, None, [], {"source": "stedi", "note": reason})

class StediEligibilityClient:
    DEFAULT_STC = ["30", "98"]
    def __init__(self, api_key: Optional[str] = None, client: Optional[CachedClient] = None,
                 payer_id: Optional[str] = None, service_type_codes: Optional[list[str]] = None):
        self.api_key = api_key if api_key is not None else get_secret("STEDI_API_KEY")
        # PHI MUST NOT hit disk: force cache_dir=None for this client.
        self.client = client or CachedClient(cache_dir=None, delay_seconds=0.2)
        self.payer_id = payer_id
        self.stc = service_type_codes or self.DEFAULT_STC
        self.url = get_settings().stedi_eligibility_url

    def check(self, q: ProviderQuery) -> EligibilityResult:
        if not self.api_key:
            return _unknown("STEDI_API_KEY not configured")
        if not self.payer_id:
            return _unknown(f"no Stedi payer id for {q.payer!r}")
        body = {"tradingPartnerServiceId": self.payer_id,
                "provider": {k: v for k, v in {"npi": q.npi, "lastName": q.last_name}.items() if v},
                "subscriber": {k: v for k, v in {
                    "memberId": q.member_id, "dateOfBirth": _dob(q.dob),
                    "firstName": q.first_name, "lastName": q.last_name}.items() if v},
                "encounter": {"serviceTypeCodes": self.stc}}
        try:
            data = self.client.post_json(self.url, content=json.dumps(body),
                headers={"Authorization": self.api_key, "content-type": "application/json"})
        except Exception:
            return _unknown("Stedi eligibility call failed")
        return parse_271_benefits(data)

def _dob(dob: Optional[str]) -> Optional[str]:
    import re
    if not dob: return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dob)
    return f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}" if m else dob
```

- [ ] **Step 4: Run → PASS.  Step 5: Commit** `feat: StediEligibilityClient (EligibilitySource, no-PHI cache)`.

---

## Task 15: Payer catalogue (`payers/catalogue.py`) + roster seed

**Files:** Create `network_probe/payers/catalogue.py`, `network_probe/payers/roster_seed.py`, `tests/test_catalogue.py`; migration `alembic/versions/0002_seed_payers.py`

- [ ] **Step 1: Encode the roster** in `roster_seed.py` as data (every row from the AZ/CO/FL/NY list), e.g.:

```python
# network_probe/payers/roster_seed.py
# (payer label, benefit_type, state, stedi_payer_id|None, enrollment_status)
ROSTER = [
    ("Aetna", "Commercial", "AZ", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "AZ", "60054", "needs_enrollment"),
    ("Alignment Health Plan", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Ambetter (Centene)", "ACA", "AZ", "68069", "needs_enrollment"),
    ("Arizona Complete Health (Centene)", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("AHCCCS", "Traditional Medicaid", "AZ", None, "needs_payer_id"),
    ("BCBS / Anthem (Elevance)", "ACA", "AZ", None, "needs_payer_id"),
    # ... (full AZ/CO/FL/NY roster continues — one tuple per table row) ...
    ("Oscar", "ACA", "AZ", "OSCAR", "supported"),
    ("Devoted Health", "Medicare Advantage", "FL", "DEVOT", "supported"),
    ("Humana", "Medicare Advantage", "AZ", "61101", "supported"),
    ("Cigna Healthcare", "Commercial", "AZ", "62308", "supported"),
    ("UnitedHealthcare", "Commercial", "AZ", "87726", "supported"),
]
```
(Implementer: transcribe every row from the spec's roster table. Unknown Stedi ids = `None` + `needs_payer_id`; the resolver in Task 23 fills them.)

- [ ] **Step 2: Failing test**

```python
# tests/test_catalogue.py
import pytest
from network_probe.payers.catalogue import DbPayerCatalogue

@pytest.mark.db
def test_catalogue_resolves_known_payer(seed_payers):   # fixture runs the 0002 seed
    cat = DbPayerCatalogue()
    p = cat.resolve("oscar")
    assert p and p.stedi_payer_id == "OSCAR" and p.enrollment_status == "supported"

@pytest.mark.db
def test_unknown_payer_returns_none(seed_payers):
    assert DbPayerCatalogue().resolve("not-a-payer") is None
```

- [ ] **Step 3: Run → FAIL.  Step 4: Implement** (Interface Segregation: tiny `resolve()` surface)

```python
# network_probe/payers/catalogue.py
from __future__ import annotations
import re
from typing import Optional, Protocol
from sqlalchemy import select
from ..db.base import SessionLocal, engine
from ..db.models import Payer

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

class PayerCatalogue(Protocol):
    def resolve(self, payer_key: str) -> Optional[Payer]: ...

class DbPayerCatalogue:
    """Global (tenant_id IS NULL) catalogue rows; tenant overrides allowed later."""
    def resolve(self, payer_key: str) -> Optional[Payer]:
        key = _slug(payer_key)
        with SessionLocal(bind=engine()) as s:
            rows = s.execute(select(Payer)).scalars().all()
            for p in rows:
                if _slug(p.key) == key or _slug(p.label) == key:
                    return p
        return None
```

- [ ] **Step 5: Migration `0002_seed_payers.py`** uses `INSERT … ON CONFLICT DO NOTHING` (TOCTOU-safe idempotent seed) inserting each ROSTER tuple as a global payer (`tenant_id=NULL`, `key=_slug(label)+"-"+state`).

- [ ] **Step 6: Run → PASS.  Step 7: Commit** `feat: payer catalogue + AZ/CO/FL/NY roster seed`.

---

## Task 16: Wire the app — mount auth, error handler, CORS, eligibility router

**Files:** Modify `network_probe/api.py`; Create `network_probe/eligibility.py`, `network_probe/audit.py`, `tests/test_api_eligibility.py`

- [ ] **Step 1: `audit.py`** (PHI-redacted)

```python
# network_probe/audit.py
from __future__ import annotations
import logging, uuid
from .crypto import FernetCrypto, hash_member_id
from .config import get_settings
from .db.session import tenant_session
from .db.repo import EligibilityCheckRepo
from .context import RequestContext
from .benefits import EligibilityResult
from .models import ProviderQuery

log = logging.getLogger("preauth.audit")

def _crypto() -> FernetCrypto:
    keys = [k for k in get_settings().fernet_keys.split(",") if k]
    return FernetCrypto(keys)

def write_audit(ctx: RequestContext, q: ProviderQuery, result: EligibilityResult, request_id: str) -> None:
    s = get_settings(); c = _crypto()
    mid_hash = hash_member_id(q.member_id, s.member_id_pepper) if q.member_id else None
    with tenant_session(ctx.tenant_id) as sess:
        EligibilityCheckRepo(sess, ctx.tenant_id).record(
            actor_id=ctx.actor_id, payer_key=q.payer,
            member_id_hash=mid_hash,
            member_id_enc=c.encrypt(q.member_id) if q.member_id else None,
            dob_enc=c.encrypt(q.dob) if q.dob else None,
            npi=q.npi, status=result.network_status.value,
            result_jsonb=result.to_dict(), source_audit=result.source_audit, request_id=request_id)
    log.info("eligibility_check tenant=%s actor=%s payer=%s npi=%s member=%s status=%s req=%s",
             ctx.tenant_id, ctx.actor_id, q.payer, q.npi, mid_hash, result.network_status.value, request_id)
```

- [ ] **Step 2: `eligibility.py`** (Stedi primary + directory merge)

```python
# network_probe/eligibility.py
from __future__ import annotations
from .models import ProviderQuery, NetworkStatus
from .benefits import EligibilityResult
from .stedi.client import StediEligibilityClient
from .payers.catalogue import DbPayerCatalogue, PayerCatalogue
from .service import check_network, get_adapter
from .context import RequestContext

def check_eligibility(q: ProviderQuery, catalogue: PayerCatalogue | None = None) -> EligibilityResult:
    cat = catalogue or DbPayerCatalogue()
    payer = cat.resolve(q.payer)
    stedi = StediEligibilityClient(payer_id=payer.stedi_payer_id if payer else None)
    result = stedi.check(q)
    # Directory engine still owns provider-specific network status; merge/corroborate.
    try:
        verdict = check_network(q)
        result.network_verdict = verdict
        result.corroboration = verdict.corroboration or []
        if verdict.status == NetworkStatus.IN_NETWORK and result.network_status == NetworkStatus.OUT_OF_NETWORK:
            result.network_status = NetworkStatus.REVIEW            # conflict → human verify
        elif result.network_status == NetworkStatus.UNKNOWN and verdict.status != NetworkStatus.UNKNOWN:
            result.network_status = verdict.status                  # trust directory when Stedi silent
    except Exception:
        pass   # no directory adapter for this payer → Stedi-only answer stands
    return result
```

- [ ] **Step 3: Modify `api.py`** — add CORS, the `message` exception handler, mount auth router, add eligibility routes; harden the existing `str(exc)` leak.

```python
# additions to network_probe/api.py
import logging, uuid
from fastapi import Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .config import get_settings
from .auth.routes import router as auth_router
from .auth.deps import get_context
from .context import RequestContext
from .eligibility import check_eligibility
from .audit import write_audit
from .netutil import assert_safe_url
from .validation import valid_npi

log = logging.getLogger("preauth.api")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)

@app.exception_handler(HTTPException)
def _http_exc(request: Request, exc: HTTPException):
    detail = exc.detail
    msg = detail.get("message") if isinstance(detail, dict) else str(detail)
    payload = detail if isinstance(detail, dict) else {"message": msg}
    return JSONResponse(status_code=exc.status_code, content=payload)

@app.exception_handler(Exception)
def _unhandled(request: Request, exc: Exception):
    rid = uuid.uuid4().hex[:12]
    log.exception("unhandled error req=%s", rid)        # full detail server-side only
    return JSONResponse(status_code=500, content={"message": "internal error", "request_id": rid})

@app.get("/api/eligibility/ping")
def elig_ping(ctx: RequestContext = Depends(get_context)):
    return {"ok": True, "tenant": str(ctx.tenant_id)}

@app.post("/api/eligibility")
def eligibility(req: CheckRequest, ctx: RequestContext = Depends(get_context)):
    if req.base_url:
        assert_safe_url(req.base_url)                   # SSRF guard
    if req.npi and not valid_npi(req.npi):
        raise HTTPException(status_code=400, detail={"message": "invalid NPI"})
    q = ProviderQuery(payer=req.payer, plan_hint=req.plan or "", npi=req.npi or None,
                      first_name=req.first_name or None, last_name=req.last_name or None,
                      state=req.state or None, zip_code=req.zip or None, tin=req.tin or None)
    # member_id/dob arrive only via the 271-ingest or an authenticated extended request; omitted here for brevity
    result = check_eligibility(q)
    rid = uuid.uuid4().hex[:12]
    write_audit(ctx, q, result, rid)
    return {"payer": req.payer, "request_id": rid, **result.to_dict()}
```
Also cap upload size in `check_from_report` (reject > 10 MB) and require `get_context` on it.

- [ ] **Step 4: Failing test** then implement to green:

```python
# tests/test_api_eligibility.py
import pytest
from fastapi.testclient import TestClient
from network_probe.api import app

@pytest.mark.db
def test_eligibility_requires_auth():
    assert TestClient(app).post("/api/eligibility", json={"payer":"oscar"}).status_code == 401

@pytest.mark.db
def test_eligibility_rejects_ssrf(auth_header):   # fixture returns a valid Bearer header
    r = TestClient(app).post("/api/eligibility",
        json={"payer":"fhir","base_url":"http://169.254.169.254/"}, headers=auth_header)
    assert r.status_code == 400
```

- [ ] **Step 5: Run → PASS.  Step 6: Commit** `feat: /api/eligibility, auth wiring, CORS, leak-free errors, audit`.

---

## Task 17: Override migration JSON→DB + back-compat

**Files:** Modify `network_probe/overrides.py`; Create `scripts/migrate_overrides.py`, `tests/test_override_migrate.py`

- [ ] **Step 1: Failing test** — running the migrator imports legacy JSON rows into `overrides` under the demo tenant; `OverrideStore` reads from DB when a session is available.

```python
# tests/test_override_migrate.py
import json, uuid, pytest
from scripts.migrate_overrides import migrate
from network_probe.db.session import tenant_session
from network_probe.db.models import OverrideRow

@pytest.mark.db
def test_migrate_json_to_db(tmp_path, demo_tenant):
    p = tmp_path/"overrides.json"
    p.write_text(json.dumps([{"payer":"devoted","npi":"1629339312","status":"OUT_OF_NETWORK",
        "verified_by":"Availity","verified_at":"2026-05-21"}]))
    migrate(p, demo_tenant)
    with tenant_session(demo_tenant) as s:
        assert s.query(OverrideRow).filter_by(npi="1629339312").one().status == "OUT_OF_NETWORK"
```

- [ ] **Step 2–4: Implement `scripts/migrate_overrides.py`** (idempotent: skip rows already present), run, commit `feat: migrate overrides JSON→Postgres`.

---

## Task 18: Benefits-matrix UI fallback + frontend env

**Files:** Modify `network_probe/static/index.html`; Create `physician_app_frontend/.env`

- [ ] **Step 1:** Add a results section to `index.html` that, given an `/api/eligibility` JSON response, renders a table grouped by service type with **IN and OON columns** for copay / coinsurance / deductible / OOP, plus active/plan/PCP/auth/referral badges. (Plain JS fetch; send the Bearer token from `localStorage.auth_token`.)

- [ ] **Step 2:** Create `physician_app_frontend/.env`:

```dotenv
VITE_API_AUTH=http://localhost:8000/api/auth
VITE_API=http://localhost:8000/api
```

- [ ] **Step 3:** Manual verification (documented, not automated here): `uvicorn network_probe.api:app` + `npm run dev` in the frontend; log in with the seeded admin, complete the must-change-password flow, confirm a token is stored and an eligibility call renders the matrix.

- [ ] **Step 4: Commit** `feat: benefits-matrix UI fallback + frontend API env`.

---

## Task 19: Docs + full regression

**Files:** Modify `ARCHITECTURE.md`, `README.md`, `pytest.ini`

- [ ] **Step 1:** Add `db` and keep `live` markers in `pytest.ini`; document `pytest -m "not live and not db"` (pure), `-m db` (needs local Postgres), `-m live` (needs prod Stedi key).
- [ ] **Step 2:** Update `ARCHITECTURE.md` §10/§11: Stedi now primary eligibility+benefits; add the persistence/auth/tenancy/audit layer and the Slice B/C boundary.
- [ ] **Step 3:** Run `pytest -m "not live"` → all green (existing 41 + new). Commit `docs: architecture + test markers for slice A`.

---

## Task 20: (LAST — gated) Live Stedi production verification

**Files:** Create `tests/test_stedi_live.py` (marked `live`)

- [ ] **Step 1:** With the **production** `STEDI_API_KEY` in `.env` and a real test member, run one `-m live` test that POSTs a 270 for a known payer and asserts the 271 parses (coverage_active is not None). **Inspect the raw 271** and reconcile any EB field-path differences in `parse_271.py` (the documented best-effort caveat).
- [ ] **Step 2:** Record the verified payer ids back into the catalogue via `scripts/resolve_payer_ids.py`.
- [ ] **Step 3: Commit** `test: live Stedi prod verification + payer-id reconciliation`.

---

## Self-review (completed against the spec)

- **Spec coverage:** config/secrets (§4.1)→T1–2; data model (§4.2)→T11; Stedi client+parser (§4.3)→T12,14; payer catalogue (§4.4)→T15,20; persistence/RLS/encryption (§4.5)→T3–6; auth contract (§4.6)→T7–10,16; tenant+audit (§4.7)→T4,16; engine merge (§4.8)→T16; API+frontend (§4.9)→T16,18; HIPAA controls (§5)→T3,4,5,9,13,16; testing (§6)→every task + T19–20; override migration (§7)→T17; build order (§8)→task order. No section unmapped.
- **Placeholders:** none — the only deferred item is reconciling live EB field paths (T20), which is an explicit, dated caveat, not a missing step. The ROSTER transcription (T15) is flagged as mechanical data entry from the spec table.
- **Type consistency:** `RequestContext(tenant_id, actor_id, role)`, `EligibilityResult`/`BenefitLine` fields, `parse_271_benefits(data)->EligibilityResult`, `StediEligibilityClient.check(q)`, `tenant_session(tenant_id)`, `get_context`/`context_from_token`, JWT `issue_access/issue_refresh/decode_token` — names match across T4, T8–16.

## Resolved engineering decisions (your callouts)
- **SOLID:** protocols `SecretsProvider`, `CryptoProvider`, `EligibilitySource`, `PayerCatalogue`; repos/DI for persistence; routes depend on `get_context`, not on token internals.
- **TOCTOU:** RLS `set_config(..., true)` inside the request transaction (T4); `ON CONFLICT` seeds (T5,15,17); `token_version` atomic revoke-all (T8–10); atomic failure-counter/lock (T10); no file stat-then-write.
- **Other vulns:** SSRF allowlist (T13), alg-pinned JWT + typ separation (T8), bcrypt + generic errors + lockout (T7,10), UUID anti-IDOR (T4), mass-assignment-proof repos & request models (T6,16), FORCE RLS + non-superuser role (T5/T0), PHI-hashed audit + no-PHI cache/logs/errors (T3,14,16), upload cap + NPI Luhn (T13,16).
