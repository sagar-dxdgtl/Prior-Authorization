# Architecture — Pre-Auth Network-Status Verification Probe

## 1. What this system is

A **pre-service / pre-authorization** probe that answers one question, honestly:

> *Given a provider (NPI/name) and a payer + plan, is that provider **in-network** for that plan?*

It returns a structured verdict — **IN_NETWORK / OUT_OF_NETWORK / UNKNOWN / REVIEW** — with a
confidence level, the exact source it came from (audit trail), and cross-check signals. It runs
**before** the encounter, so there is **no claim/835 yet** — every input must be available at pre-auth.

It is the **network-accuracy module**, not a full eligibility platform. It's designed to fill the
"`Provider Network: Unknown`" field that a pVerify-style 271 leaves blank (see §10).

---

## 2. Core principles (non-negotiable)

1. **Discover endpoints empirically** — never hardcode payer APIs from memory.
2. **Never default ambiguity to OUT_OF_NETWORK** — `UNKNOWN` is the honest answer when we can't tell.
3. **Always populate `source_url`** — every verdict cites the exact endpoint(s) queried.
4. **A directory is one signal, not ground truth** — corroborate, weight confidence, escalate to REVIEW.
5. **Respect the sites** — real User-Agent, low volume, delays, dev cache; **never** bypass
   CAPTCHA / WAF / bot-protection / auth. Blocked → document and report, don't circumvent.

---

## 3. The pre-auth determination pipeline

```
  Input (NPI/name + payer + plan [+ TIN, member_id, dob, state/zip])
        │   from CLI · API · UI · or a parsed 271 report
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ service.check_network(q)                                       │
  └──────────────────────────────────────────────────────────────┘
        │
        ▼
  (A) Golden-record override?  ── yes ─▶  return human-verified verdict (confidence: high)
        │ no
        ▼
  (B) Adapter for this payer  ──▶  live directory query (the PRIMARY signal)
        │                          · resolve plan → network (alias map)
        │                          · find provider by NPI (or name) in that network
        │                          · build verdict + source_url  (IN / OUT / UNKNOWN)
        ▼
  (C) Corroboration (finalize)  ──▶  run cross-check sources, then:
        │      · IN + a contradiction      →  REVIEW (confidence: conflict)
        │      · IN single-source          →  demote high → medium  (+ directory caveat)
        │      · IN + stale signal         →  confidence → low
        │      · ambiguous                 →  UNKNOWN (never guessed OON)
        ▼
  NetworkVerdict { status, confidence, plan_or_network_checked,
                   source_url, notes, matched_provider, corroboration[] }
```

The pipeline lives in `service.check_network()` → `corroboration.finalize()`.

---

## 4. How we make the pre-auth answer trustworthy

These are the mechanisms layered on top of the raw directory lookup:

| # | Mechanism | Where | What it guards against |
|---|---|---|---|
| 1 | **Empirical discovery** | per-adapter | stale/guessed endpoints |
| 2 | **Plan → network resolution** | `plan_aliases.py` | checking the *wrong* network for a plan |
| 3 | **UNKNOWN, never guessed OON** | adapters + `finalize` | false "out of network" denials |
| 4 | **Audit trail (`source_url`)** | every verdict | unverifiable claims |
| 5 | **Identity cross-check (NPPES)** | `NppesSource` | wrong/inactive provider, name mismatch |
| 6 | **TIN-scope (group-level)** | `TinScopeSource` | "individual listed, but billing TIN is OON" |
| 7 | **Freshness** | `FreshnessSource` | stale listings (`going_oon_soon`, `last_inn_date`) |
| 8 | **Eligibility cross-check** | `StediSource` (env-gated) | directory disagreeing with the payer's 271 |
| 9 | **Confidence asymmetry** | `finalize` | over-trusting a single source (high → medium) |
| 10 | **Conflict → REVIEW** | `finalize` | asserting when sources disagree |
| 11 | **Golden-record override (MDM)** | `overrides.py` | re-deriving a known-wrong directory answer |
| 12 | **Respectful access** | `_http.py` | site abuse / bans (UA, delays, cache, no bypass) |

**Verdict states:** `IN_NETWORK` · `OUT_OF_NETWORK` · `UNKNOWN` (can't tell) · `REVIEW` (sources conflict).
**Confidence:** `high` · `medium` · `low` · `conflict`.

---

## 5. Corroboration / signal model

Each source returns a `Signal(source, result, detail)` where `result` ∈
`corroborates · contradicts · stale · inconclusive`. `finalize()` combines them with the directory verdict.

- **NppesSource** — POSTs to NPPES (`npiregistry.cms.hhs.gov/.../npiDetails`): provider exists, active,
  name/state match. *No TIN — NPPES doesn't publish it.*
- **TinScopeSource** — is the provider's **billing TIN** among the in-network TINs? In-network TINs come
  from the adapter (Oscar exposes them) or the **NPI→TIN crosswalk** (`tin_crosswalk.py`) when the
  directory doesn't. A mismatch → contradicts → REVIEW.
- **FreshnessSource** — `going_oon_soon` / `last_inn_date` → stale → confidence drops to low.
- **StediSource** — independent 270/271 eligibility cross-check via Stedi (only active when
  `STEDI_API_KEY` set; `PAYER_IDS` mapped for all 5). *Caveat: a 271's network indicator is benefit-tier,
  so it's payer-dependent / often inconclusive.*

---

## 6. Payer adapters (the primary signal)

All implement `base.PayerAdapter.check_network(q) -> NetworkVerdict`. Registered in
`service._ADAPTER_FACTORIES`.

| Key | Source | Method |
|---|---|---|
| `oscar` | Oscar private JSON API | autocomplete + per-network resolution; **exposes per-TIN data**, `going_oon_soon`, `last_inn_date` |
| `devoted` | Algolia search index | NetworkNames facet, contracting-group keys |
| `humana-fhir` / `cigna-fhir` / `uhc` | FHIR Da Vinci **PDEX Plan-Net** (public, CMS-mandated, no auth) | `Practitioner?identifier=NPI` → `PractitionerRole` → network reference |
| `fhir` (generic) | any PDEX endpoint via `base_url=` | same FHIR flow |

**Documented blockers:** Humana's & BCBS-TX's *web* directories are bot-protected (Akamai/Imperva). We
do **not** bypass them — we use Humana's compliant **FHIR** endpoint instead; BCBS-TX is reported blocked.

---

## 7. Inputs / entry points

- **271 ingest** (`domain/report_ingest.py`, `cli/ingest.py`) — drop in a pVerify eligibility PDF → parse payer,
  plan, provider NPI+name, member id/dob, state/zip → run the probe. *This is the member-keyed intake.*
- **API** (`api/app.py`, FastAPI) — `POST /api/check`, `POST /api/check-from-report` (PDF upload),
  `GET /api/payers`, `GET /api/samples`, `POST /api/override`.
- **UI** (`api/static/index.html`) — single-page form, report upload, verdict + cross-checks + audit trail.
- **CLI** (`cli/main.py`) — single check; `python -m network_probe.cli.ingest *.pdf` for batch.

---

## 8. Module map

```
src/network_probe/
  domain/
    models.py          ProviderQuery, NetworkVerdict, NetworkStatus
    service.py         check_network() — adapter dispatch + finalize
    plan_aliases.py    plan name → network resolution
    corroboration.py   Signal, sources (NPPES/TIN/Freshness/Stedi), finalize()
    overrides.py       golden-record override store (MDM)
    tin_crosswalk.py   NPI→TIN loader (staged; needs a data file)
    report_ingest.py   271 PDF → ProviderQuery
  payers/
    adapters/base.py     PayerAdapter interface
    adapters/oscar.py    Oscar (per-TIN, freshness)
    adapters/devoted.py  Devoted (Algolia)
    adapters/fhir_pdex.py Humana / Cigna / UHC / generic PDEX
  cli/
    main.py            CLI
    ingest.py          batch CLI
  api/
    app.py             FastAPI app
    static/index.html  UI
  core/
    _http.py           CachedClient (UA, delays, cache)
    config.py, context.py, crypto.py, secrets_provider.py
  auth/  db/  stedi/   auth, persistence (RLS), Stedi eligibility client
```

---

## 9. Where we already beat pVerify (protect these)

- **Automated** network-status determination — pVerify does this manually (phone / portal / Availity + notes).
- **Multi-source corroboration + REVIEW + golden-record** — pVerify resolves conflicts by hand.
- **Per-verdict audit trail** of the exact endpoint queried.

---

## 10. What pVerify does that we don't

pVerify is a full **eligibility + benefits + RCM-workflow** platform; we are the network-accuracy slice with a HIPAA-grade platform under it (Slice A shipped).

### Within the pre-auth / eligibility moment
| Capability | pVerify | Us |
|---|---|---|
| 270/271 eligibility (active?, eff/term dates) | ✅ | ✅ via Stedi clearinghouse (primary source, Slice A) |
| Benefits & cost-share (copay / coins / deductible / OOP) | ✅ | ✅ IN/OON cost-share, met-pairing, COB via `stedi/parse_271.py` |
| PCP / prior-auth / referral requirements | ✅ | ⚠️ partial (Oscar adapter; not from Stedi) |
| COB / secondary / plan sponsor / IPA | ✅ | ✅ COB parsed (PHI-redacted); secondary detail still partial |
| Broad payer reach via EDI/clearinghouse | ✅ (~all payers) | ✅ Stedi + 5-payer AZ/CO/FL/NY catalogue; web-blocked payers via FHIR |
| Member-keyed lookup (member ID + DOB → plan) | ✅ | ✅ via 271 ingest + `/api/eligibility` member key |
| **Provider network status** | ⚠️ often "Unknown", manual, error-prone | ✅ **our core** |

### Beyond pre-auth (the wider platform)
| Capability | pVerify | Us |
|---|---|---|
| Live claims-grade network truth (Availity / TIN portal / phone) | ✅ (manual) | ✅ Stedi primary + directory merge; Slice A shipped |
| Case-management workflow (notes, PEC, reschedule, retention) | ✅ | ⚠️ override store only; full case-mgmt in Slice B |
| Batch / scale / queue / retry hardening | ✅ | ⚠️ per-call sync; queue/scale in Slice B |
| Persistence / datastore / member files | ✅ | ✅ Postgres + UUID PKs + Alembic migrations (Slice A) |
| Compliance (HIPAA BAA, encryption, audit, multilingual) | ✅ | ✅ Fernet PHI-at-rest, RLS multi-tenancy, PHI-redacted audit; multilingual in Slice B |

> **The key inversion:** the one thing pVerify is *weakest* at — provider network status (its 271 returns
> "Unknown", and its automated field was wrong in 4/4 of its own OON examples) — is exactly what we do
> well and automatically. We're complementary: feed us pVerify's 271, we fill the network answer.

---

## 11. Slice A platform layer (shipped)

Slice A transformed the demo probe into a multi-tenant, HIPAA-aware eligibility + benefits service. The following are all built and committed:

### Eligibility + benefits (primary)
- **Stedi** (`stedi/client.py`) — real 270/271 clearinghouse transactions; no-PHI cache key.
- **Benefits parser** (`stedi/parse_271.py`) — extracts IN/OON cost-share, deductible met-pairing, COB info, errors; PHI-redacted before logging.
- **Benefits model** (`benefits.py`) — structured output for the UI and API.
- **Engine** (`eligibility.py`) — Stedi primary + legacy directory merge; conflict → REVIEW; tenant-scoped override application.

### Platform / data layer
- **Config** (`config.py`) — `pydantic-settings` with fail-fast PHI-crypto validation; rejects weak/default `FERNET_KEYS` and `MEMBER_ID_PEPPER` outside dev.
- **Secrets seam** (`secrets_provider.py`) — env vars in dev; AWS Secrets Manager in prod (swappable without code changes).
- **Crypto** (`crypto.py`) — Fernet field-encryption for PHI at rest; peppered HMAC for member-id lookup keys.
- **Postgres datastore** (`db/`) — SQLAlchemy models with UUID PKs; Alembic migrations; FORCE ROW LEVEL SECURITY per tenant; `SECURITY DEFINER auth_lookup_user` for pre-tenant login; tenant-scoped session + repositories.
- **Payer catalogue** (`payers/`) — seeded AZ/CO/FL/NY payer roster with Stedi payer IDs; gated resolver.
- **Audit log** (`audit.py`) — action-tagged, PHI hashed + encrypted, actor retained after user deletion.

### Auth layer
- **OAuth2 password + JWT** (`auth/`) — algorithm-pinned (HS256 + `typ` claim), `token_version` for server-side revocation, bcrypt passwords, RLS-safe login with atomic lockout + constant-time comparison.
- **Gates**: must-change-password 403 gating; `/api/auth/login`, `/api/auth/refresh`, `/api/auth/change-password`.

### API + frontend
- **API** (`api.py`) — member-keyed `/api/eligibility`; auth + SSRF guard + audit on all PHI routes; leak-free errors; CORS allowlist; quota headers; body-size + upload caps.
- **Frontend** (`web/`) — React + Vite + Ant Design; login flow + benefits matrix; `npm run dev` on port 5173.

---

## 12. Staged (not code gaps)

### Slice B — queue, scale, RBAC, ops
- **Queue / scale / retry hardening** — async task queue (Celery or similar) for batch eligibility; per-tenant rate limiting beyond in-process tokens.
- **RBAC depth** — fine-grained role permissions (e.g. read-only vs. submit-PA vs. admin); resource-level policies.
- **AWS KMS + Secrets Manager** — replace `FERNET_KEYS` env var with KMS-managed DEK; `secrets_provider.py` seam already prepared.
- **Case-management workflow** — notes, prior-auth status tracking, PEC, reschedule, retention; override store is the seed.
- **Multilingual** — i18n for member-facing outputs.

### Slice C — directory breadth + crosswalk
- **NPI→TIN crosswalk** — loader built (`domain/tin_crosswalk.py`); needs a data file (TiC parse / vendor crosswalk). No free API exists; TiC files are per-payer and huge. See `./roadmap/TODO-unblock-phase2-3.md`.
- **Additional payer directories** — expand beyond the current 5-payer catalogue; more FHIR PDEX adapters.

See `./roadmap/TODO-pverify-parity.md` for the sequential roadmap and `./roadmap/TODO-network-accuracy.md` for the
accuracy mechanisms in depth.
