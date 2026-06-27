# Find-a-Doctor Network-Status Verification Probe

Given a **provider** (NPI / name) and a **payer + plan**, this queries the payer's public
"Find a Doctor" provider directory **live, per provider** and returns a structured verdict:

```
IN_NETWORK · OUT_OF_NETWORK · UNKNOWN
```

This is the *network-status enrichment* layer that sits next to 270/271 eligibility. A 271 tells you
the member is **active**; this probe tells you whether **this provider participates in this member's
plan network** — the gap raw eligibility (and tools like pVerify) miss.

> **What this is NOT:** not a bulk download, not a curated/cached provider table, not a scraper run
> on a schedule. Each call is **one live lookup** against the payer's own directory backend, scoped
> to the member's specific plan/network, with the exact source URL recorded for audit.

Implements **three adapters** behind one interface:
- **Oscar Health** (commercial/marketplace) — open private JSON directory API.
- **Devoted Health** (Medicare Advantage) — open Algolia directory.
- **FHIR PDEX Plan-Net** (generic) — the **compliant** path: any payer's CMS-mandated public
  Provider Directory API (FHIR R4). Verified live against **Humana**; one class covers any PDEX
  server (Cigna pre-wired; others via `--base-url`).

The architecture is a pluggable per-payer adapter, so new payers drop in without touching the shared
models, service, or CLI.

> **Note on Humana & BCBS-TX:** their *web* "Find a Doctor" tools are behind bot protection
> (Akamai-style sensor headers; Imperva) — documented as blockers in `docs/discovery/DISCOVERY-humana.md` /
> `docs/discovery/DISCOVERY-bcbstx.md`, and **not** scraped (see [Ethics](#ethics)). The **FHIR adapter reaches
> Humana legitimately** via its CMS Provider Directory API instead.

> **Why two payers matters:** the same provider can be in one payer's network and out of another's.
> **Kyle A Herron, MD (NPI 1679766943)** is **OUT-of-network for Oscar's** FL HMO plan but
> **IN Devoted's** FL HMO network — the probe returns opposite, correct verdicts for each. That
> plan-specificity is the whole point of the network-status layer.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # editable install (requirements.txt also works: it is just `-e .[dev]`)

# The ground-truth case (expected: OUT-OF-NETWORK)
python -m network_probe.cli \
    --payer oscar --npi 1679766943 --last-name Herron \
    --plan "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER" --state FL --zip 33409

# add --json for machine-readable output, --no-cache to force a fresh live call

# Same provider, different payer (expected: IN-NETWORK)
python -m network_probe.cli \
    --payer devoted --npi 1679766943 --last-name Herron --plan HMO --state FL --zip 33409

# Same provider, Humana via the compliant FHIR API (expected: IN-NETWORK on Medicare PPO)
python -m network_probe.cli \
    --payer humana-fhir --npi 1679766943 --last-name Herron --plan "Medicare PPO"
```

### Web UI & API

```bash
uvicorn network_probe.api:app          # then open http://127.0.0.1:8000
```
A single-page UI (payer picker, provider/plan form, color-coded determination with the source
endpoints shown as an audit trail) over a small JSON API:
- `GET /api/payers` — available payers + the fields each needs
- `POST /api/check` — `{payer, plan, npi, last_name, state, zip, year, base_url}` → verdict JSON

The API is a thin shell over `network_probe.domain.service.check_network` — same verdict logic as the CLI.

Expected:

```
  ✗ OUT-OF-NETWORK   (confidence: high)
  plan/net : Florida - HMO Standard (networkId=066, year=2026) / plan 'Silver Simple PCP Saver CSR 150'
  why      : Searched network 066 for last name 'Herron': 4 provider(s) returned (below the 10-result
             cap), none matching the target NPI 1679766943. Provider is not in this network's directory.
  source   : https://www.hioscar.com/api/get-network-plans?networkId=066...; https://www.hioscar.com/search/autocomplete/...
```

### CLI options
| flag | meaning |
|---|---|
| `--payer` | payer key (`oscar`) |
| `--plan` | plan name / hint — e.g. `"BASE SILVER CSR 150"` or `"SILVERSIMPLEPCPSAVER"` |
| `--npi` | provider NPI (preferred match key) |
| `--first-name`, `--last-name` | provider name (last name is required; NPI search isn't supported by Oscar) |
| `--state`, `--zip` | location, for plan/network scoping |
| `--year` | coverage year (default: current year) |
| `--json` | emit the full verdict as JSON |
| `--no-cache` | bypass the on-disk dev cache and hit the site live |

---

## What the verdicts mean

| Status | When it's returned |
|---|---|
| **IN_NETWORK** | Provider matched (by NPI) and the profile shows an active in-network record for the resolved network + coverage year. |
| **OUT_OF_NETWORK** | Either the matched provider's profile has no in-network record for that network/year, **or** a by-name search of the correct network returned results (proving the search worked) and the target provider was conclusively absent. |
| **UNKNOWN** | The plan couldn't be mapped to a network, no name was given, or the name search hit the directory's 10-result cap so absence couldn't be confirmed. **Ambiguity is never reported as OUT_OF_NETWORK** — a wrong OON is worse than an honest UNKNOWN. |
| **REVIEW** | An independent cross-check **conflicts** with the directory (e.g. NPPES shows the NPI deactivated, or the member's billing TIN isn't among the provider's in-network TINs). Flagged for human verification instead of asserting a possibly-wrong IN. |

`confidence` is `high` / `medium` / `low` / `conflict`; `source_url` records the exact endpoint(s)
queried. A directory is treated as **one signal, not ground truth**: a single-source IN is reported at
`medium`, cross-checks appear under "Cross-checks," and a **confirmed override** (golden record,
`POST /api/override`) wins over the live directory. See `docs/roadmap/TODO-network-accuracy.md`.

---

## How it works (Oscar)

Full reverse-engineered endpoint contract is in [`DISCOVERY.md`](./docs/discovery/DISCOVERY.md). In short:

1. **Resolve plan → network.** Oscar searches one network at a time, and a plan maps to exactly one
   network (the #1 source of wrong answers if you get it wrong). The probe enumerates the state's
   networks and matches the plan hint against each network's real plan list. The test plan resolves
   uniquely to **Florida – HMO Standard, `networkId 066`**.
2. **Search by last name** within that network (Oscar's autocomplete; name-only, no NPI search,
   **hard-capped at 10 results**).
3. **Match by NPI** (exact) against the returned providers; fall back to strict first+last name.
4. **Decide:**
   - matched → fetch the provider profile → read `offices[].network_infos` for the network + year.
   - not matched, results under the cap → OUT_OF_NETWORK (absence is conclusive).
   - not matched, cap hit → UNKNOWN (the provider might be hidden behind the cap).

No authentication, API key, cookies, or CAPTCHA are involved — Oscar's directory backend is public.
The client still sends a real User-Agent, adds a small delay between live calls, and caches responses
on disk during development (`.cache/`) to avoid hammering the endpoint.

## How it works (Devoted)

Full contract in [`DISCOVERY-devoted.md`](./docs/discovery/DISCOVERY-devoted.md). Devoted's directory is a public
**Algolia** index queried with a read-only InstantSearch key embedded in the page. The "network" is
`"<STATE> <PLANTYPE>"` (e.g. `FL HMO`, `TX PPO CSNP`). The probe resolves that from `--state` +
`--plan` (validated against the live network-name facet), then searches by **exact NPI** filtered to
the network + year — cleaner than Oscar (no name fuzz, no result cap). Found → IN_NETWORK; found only
in another Devoted network → OUT_OF_NETWORK; absent everywhere → OUT_OF_NETWORK (medium, with the
NP/PA-unlisted caveat). Devoted requires an NPI (its name search is fuzzy); without one it returns
UNKNOWN.

## How it works (FHIR PDEX Plan-Net) — the compliant adapter

Full contract in [`DISCOVERY-fhir.md`](./docs/discovery/DISCOVERY-fhir.md). Talks to a payer's **CMS Provider
Directory API** (FHIR R4, Da Vinci PDEX Plan-Net) — public and auth-free by federal mandate, so no
scraping or bot walls. `Practitioner?identifier=<NPI>` → `PractitionerRole?practitioner=<id>` → read
the network from each role's `network-reference` extension, then match `--plan` against those network
names. Confident match → IN_NETWORK; in the directory but no confident plan match → UNKNOWN (lists the
real networks); absent → OUT_OF_NETWORK. One class works for any PDEX server (`--payer humana-fhir`,
`--payer cigna-fhir`, or `--payer fhir --base-url <url>`).

## Ethics

This probe only reads **open** endpoints and the **public, compliance-mandated** FHIR APIs. It sends a
real User-Agent, keeps volume tiny, delays between live calls, and caches during dev. It does **not**
bypass authentication, CAPTCHAs, WAFs, or bot protection. Two payers (Humana, BCBS-TX) gate their web
directories behind bot management — those are recorded as **documented blockers**, not defeated, and
Humana is instead reached via its legitimate FHIR Provider Directory API. A wrong verdict is worse
than an honest `UNKNOWN`.

---

## Run the full stack

### 1. Install Python dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"      # src-layout editable install; `pip install -r requirements.txt` also works
```

### 2. Create the Postgres databases and app role

```sql
-- run as a superuser (e.g. psql -U postgres)
CREATE DATABASE preauth;
CREATE DATABASE preauth_test;
CREATE USER preauth_app WITH PASSWORD 'CHANGE_ME';
GRANT ALL ON DATABASE preauth TO preauth_app;
GRANT ALL ON DATABASE preauth_test TO preauth_app;
```

### 3. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set:

- **`DATABASE_URL`** — owner connection string (postgres superuser or role with CREATEROLE).
- **`APP_DB_URL`** — `preauth_app` connection string used at runtime.
- **`JWT_SECRET`** — at least 32 random characters.
- **`FERNET_KEYS`** — generate a fresh key:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

- **`MEMBER_ID_PEPPER`** — at least 32 random characters (distinct from the key above).
- **`STEDI_API_KEY`** — optional; leave blank to skip live eligibility calls.
- **`CORS_ORIGINS`** — set to `http://localhost:5173` for local dev (the Vite default).

### 4. Run Alembic migrations (creates schema + seeds payers + demo admin)

```bash
alembic upgrade head
```

### 5. Start the API server

```bash
uvicorn network_probe.api:app --reload
# API available at http://127.0.0.1:8000
```

### 6. Start the frontend

```bash
cd web
npm install
npm run dev
# UI available at http://localhost:5173
```

### 7. Log in

Default demo credentials (seeded by migration `0003_seed_admin.py`):

| field | value |
|---|---|
| Username | `admin` |
| Password | `ChangeMe-Admin-2026` |

**A forced password-change 403 is returned on first login** — submit a `POST /api/auth/change-password` (or use the UI prompt) before making any other authenticated calls.

---

## Tests

Test markers:

| command | what runs | requires |
|---|---|---|
| `pytest -m "not live and not db"` | pure unit + integration tests (fixture-driven, no external services) | nothing beyond `pip install -e ".[dev]"` |
| `pytest -m db` | database-layer tests (models, RLS, auth routes, repo) | local Postgres `preauth_test` with `preauth_app` role |
| `pytest -m live` | real end-to-end calls to Oscar, Devoted, Stedi | live network + prod `STEDI_API_KEY` |

```bash
pytest -m "not live and not db"   # fast, deterministic (default CI gate)
pytest -m db                       # needs local preauth_test
pytest -m live                     # needs prod Stedi key + live network
pytest                             # everything
```

- **Offline tests** replay captured responses (`tests/fixtures/`) through `httpx.MockTransport`, so
  the verdict logic is pinned against the *real* recorded data without network flakiness. They assert,
  for **both** payers, the OON and IN cases — including the cross-payer Herron case (OON on Oscar,
  IN on Devoted) — so the probe isn't just saying OON for everyone.
- **Live tests** (`-m live`) run the actual probes against Oscar and Devoted and skip gracefully if a
  site is unreachable.

---

## Project layout

```
src/network_probe/
  core/                cross-cutting infra: config, context, crypto, secrets_provider, _http
  domain/              models, benefits, eligibility, service, corroboration, overrides, ...
    models.py            ProviderQuery, NetworkVerdict, NetworkStatus   (payer-agnostic)
    service.py           payer string -> adapter dispatch                (payer-agnostic)
  payers/
    adapters/base.py     PayerAdapter interface                          (payer-agnostic)
    adapters/oscar.py    Oscar adapter — all Oscar specifics live here
    adapters/devoted.py  Devoted adapter (Algolia) — all Devoted specifics
    adapters/fhir_pdex.py Generic FHIR PDEX Plan-Net adapter (Humana/Cigna/any payer)
    catalogue.py, roster_seed.py   payer roster -> Stedi payer ids
  api/                 FastAPI app (app.py), ratelimit/netutil/validation, static UI
  cli/                 command-line entry points (main.py, ingest.py, __main__.py)
  auth/  db/  stedi/   authentication, persistence (RLS), Stedi eligibility client
tests/
  test_oscar.py        offline (fixtures) + live end-to-end
  test_devoted.py      offline (fixtures) + live end-to-end
  test_fhir_pdex.py    offline (fixtures) + live end-to-end
  fixtures/            captured real responses
docs/
  architecture.md      system architecture
  discovery/           reverse-engineered payer endpoint contracts (Oscar, Devoted, FHIR, ...)
  roadmap/             TODO-* roadmap / parity notes
```

### Adding a payer
Implement `PayerAdapter.check_network` in `src/network_probe/payers/adapters/<payer>.py`, then register
it in `src/network_probe/domain/service.py`. The models, CLI, and service stay untouched.
