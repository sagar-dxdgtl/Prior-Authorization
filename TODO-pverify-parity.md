# TODO — pVerify gap roadmap (work sequentially)

**Framing.** We built the **network-status layer** — the piece pVerify is weakest at (its 271 returns
`Provider Network: Unknown`, and its automated network field was wrong in 4/4 of its own OON examples).
pVerify is a full **eligibility + benefits** platform; we are the **network-accuracy module** that
bolts onto a 271. This doc lists what's missing to stand alone, in the order we'll build it.

Companion doc: `TODO-network-accuracy.md` (the accuracy sub-roadmap — #1–#5 already shipped).

---

## Current state (what we have)
- Live network-status probe across **5 payers**: Oscar, Devoted (scrape), Humana, Cigna, UHC (FHIR PDEX).
- Verdicts **IN / OUT / UNKNOWN / REVIEW** with confidence, audit-trail source URLs.
- Accuracy layer: NPPES identity cross-check, TIN-scope, freshness, **override/golden-record**,
  confidence/asymmetry. Corroboration shown in CLI/API/UI.
- FastAPI + single-page UI; CLI; 41 offline + live tests.
- Documented blockers: Humana & BCBS-TX **web** tools are bot-protected (we use Humana's FHIR instead).

## Where we already beat pVerify (protect these)
- Automated network-status determination (their manual phone/portal/Availity + notes step).
- Multi-source corroboration + REVIEW + golden-record override (they do this by hand).
- Per-verdict audit trail of the exact endpoint queried.

## Gap table (pVerify has → we don't)
| Area | pVerify | Us |
|---|---|---|
| 270/271 eligibility (active?, dates) | ✅ | ❌ |
| Benefits & cost-share (copay/coins/deductible/OOP) | ✅ | ❌ |
| PCP / auth / referral required | ✅ | partial (Oscar) |
| COB / secondary / plan sponsor / IPA | ✅ | ❌ |
| Member-keyed lookup (member ID + DOB → plan) | ✅ | ❌ (we need plan_hint) |
| Broad payer reach via EDI/clearinghouse | ✅ (~all) | ❌ (5, 2 web-blocked) |
| Live claims-grade network truth (Availity/TIN portal/phone) | ✅ (manual) | framework only |
| Case-management workflow (notes, PEC, reschedule, retention) | ✅ | seed (override store) |
| Batch / scale / queue / retry hardening | ✅ | ❌ (per-call + dev cache) |
| Persistence / datastore / member files | ✅ | JSON cache + JSON overrides |
| Compliance (HIPAA BAA, encryption, audit, multilingual) | ✅ | demo only |

---

## Sequential plan

### Phase 1 — 271 ingest → network verdict  ✅ DONE
**Why:** highest leverage, uses data we already have, turns us from "manual form" into "drop in the
eligibility report → get the network answer." Directly fills pVerify's `Provider Network: Unknown`.
- [x] `report_ingest.parse_report` — payer, plan name + policy type, provider NPI **and name** (parsed
      from the report, no NPPES dependency), member state/ZIP, member ID. (`pypdf`; Fed Tax ID blank.)
- [x] Payer string → adapter key (Oscar/Devoted/Humana-fhir/Cigna-fhir/UHC).
- [x] `report_to_query` + `check_network` → verdict; NPPES name fallback only if report lacks it.
- [x] API `POST /api/check-from-report` (PDF upload) + **UI upload control** (auto-fills the form).
- [x] Batch CLI `python -m network_probe.ingest test-data/*.pdf` (+ `--json`).
- [x] Tests (synthetic text, no PHI) + live verified: all 7 reports → correct verdicts
      (Ochoa/Oscar → OON high; Rodriguez & Salman → IN; rest → OON).

### Phase 2 — Live claims-grade network source  🟡 BUILT, needs prod credentials
**Why:** the only thing that makes REVIEW conflicts *auto-detected* instead of override-after-the-fact;
it's what would have caught Devoted/Li without a human.
- [x] **`StediSource`** built + unit-tested; **sandbox-verified live** (auth/endpoint/JSON). Auto-added
      to `default_sources` when `STEDI_API_KEY` is set.
- [x] `PAYER_IDS` populated for all 5 payers (OSCAR / DEVOT / 61101 / 62308 / 87726).
- [ ] Go live: **production** Stedi key (test keys = mock only) + **payer enrollment**.
- Alt: `AvailitySource` (seam ready) if Availity is preferred. See `TODO-unblock-phase2-3.md`.
- Caveat: 271 network indicator is benefit-tier → provider-specific signal is payer-dependent.

### Phase 3 — NPI→TIN crosswalk (TiC)  🟡 LOADER BUILT, needs a data file
**Why:** make TIN-scope work beyond Oscar (no public directory/NPPES publishes TIN).
- [x] **`tin_crosswalk.TinCrosswalk`** loader — reads NPI→TIN from JSON/CSV (arg or `TIN_CROSSWALK_PATH`
      env), no-ops if absent. `TinScopeSource` falls back to it when the directory has no per-TIN data.
      Tested: with a crosswalk, a member billing under a different TIN → **contradicts → REVIEW**.
- [ ] Feed it **contracted** data (pre-auth has no 835): a payer **TiC in-network file** (multi-GB;
      stream-parse to NPI→{TIN,network}) or a prebuilt **NPI→TIN** CSV. *Provider's own billing TIN is
      a known input, not a lookup; 835 is post-service (denial loop / historical map only).*
- (Detail in `TODO-network-accuracy.md` item #3.)

### Phase 4 — Member-keyed intake  ✅ DONE (via Phase 1)
**Why:** today we need the plan name; pVerify keys off member ID + DOB.
- [x] The **271 ingest is the member key** — it extracts member ID + resolves payer/plan/provider from
      the report, so no hand-typed `plan_hint`. Drop in the report → done.
- [ ] (Optional) member ID + DOB → plan via a payer **member** API — needs member-auth (out of demo scope).

### Phase 5 — Scale & persistence  ⏸ DEFERRED (not needed for demo)
- [ ] Real datastore (replace JSON cache/overrides); verdict history; NSA-style retention.
- [ ] Batch queue, concurrency, retry/backoff, rate-limit hardening per payer.
- [ ] Expand payer coverage (more FHIR PDEX endpoints; revisit bot-blocked payers via official APIs only).

### Phase 6 — Eligibility/benefits parity  ⏸ DEFERRED (not needed for demo)
- [ ] Surface benefits/cost-share (copay/coins/deductible/OOP), PCP/auth/referral, COB — i.e. the rest
      of the 271. Large; out of core "network accuracy" scope.

### Phase 7 — Compliance & ops platform  ⏸ DEFERRED (not needed for demo)
- [ ] HIPAA posture (BAA, encryption at rest/in transit, access control, PHI audit logging).
- [ ] Case-management workflow (notes + user + timestamp, review queue, patient notification), multilingual.

---

## Recommended order
**1 → 2 → 3** first (they compound and play to our strength), then **4 → 5**, with **6/7** only if the
goal shifts from "network-accuracy module" to "full pVerify replacement."
