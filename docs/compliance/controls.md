# Compliance controls — SOC 2 + HIPAA mapping

How the Prior-Authorization platform implements key SOC 2 (Trust Services Criteria) and HIPAA Security
Rule controls. Each row maps a control to the **concrete mechanism in this repo**. "Status" is honest:
**Implemented** (in code), **Operational** (needs a deployment/process step), or **Gap** (planned, see §4).

> Scope: the FastAPI backend + Postgres datastore + React frontend. PHI = subscriber identifiers
> (member id, DOB, name). Provider NPIs are **not** patient PHI.

---

## 1. HIPAA Security Rule — Technical Safeguards (§164.312)

| § | Control | Implementation | Status |
|---|---|---|---|
| (a)(1) | **Access control** — unique user ID, RBAC, minimum necessary | OAuth2 password + alg-pinned JWT (`auth/`); `users.role` (admin/reviewer/user) + `require_role`; **per-tenant Postgres row-level security** (FORCE RLS, `tenant_id = current_setting('app.tenant_id')`) on every PHI table; `tenant_session` sets the GUC per transaction; app DB role is NOSUPERUSER/NOBYPASSRLS. Admin/audit views expose only `member_id_hash`, never `*_enc`. | Implemented |
| (a)(2)(iii) | **Automatic logoff** | Short access-token TTL (30 min) + refresh; frontend clears auth on 401. | Implemented |
| (a)(2)(iv) | **Encryption/decryption (at rest)** | Fernet field-encryption of `member_id`/`dob`/`name` (`crypto.FernetCrypto`); member id additionally salted-HMAC hashed for lookup without decryption; key rotation via `MultiFernet`; **KMS-unwrapped keys** when `FERNET_KEYS_KMS=true` (`resolve_fernet_keys`). `result_jsonb`/`source_audit` are provably PHI-free (COB + payer-error redaction). | Implemented |
| (b) | **Audit controls** | Append-only `eligibility_checks` audit row per PHI access with `action` discriminator, actor, tenant, hashed member id, timestamp, request id — on **every** PHI route (eligibility / network / override / 271-PDF ingest). Logs carry the hash, never plaintext. Admin audit view (`/api/admin/audit`). | Implemented |
| (c)(1) | **Integrity** | Golden-record overrides + multi-source corroboration + `source_audit` per verdict; conflict → `REVIEW` (never a guessed answer); review workflow for human verification. | Implemented |
| (d) | **Person/entity authentication** | bcrypt (12 rounds) password verification, constant-time (dummy-hash) to defeat user enumeration; atomic account lockout; `token_version` revoke-all on password change; forced first-login password change (server-enforced 403). | Implemented |
| (e)(1) | **Transmission security** | HTTPS to Stedi/payers; **SSRF guard** (`assert_safe_url`, `not ip.is_global`) on attacker-influenced `base_url`; TLS termination in front of the API (deployment). | Implemented (TLS termination = Operational) |

## 2. HIPAA Security Rule — Administrative / Physical (§164.308 / §164.310)

| § | Control | Implementation / Note | Status |
|---|---|---|---|
| 308(a)(1) | Risk analysis / management | Adversarial security reviews per security-critical change (RLS, auth, API); honest UNKNOWN/REVIEW design; this controls doc. Formal periodic risk assessment = process. | Implemented + Operational |
| 308(a)(3) | Workforce access management | RBAC roles; per-tenant isolation. Formal joiner/mover/leaver + access reviews = process. | Implemented + Operational |
| 308(a)(5) | Security awareness, login monitoring | Lockout + audit log + quota; security training = process. | Implemented + Operational |
| 308(a)(7) | Contingency plan (backup/DR) | Postgres (RDS) automated backups + PITR — deployment config. | **Gap → Operational** |
| 308(b)(1) | **Business Associate Agreements** | Required with Stedi (clearinghouse) + the cloud provider before real-member prod traffic. | **Gap (operational)** |
| 310 | Physical safeguards | Managed cloud (AWS) shared-responsibility. | Operational |

## 3. SOC 2 — Trust Services Criteria (common criteria)

| CC | Control | Implementation | Status |
|---|---|---|---|
| CC6.1 | Logical access — authentication | OAuth2/JWT, bcrypt, lockout, constant-time, forced password change. | Implemented |
| CC6.1 | Logical access — authorization | RBAC (`require_role`) + tenant RLS; `tenant_id`/`role` from the signed token, never the request body; repos hard-bind `tenant_id`. | Implemented |
| CC6.1 | Encryption | Fernet at rest (+ KMS unwrap), HMAC-hashed member id, HTTPS in transit. | Implemented |
| CC6.6 | Boundary protection | SSRF allowlist; CORS origin allowlist; request body-size cap + upload cap; generic (leak-free) error envelope — no `str(exc)`/stack/PHI in responses. | Implemented |
| CC6.7 | Restrict information transmission | PHI never in logs/cache/JWT/error bodies/`result_jsonb`; member id hashed in logs. | Implemented |
| CC7.1 | Detect — monitoring | PHI-access audit trail; per-tenant quota counters; structured request-id-correlated logs. Central log shipping (CloudWatch) seam ready. | Implemented + Operational |
| CC7.2 | Rate limiting / abuse | DB-backed per-tenant daily/monthly quota enforcement (429). | Implemented |
| CC8.1 | Change management | Git PR workflow; Alembic versioned migrations (FORCE RLS in-migration); per-task spec+quality+security reviews; ruff/mypy + pre-commit; full test suite (pure + db) gating each merge. | Implemented |
| CC6.1 | Secrets management | `SecretsProvider` (env → AWS Secrets Manager); Stedi key/DB creds never committed (git-ignored `.env`); fail-closed config validation (refuses to boot without strong PHI-crypto secrets outside dev). | Implemented |

## 4. Known gaps / next-slice items (operational or future)

- **BAAs** with Stedi + cloud provider before any real-member prod eligibility (test/mock payers carry no real PHI). *(Operational — blocks prod traffic, not code.)*
- **KMS in prod**: store KMS-wrapped `FERNET_KEYS`, set `FERNET_KEYS_KMS=true`, pull the Stedi key + DB creds from Secrets Manager; define a **key-rotation cadence** (the code supports rotation via `MultiFernet`).
- **Backup/DR**: RDS automated backups + PITR + a documented restore runbook.
- **Centralized audit-log shipping** (CloudWatch / SIEM) + retention (HIPAA ≈ 6 years) + tamper-evidence.
- **Formal access reviews**, SSO/IdP integration, MFA.
- **Penetration test** + dependency/SCA scanning in CI.
- **Multilingual** patient-facing output; **patient-notification** delivery (email/SMS) for the review workflow.
- **Distributed rate limiting** (Redis) + async batch/roster queue for scale beyond a single process.

---

*This document reflects the implementation as of Slice B. It is an engineering control map, not a formal
SOC 2 report or HIPAA risk assessment — those require an auditor and operational evidence.*
