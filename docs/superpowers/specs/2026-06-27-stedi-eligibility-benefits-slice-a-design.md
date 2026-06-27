# Slice A — Stedi Eligibility + Benefits (INN/OON) on a multi-tenant, HIPAA-aware base

- **Date:** 2026-06-27
- **Status:** Draft for review
- **Branch:** `feat/stedi-eligibility-benefits` (based on `main`)
- **Scope:** Slice A of a 3-slice program. This doc covers Slice A only.

---

## 1. Context & goal

We have a working **network-status probe** (5 payer adapters + corroboration + golden-record
override + 271-PDF ingest + FastAPI/UI/CLI, ~41 tests). It answers *"is this provider in-network
for this plan?"* but it is **demo-grade on the platform side**: JSON-file stores, no auth, no
tenancy, no datastore, a disk cache that would hold PHI, and `StediSource` wired only as a one-bit
(Y/N) corroboration cross-check.

**Goal of Slice A:** turn on the production Stedi key so 270/271 becomes a **primary eligibility +
benefits source**, delivering:

1. **Broad payer reach** across the practice's AZ / CO-Denver / FL-South-Florida / NY roster
   (Stedi is a clearinghouse — one integration reaches ~all payers in the roster).
2. **Out-of-network *with benefits*** — full cost-share (copay / coinsurance / deductible / OOP),
   **tagged IN vs OON**, individual + family, met + remaining, per service-type, plus
   PCP / prior-auth / referral-required and COB.
3. A **compliant base** to land it in: Postgres datastore with per-tenant row-level security,
   PHI encryption at rest, PHI-redacted audit logging, and OAuth2/JWT login wired to the existing
   `physician_app_frontend` (Quickflows.ai) React app.

### TODO-pverify-parity mapping (nothing dropped)

| TODO phase | Lands in |
|---|---|
| P1 271 ingest → verdict | ✅ done — reused as intake/member-key |
| **P2** live Stedi source | **Slice A** — Stedi goes live as primary eligibility+benefits |
| P3 NPI→TIN crosswalk | Slice C (loader built; needs data file) |
| P4 member-keyed intake | ✅ done (271 ingest) |
| **P5** scale & persistence | datastore + history + payer coverage → **Slice A**; queue/retry/rate-limit → Slice B |
| **P6** eligibility/benefits parity | **Slice A** — full cost-share + PCP/auth/referral + COB |
| **P7** compliance & ops | PHI audit log + encryption + no-PHI-to-disk + **login/auth** → **Slice A**; RBAC depth, case-mgmt workflow, multilingual, BAA → Slice B |

---

## 2. Non-goals (explicit Slice B / C boundary)

Built as **seams** in Slice A, fully delivered later:

- **Slice B:** request queue + concurrency + retry/backoff + rate-limit *enforcement*; full RBAC
  (roles/permissions matrix) beyond login; AWS KMS/Secrets-Manager *enforcement* in prod; case-
  management workflow (notes, review queue, patient notification); multilingual; DB-per-tenant
  option; SOC2 control documentation; batch/roster eligibility runs.
- **Slice C:** NPI→TIN crosswalk data ingestion (P3); additional payer directory adapters; deeper
  per-payer network-status accuracy.

Intake stays **per-member** (271 PDF or member-id+DOB form). The payer roster is the practice's
*accepted-payer list*, not a member list.

---

## 3. Architecture overview

```
  React (physician_app_frontend, Quickflows.ai)
        │  Bearer JWT  ·  VITE_API_AUTH=/api/auth ·  VITE_API=/api
        ▼
  FastAPI app
   ├─ /api/auth/*  ── OAuth2 password + JWT (login / refresh / change-password)
   │                    → resolves (tenant_id, actor) for every downstream call
   ├─ /api/eligibility ─ check_eligibility(q, ctx)         ◀── NEW primary path
   ├─ /api/check       ─ check_network(q)  (directory engine, unchanged)
   ├─ /api/check-from-report (271 PDF ingest, unchanged)
   └─ /api/payers, /api/override, ...
        │
        ▼
  Engine
   (A) StediEligibilityClient → 270 POST → 271 JSON → parse_271_benefits()
        → EligibilityResult{ active, plan, network_status, benefits[IN/OON], pcp/auth/referral, cob }
   (B) Directory network engine (Oscar/Devoted/FHIR) → NetworkVerdict   (network status, high accuracy)
   (C) merge/corroborate(A,B) → directory IN + Stedi OON ⇒ REVIEW, etc.
        │
        ▼
  Persistence (Postgres 18, RLS by tenant_id)
   tenants · users · payers(catalogue) · eligibility_checks(history+audit) · overrides
   PHI columns Fernet-encrypted at rest; member_id also stored hashed for lookup
        │
        ▼
  Audit log (PHI-redacted/hashed) — Postgres now, CloudWatch seam for Slice B
```

**No PHI to disk:** the Stedi client and any 271-bearing path bypass the `.cache/` disk cache
(in-memory/TTL only). The disk cache remains for *public, non-PHI* directory responses.

---

## 4. Components

### 4.1 Config & secrets (`network_probe/config.py`)
- `pydantic-settings` `Settings` loaded from env / git-ignored `.env`.
- Keys: `DATABASE_URL`, `STEDI_API_KEY`, `STEDI_ELIGIBILITY_URL`, `JWT_SECRET`, `JWT_ACCESS_TTL`,
  `JWT_REFRESH_TTL`, `FERNET_KEYS` (rotation list), `AWS_DEFAULT_REGION`, CORS origins.
- **Secrets seam** `secrets.py`: `get_secret(name)` returns from env locally; when AWS creds are
  present, resolves from **Secrets Manager** (`preauth/*`). Fernet data-key can be wrapped by **KMS**
  (`alias/preauth-phi`) when configured, else a local key. App runs fully **without** AWS.

### 4.2 Data model (`network_probe/benefits.py`)
```python
class Network(str, Enum): IN = "IN"; OON = "OON"; UNKNOWN = "UNKNOWN"
class BenefitCategory(str, Enum): COPAY; COINSURANCE; DEDUCTIBLE; OOP_MAX; LIMITATION
class CoverageLevel(str, Enum): INDIVIDUAL; FAMILY; UNKNOWN

@dataclass
class BenefitLine:
    service_type: str            # STC, e.g. "30","98"
    service_type_label: str
    network: Network
    category: BenefitCategory
    level: CoverageLevel
    amount: Optional[Decimal]    # dollars (copay/deductible/oop)
    percent: Optional[Decimal]   # coinsurance
    time_period: Optional[str]   # calendar year / remaining / visit
    met: Optional[Decimal]
    remaining: Optional[Decimal]
    raw_codes: dict              # EB01/EB06/etc. for audit

@dataclass
class EligibilityResult:
    coverage_active: Optional[bool]
    plan_name: Optional[str]
    group: Optional[str]
    coverage_dates: dict          # eff/term
    network_status: NetworkStatus # merged directory + Stedi indicator
    benefits: list[BenefitLine]   # every line tagged IN or OON
    pcp_required: Optional[bool]
    prior_auth_required: Optional[bool]
    referral_required: Optional[bool]
    cob: Optional[dict]           # secondary payer / plan sponsor / IPA
    network_verdict: Optional[NetworkVerdict]   # from directory engine
    corroboration: list[dict]
    source_audit: dict            # endpoints + request id, no PHI
```

### 4.3 Stedi eligibility client + 271 parser (`network_probe/stedi/`)
- `StediEligibilityClient.check(q)` — POST 270 to
  `https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3`,
  `Authorization: <STEDI_API_KEY>`. Provider = rendering/billing NPI; subscriber = member_id + DOB
  (or name). `encounter.serviceTypeCodes` = configurable default set.
- `parse_271_benefits(data) -> EligibilityResult` — walks `benefitsInformation`. X12 EB mapping
  (verify against a real 271 when the prod key lands — same caveat as today's code):
  - EB01: `1`=Active, `6`=Inactive, `B`=Copay, `A`=Coinsurance, `C`=Deductible, `G`=OOP(stop-loss),
    `F`=Limitation; PCP/auth/referral from EB + message segments.
  - `coverageLevelCode` → individual/family; `inPlanNetworkIndicatorCode` Y/N → `Network`;
    `timeQualifierCode` (23=calendar yr, 29=remaining) → `time_period`/`remaining`;
    `benefitAmount`/`benefitPercent` → amount/percent.
  - `errors`/AAA reject → `coverage_active=None`, `network_status=UNKNOWN` (never guessed OON).
- **OON-with-benefits is intrinsic:** both Y and N tiers are parsed, so OON `BenefitLine`s are
  populated whenever the payer returns them.

### 4.4 Payer catalogue (`network_probe/payers/` + `payers` table)
- Data-driven map: roster entry → Stedi `tradingPartnerServiceId` + `{benefit_type, state,
  enrollment_status, network_indicator_supported}`.
- Seeded from the AZ/CO/FL/NY roster + the 5 known IDs (Oscar/Devoted/Humana 61101/Cigna 62308/UHC
  87726). Ships `scripts/resolve_payer_ids.py` to fill the rest from Stedi's payer-search API once
  the prod key is in (records confidence + needs-enrollment).
- Drives the payer picker and 270 routing; per-tenant overrides allowed.

### 4.5 Persistence (`network_probe/db/`)
- SQLAlchemy 2.x models + **Alembic** migrations. Tables (all carry `tenant_id`):
  - `tenants(id, name, slug, created_at)`
  - `users(id, tenant_id, email, username, password_hash, role, must_change_password, ...)`
  - `payers(id, tenant_id?, key, label, benefit_type, state, stedi_payer_id, enrollment_status)`
  - `eligibility_checks(id, tenant_id, actor_id, payer_key, member_id_hash, member_id_enc,
     dob_enc, npi, status, result_jsonb, source_audit, created_at)` — history **and** audit row.
  - `overrides(...)` — migrated from `.overrides/overrides.json`.
- **Row-level security:** every query runs with `SET LOCAL app.tenant_id = :tid`; RLS policies
  restrict rows to that tenant. Enforced in a session dependency.
- **PHI encryption:** `member_id`, `dob`, name fields stored Fernet-encrypted (`*_enc`); `member_id`
  also stored as salted hash (`member_id_hash`) for lookup without decryption. Keys via `secrets`
  seam (local Fernet now, KMS-wrapped later).

### 4.6 Auth (`network_probe/auth/`) — implements the frontend contract
- `POST /api/auth/login` — `OAuth2PasswordRequestForm`; verify bcrypt (`passlib`); issue JWT access
  (`JWT_ACCESS_TTL`, default 30 min) + refresh (default 14 d) via `pyjwt`. Response shape matches
  `authService.handleLoginResponse`: `{access_token, expires_in, refresh_token, user}`; first login
  returns `{must_change_password:true, tokens:{access}, user}`.
- `POST /api/auth/refresh` — `{refresh_token}` → `{access_token, expires_in}`.
- `POST /api/auth/change-password/` — Bearer; bcrypt update; clears `must_change_password`.
- JWT claims carry `tenant_id`, `sub` (user id), `role`. A `get_context()` dependency decodes the
  Bearer token → `RequestContext{tenant_id, actor_id, role}` used by every protected route + audit.
- **Quota/rate-limit headers:** middleware emits `x-ratelimit-*` / `x-quota-*` (per-tenant counters;
  basic now, enforcement in Slice B) so the existing UI renders them.
- Seed: an Alembic data-migration creates a demo tenant + admin user (`must_change_password=true`).

### 4.7 Tenant context + audit logging (`network_probe/audit.py`)
- Every eligibility/network/override call writes an audit event: `tenant, actor, action, payer,
  member_id_hash, npi, result_status, source endpoints, request_id, ts`. **No plaintext PHI** in
  logs (hash/redact). Persisted to `eligibility_checks` (+ structured logger); CloudWatch seam later.

### 4.8 Engine integration (`network_probe/service.py`)
- New `check_eligibility(q, ctx) -> EligibilityResult`: Stedi primary → benefits + network indicator;
  if a directory adapter exists for the payer, also run `check_network` and **merge/corroborate**
  (directory IN + Stedi OON ⇒ REVIEW; agreement ⇒ raise confidence). `StediSource` cross-check kept.
- `check_network(q)` unchanged. Combined results persisted with tenant/actor.

### 4.9 API + frontend integration
- New `POST /api/eligibility` (Bearer-protected) returns `EligibilityResult`. Existing routes gain
  the `get_context` dependency. CORS configured for the Vite dev origin.
- `physician_app_frontend`: set `VITE_API_AUTH=/api/auth` and `VITE_API=/api` (`.env`), so the
  existing LoginPage works unchanged against our backend. Benefits-matrix UI page is a **follow-on**
  (offered with a browser mockup when we design it) — Slice A ships the API + the existing
  `static/index.html` extended to render the matrix as a fallback.

---

## 5. Security / compliance posture (HIPAA mapping)

| Control | Slice A implementation |
|---|---|
| Encryption in transit | HTTPS to Stedi/payers; TLS terminate in front of FastAPI (prod) |
| Encryption at rest | Fernet on PHI columns; Postgres on encrypted volume (RDS default); KMS seam |
| Access control | OAuth2/JWT login; tenant-scoped RLS; role claim (RBAC depth = Slice B) |
| Audit (§164.312(b)) | append audit row per access incl. actor/tenant/ts/action; PHI hashed |
| Minimum necessary | no PHI in logs/cache; member_id hashed for lookup; decrypt only on demand |
| Integrity | golden-record overrides; corroboration; source_audit per verdict |
| Secrets | env→Secrets Manager; Stedi key & DB creds never committed (git-ignored `.env`) |

SOC2 controls touched (documented in Slice B): change mgmt (migrations), access (RBAC), audit
logging, encryption, secrets management.

---

## 6. Testing strategy

- **Unit / fixture (no PHI, synthetic 271s):** `parse_271_benefits` across INN+OON, individual+
  family, met/remaining, multiple STCs, active/inactive, AAA reject; payer-catalogue resolution;
  merge/corroboration logic; JWT issue/verify; Fernet round-trip; RLS isolation (tenant A cannot
  read tenant B).
- **Integration (local Postgres):** migrations apply; eligibility check persists + audit row; auth
  login/refresh/change-password; override migration JSON→DB.
- **Live (deferred to "at last"):** real Stedi prod key — one gated `-m live` test confirming a real
  271 parses; verify EB field paths against the live response.
- Keep existing 41 tests green.

---

## 7. Data migration
- One-off: read `.overrides/overrides.json` → insert into `overrides` table under the demo tenant.
  Keep JSON read path as a fallback until cutover.

## 8. Build order within Slice A
1. Config/secrets seam + `.env.example` + deps.
2. DB layer: SQLAlchemy models + Alembic + RLS + Fernet + local Postgres wiring.
3. Auth (login/refresh/change-password + JWT + context dep) → frontend login works.
4. Benefits model + Stedi client + `parse_271_benefits` (fixtures).
5. Payer catalogue + resolver script.
6. `check_eligibility` + merge/corroboration + audit persistence.
7. `POST /api/eligibility` + UI matrix (fallback in index.html) + frontend `.env`.
8. Override migration + full test pass.
9. (Last) live Stedi prod-key verification.

## 9. Dependencies added
`sqlalchemy`, `alembic`, `psycopg[binary]`, `cryptography`, `pydantic-settings`, `pyjwt`,
`passlib[bcrypt]`, `boto3`. (Python-only; no new language.)

## 10. Open decisions / defaults (veto any)
- Default service-type codes for the 270: `30` (general), `98` (office visit), plus specialist/
  professional — configurable per check.
- JWT TTLs: access 30 min, refresh 14 d.
- Benefits-matrix UI lives in `physician_app_frontend` (new page) **and** a fallback in
  `static/index.html`; full React page treated as follow-on.
- Demo seed tenant/admin created by migration for local testing.

## 11. Risks
- **Stedi 271 field paths** vary by payer; parser is best-effort until validated live (mitigated by
  honest UNKNOWN + the deferred live test).
- **Payer-id coverage / enrollment**: some roster payers need enrollment before real eligibility;
  catalogue records `enrollment_status` and the engine degrades to UNKNOWN, never guessed.
- **271 network indicator is benefit-tier** — provider-specific OON stays the directory engine's job;
  Stedi corroborates, doesn't override network status.

---

## 12. Revision — post-review hardening (2026-06-27)

An adversarial review (security/TOCTOU + correctness + HIPAA-coverage critics) found a critical flaw
and several real gaps. These supersede the relevant parts above:

- **Frontend = our own app.** We build **our own React + Vite + Ant Design app in this repo (`web/`)**
  with **our own login page**, visually modeled on `physician_app_frontend` but **not connected to it**
  (that repo is design/contract reference only). It hosts login + the benefits-matrix screen.
- **Auth (critical fix).** The pre-tenant username lookup must NOT run as the `NOBYPASSRLS` app role
  against `FORCE RLS users` (it returns zero rows → all logins fail). Use a **`SECURITY DEFINER`
  Postgres function `auth_lookup_user(username)`** (owned by the migration owner) that returns only
  `id, tenant_id, password_hash, role, token_version, must_change_password, failed_logins,
  locked_until`. **Login key = globally-unique username** (`UNIQUE (lower(username))`); tenant is
  derived from the returned row (no tenant selector needed; resolves the cross-tenant ambiguity).
  Lockout writes happen inside `tenant_session(tenant_id)` after the lookup. Lockout increment is a
  **single atomic UPDATE**; unknown usernames still run a **dummy bcrypt verify** (constant-time, no
  enumeration). `must_change_password` is **enforced server-side**: `get_context` returns **403** for
  any non-change-password route while the flag is set.
- **Config fails closed.** `Settings` validators require **≥1 valid Fernet key** and a **strong,
  non-default `MEMBER_ID_PEPPER` (≥32)** unless `APP_ENV in {dev,test}`; `.env.example` ships a real
  generated Fernet key. The app refuses to boot misconfigured rather than 500-ing per request.
- **PHI-free `result_jsonb`/`source_audit` (real invariant).** The raw 271 `coordinationOfBenefits`
  and payer AAA/error payloads can echo subscriber identifiers, so the parser **redacts** them —
  `cob` keeps only non-PHI fields (payer/sponsor/IPA names, sequence); errors keep only codes + a
  fixed description. PHI lives only in the encrypted `*_enc` columns.
- **Audit covers every PHI route.** `eligibility_checks` gains an **`action`** discriminator
  (`eligibility|network|override|report_ingest`) and a **`name_enc`** column; `/api/check`,
  `/api/override`, and the **271-PDF** `/api/check-from-report` each write an audit row.
- **Existing routes hardened.** `/api/check` and `/api/check-from-report` now **require auth**,
  **SSRF-guard `base_url`**, **cap upload size**, and **never return `str(exc)`** (generic message +
  server-logged `request_id`).
- **SSRF guard is real.** `assert_safe_url` rejects on `not ip.is_global` (covers RFC1918, loopback,
  link-local incl. 169.254.169.254, unspecified, IPv4-mapped); the **validated `base_url` is threaded
  into the actual outbound call**; the FHIR client runs with **`follow_redirects=False`** and
  re-validates any redirect target. (Connect-time IP-pinning for full DNS-rebind defense is noted as a
  Slice-B hardening.)
- **`met` is real.** Computed by pairing a calendar-year total line with the matching `remaining` line
  per `(category, network, level)`; left `null` when unpaired (honest, not faked).
- **Member-keyed flow wired.** The eligibility request model carries `member_id, dob, first_name,
  last_name`; they populate `ProviderQuery` (DOB validated), are encrypted at rest, and never logged.
- **Tests run clean.** `tests/conftest.py` sets required test env + DB fixtures (seed tenant/admin/
  payers, `auth_header`); the per-commit gate is **`pytest -m "not live and not db"`**; the `db`
  marker is registered up front.

The implementation plan (`docs/superpowers/plans/2026-06-27-stedi-eligibility-benefits-slice-a.md`)
is rewritten to v2 to reflect all of the above.
