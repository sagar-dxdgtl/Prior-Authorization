# Unblocking Phases 2 & 3 — what's needed

Both are **drop-in** once the access/data arrives — the code seams already exist, no rework.
See `TODO-pverify-parity.md` for where these sit in the roadmap.

| Phase | Blocker type | Give me | I build |
|---|---|---|---|
| 2 — live network source | **Stedi** (recommended) | a **production Stedi API key** + payer enrollment | `StediSource` (built ✓) — already wired |
| 2 — alt | credentials | Availity **Client ID + Secret** (Coverages API) | `AvailitySource` → auto-REVIEW on conflict |
| 3 — NPI→TIN crosswalk | data/file | a payer **TiC index URL**, or **835/claims**, or an **NPI→TIN file** | `tin_crosswalk` feeding `TinScopeSource` |

> **Phase 2 update — `StediSource` is built** (`network_probe/corroboration.py`). Sandbox verified with a
> test key: auth (`Authorization: <key>`), endpoint
> (`https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3`), and JSON
> response all work. It auto-activates when `STEDI_API_KEY` is set. **`PAYER_IDS` is now populated**
> for all 5 payers (Oscar `OSCAR`, Devoted `DEVOT`, Humana `61101`, Cigna `62308`, UHC `87726` — all
> eligibility-SUPPORTED). **To go live now only needs:** (1) a **production** key (test keys hit mock
> payers only), and (2) **payer enrollment** for real eligibility. Set the key via env, never commit it.

---

## Phase 2 — Live claims-grade network source
**Goal:** an independent source that says IN/OON at the network (ideally TIN) level, so a conflict with
the directory auto-flags **REVIEW** instead of needing a human override (the Devoted/Li case).
Blocker is **access/credentials**, not code (`corroboration.CorroborationSource` seam is ready).

### Option A — Stedi (RECOMMENDED — built ✓, API-first, free 270/271)
- **Provide:** a **production Stedi API key** (self-serve at stedi.com; test key already validated the
  plumbing). For real eligibility: **payer enrollment** + `PAYER_IDS` mapping.
- **Where:** stedi.com → sign up → API key. 270/271 eligibility is free across tiers; mock requests free.
- **Status:** `StediSource` is implemented and unit-tested; sandbox call confirmed live. Drop in a prod
  key (env `STEDI_API_KEY`) + payer IDs and it activates in the corroboration pipeline.
- **Caveat:** a 271's network indicator is benefit-tier; provider-specific network status is
  payer-dependent, so expect `inconclusive` for some payers (same limit as any eligibility feed).

### Option B — Availity (what pVerify uses)
- **Provide:**
  - An **Availity account** (Availity Essentials is free for provider orgs).
  - A registered **application** on the Availity developer portal → **Client ID + Client Secret**
    (OAuth2 client-credentials).
  - The **Coverages (eligibility & benefits) API** product enabled on that app (where in/out-of-network
    indicators come back).
  - Possibly **per-payer enrollment** (some payers need trading-partner setup first).
- **Where:** developer.availity.com → register org (they verify a real healthcare entity via Tax ID) →
  create app → copy creds. Sandbox creds are fine to start.
- **I build:** `AvailitySource` — OAuth2 token → Coverages call for member+provider → read network
  indicator → `corroborates`/`contradicts` → conflict becomes REVIEW automatically.
- **Caveat:** Availity rides the same 271 rails, so some payers still return "unknown"; value is the
  payers where it *does* return OON (as in the pVerify OON-examples doc).

### Option C — Payer TIN-level portal (e.g. Cigna "Network Status")
- **Provide:** per-payer **provider-portal login** credentials.
- **Caveat:** login-gated, sometimes bot-protected (same wall as Humana/BCBS-TX web). Per-payer and
  fragile — only worth it for one must-have payer.

### Option D — Other clearinghouse 271 (Optum-Change / Waystar)
- **Provide:** account + **submitter/trading-partner ID** + connectivity (API or SFTP).
- **Caveat:** heaviest setup. (Stedi already covers this need more simply.)

➡️ **Fastest path:** Stedi production key (Option A) — the source is already built.

---

## Phase 3 — NPI→TIN crosswalk
**Goal:** know the TIN(s) a provider bills under so TIN-scope works on every payer, not just Oscar
(no public directory or NPPES publishes TIN). Blocker is a **source file** — data is public but huge.
`TinScopeSource` already consumes per-TIN data; it just needs feeding.

**Provide ANY one of:**
1. **A payer's Transparency-in-Coverage (TiC) index URL** — public, no auth; usually in the payer's
   site footer ("machine-readable files / Transparency in Coverage"). The in-network rate file has
   `provider_groups` with **`npi: [...]` + `tin: {type:"ein", value:"…"}`**.
   - *Caveat:* files are often **1–50+ GB** `.json.gz`; I'd stream-parse (`ijson`), filter to our NPIs,
     and build a local `NPI → {TIN, network}` index (SQLite). A real ingestion job.
2. **OR claims / 835 ERA data** you already have — billing TIN is right there per encounter. **Simplest**
   (no GB downloads).
3. **OR a prebuilt NPI→TIN file** (CSV/JSON) from an internal source or data vendor — I just load it.

**Built ✓:** `tin_crosswalk.TinCrosswalk` — reads NPI→TIN from JSON/CSV (arg or `TIN_CROSSWALK_PATH`
env), no-ops if absent; `TinScopeSource` falls back to it when the directory has no per-TIN data.
**Only needs a data file** to activate — same staging as `StediSource`.

Accepted formats: JSON `{"<payer>":{"<npi>":["<tin>"]}}` · JSON list `[{payer,npi,tin}]` · CSV
`npi,tin[,payer]` (blank payer = any).

### Pre-auth note — which TIN comes from where
This tool runs **pre-service / pre-auth**, so there is **no 835/claim yet** for the encounter. Two TINs:
- **Provider's billing TIN** (`q.tin`) — a **known input** at pre-auth (the practice's W-9 / PM config);
  not a lookup. (This is why the 271's blank "Fed Tax ID" doesn't block us.)
- **Payer's contracted in-network TIN set** — the thing we must **source** to check the billing TIN
  against. Pre-auth sources only: **payer directory** (Oscar, live) and **TiC** (contracted, published
  before payment). **835 is NOT a pre-auth source** — it's post-adjudication. It belongs to the
  post-service denial loop (feeds the golden-record override) or offline historical NPI→TIN reference.

So feed the crosswalk **contracted** data (TiC / directory), not 835.

### How to obtain the contracted data (we don't have it)
**Reality:** no free, clean, ready-made NPI→TIN file exists. NPPES/directories omit TIN by design.
The only authoritative *public* source is TiC; everything clean is huge, paid, or DUA-gated.

1. **TiC machine-readable files — free & public, but up to ~1 TB.** In-network JSON has
   `provider_groups` → `npi:[...]` + `tin:{type:"ein",value}`. Index URLs:
   - Cigna: https://www.cigna.com/legal/compliance/machine-readable-files
   - Humana: https://developers.humana.com/cost-transparency
   - UnitedHealthcare: https://transparency-in-coverage.uhc.com/
   - Oscar / Devoted: site footer → "machine-readable files / Transparency in Coverage"
   - *Approach:* index → pick ONE in-network file for a target plan/region → stream-parse (`ijson`)
     filtering to our NPIs. Feasible for one targeted plan; not "download everything."
2. **Commercial crosswalk — paid, ready-made.** Turquoise Health / Serif Health derive NPI↔TIN from
   TiC. Fastest for production; costs money.
3. **CMS MD-PPAS (via ResDAC) — built on NPI↔TIN**, but needs a **Data Use Agreement** (research
   access, not a quick download).
4. **835 / claims ERA — historical reference only, NOT pre-auth.** Past remits reveal which TIN an NPI
   bills under, but that's offline map-building (no claim exists at pre-auth). We don't have it now.
5. **eintaxid.com — free EIN search**, but org-name→EIN and partial; not NPI-keyed.

**Payer "search by EIN/Name" tools (e.g. anthem.com/machine-readable-file/search) are NOT provider
lookups.** The EIN is the *employer/plan-sponsor's*, and results are *file download URLs* (the Table of
Contents) — you still download + parse the large In-Network JSON for NPI+TIN. It's a file locator in
front of the same bulk TiC path, not a free provider-TIN search.

**Free-API check (2026-06): none exists.** No freemium API returns NPI→TIN by query without a bulk
download. APIs are all **paid** (PayerPrice, Payerset, Serif, Turquoise). The only free queryable
option is **DoltHub's community price-transparency SQL DB** — but coverage is partial (hospital/
procedure-focused, not comprehensive payer in-network NPI→TIN) and it's Dolt/SQL, not a clean REST API.

➡️ **Decision (2026-06): leave the loader staged** — no free API and TiC bulk files are too large to
host for now. Feed it later from a **paid vendor crosswalk** or a **targeted TiC slice** (contracted
data) when space allows. TIN-scope keeps working live on Oscar in the meantime.
Sources: [Cigna MRF](https://www.cigna.com/legal/compliance/machine-readable-files) ·
[UHC MRF](https://transparency-in-coverage.uhc.com/) ·
[Humana cost transparency](https://developers.humana.com/cost-transparency) ·
[MD-PPAS / ResDAC](https://resdac.org/cms-data/files/md-ppas) ·
[NPI↔TIN background (trekhealth)](https://www.trekhealth.io/resources/npi-to-tin-mapping-connecting-clinical-and-financial-identity-in-transparency-in-coverage-data)

---

## Notes
- Phase 2 source (`StediSource`) is **built** and sandbox-verified; it's gated only by a **production
  key + payer enrollment + PAYER_IDS mapping**. Phase 3 is gated by **a data file**. Neither is a code gap.
- `StediSource` reads `STEDI_API_KEY` from env and is added to `default_sources` only when that env var
  is set, so it has zero impact until configured. Never commit the key.
