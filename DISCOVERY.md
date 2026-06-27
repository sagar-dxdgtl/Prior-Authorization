# DISCOVERY.md — Oscar Health "Find a Doctor" endpoint contract

**Discovered:** 2026-06-22, by live inspection of `https://www.hioscar.com` (guest / not logged in),
DevTools-style network capture via a real browser session.
**Payer:** Oscar Health · **Coverage year:** 2026 · **State:** FL

> This file is the contract the probe is built against. Everything below was observed live,
> not assumed. Raw response captures are in `./.discovery/` as evidence.

---

## TL;DR

- **No documented public API, but the private backend endpoints are wide open** — no auth, no API
  key, no CSRF token, no cookies, no Cloudflare/Akamai/CAPTCHA challenge. Verified by re-fetching
  with `credentials: 'omit'` → all 200. A plain `httpx` client will work.
- Provider search is **name-only** and **strictly scoped to one network at a time** (`network_id`).
- The authoritative participation signal is **per-office, per-network, with date ranges**, exposed
  on the provider-profile endpoint as `offices[].network_infos[]` and the resolved
  `offices[].in_network`.
- **Ground truth reproduced:** Kyle Herron (NPI `1679766943`) does **not** appear in the
  Florida-HMO-Standard network (`066`) provider directory → **OUT_OF_NETWORK**. ✓
- **Positive control reproduced:** Jessica L Herron (NPI `1568741320`) *is* in `066` for 2026
  (`network_infos` record `provider_network_id=066, in_network=true, 2026-01-01..null`) →
  **IN_NETWORK**. ✓

---

## Protection / bot-defense assessment

| Check | Result |
|---|---|
| `Authorization` / `x-api-key` header required | **No** |
| CSRF / anti-bot token required | **No** |
| Cookies / session required | **No** (re-tested with `credentials:'omit'` → 200) |
| Cloudflare / Akamai / WAF challenge | **None seen** |
| CAPTCHA | **None** |
| Rate limiting | Not hit at our tiny volume; respect it anyway (UA + delay + cache) |

Request headers the browser sent (nothing privileged):
```
user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ... Chrome/149 Safari/537.36
accept: application/json
referer: https://www.hioscar.com/search/...
sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform   (standard client hints)
```
**Conclusion: not blocked. Proceed.** (Still: real User-Agent, small volume, delay, dev cache.)

---

## The scoping problem (most important correctness issue)

Oscar does **not** search "all of Oscar." You must pick ONE network, and a member's plan maps to
exactly one network. The same provider can be in one Oscar network and out of another. Getting the
network wrong is the #1 way to produce a wrong verdict.

### How the UI funnels to a network (the path we walked)
`/care-options` → "Search network" → `/search/networks/` select:
**Coverage year → Network partner (Oscar) → Coverage area → Network type → Plan**
→ lands on `/search/?networkId=...&state=...&year=...&policyId=...&formularyPlanType=...`

### Florida coverage areas (networks) offered for 2026
- `Florida - EPO Off-Exchange`
- `Florida - HMO Broad`
- **`Florida - HMO Standard`**  ← the test plan lives here (HMO Open Access, no referrals)

### Test-case mapping (confirmed live)
| Field | Value |
|---|---|
| Plan label in CLAUDE.md | `BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER` |
| Oscar's plan name | **`Silver Simple PCP Saver CSR 150`** |
| Coverage area / network | **`Florida - HMO Standard`** → `network_id` = **`066`** |
| Network type | `HMO Open Access (Standard network, no referrals required)` |
| `policyId` | `e9d56277-eae0-46f9-865a-e90c7573a0e8` |
| `formularyPlanType` | `INDIVIDUAL_6_TIER` |

---

## Endpoints (all `GET`, host `https://www.hioscar.com`, all returned 200 w/o auth)

### 1. Networks list — `/search/api/v2/networks`
Top key: `networkDetailsByYear` (networks keyed by year). Used to enumerate valid `networkId`s.

### 2. Network details — `/search/api/v2/network-details`
`?coverageArea=FL&networkId=066&year=2026` — metadata for one network.

### 3. Plans in a network — `/api/get-network-plans`  ← resolves plan → networkId
`?networkId=066&planYear=2026&state=FL`
Returns `{ "plans": [ { "tier": "Silver", "options": [ [policyId, planName, formularyPlanType], ... ] }, ... ] }`.
The test plan appears here as:
```
["e9d56277-eae0-46f9-865a-e90c7573a0e8", "Silver Simple PCP Saver CSR 150", "INDIVIDUAL_6_TIER"]
```
→ Because this option is in the `networkId=066` plan list, the plan belongs to network `066`.
**Probe resolution strategy:** given a plan name + state + year, scan candidate FL networks'
plan lists and find which network contains the plan → that's the `networkId` to search.

### 4. Provider name search (typeahead) — `/search/autocomplete/multientity/`  ★ search entry point
`?network_id=066&categories=2&query=Herron&state=FL&year=2026`

- **`query`** = free-text **name** (NOT NPI — querying an NPI returns 0 results).
- **`network_id`** = the network to search within (REQUIRED scoping).
- **`categories`** = entity types. **`2` = providers.** `2,4,5,6,8` is the UI default and mixes in
  facilities/drugs (drug noise on fuzzy matches). **Use `categories=2` for provider lookups.**
- **`state`**, **`year`** = scope.
- **HARD CAP: 10 results.** `limit`/`size`/`pageSize` params are ignored. (`Garcia` → 10 even with
  `limit=50`; `Herron` → 4.) **This cap is central to the verdict logic — see below.**

Response shape (providers are `group_type == 2`):
```json
{"results": [
  {"entity_id": "8oHqRKWUYDqgNR", "group_type": 2, "display_name": "Jessica L Herron",
   "response_fields": {"doctor_name_fields": {
     "first_name": "Jessica", "middle_name": "L", "last_name": "Herron",
     "npi": "1568741320", "primary_specialty": "Mental Health Nurse Practitioner",
     "office_ids": ["ZdHkmyt6z9kNozPUGPmK"], "tenant_id": "oscar"}}}
], "request_id": "..."}
```
- **Provider identity:** `response_fields.doctor_name_fields.npi` (string), `.first_name/.last_name`.
- **`entity_id`** is the key to fetch the full profile (endpoint 5).

`Herron` in `066` returns exactly 4 providers — **Kyle Herron (1679766943) is NOT among them:**
| name | npi |
|---|---|
| Jessica L Herron | 1568741320 |
| Shaidah J. Herron | 1326739715 |
| Thomas Joseph Herron | 1013237007 |
| Michael Herron | 1700867207 |

### 5. Provider profile (authoritative participation) — `/api/provider-profile/legacy-initial-data-api/{entity_id}`  ★ verdict source
`?networkId=066&planYear=2026&state=FL&zip_code=33409`

Returns `{reactProps, reduxState}`. Provider lives at
`reduxState.providerProfile.provider`:
- identity: `npi`, `first_name`, `last_name`, `provider_id` (== entity_id), `specialties`
- **`offices[]`** — each office carries the network signal:
  - **`in_network`** (bool) — *resolved* for the queried `networkId` + current date.
  - **`network_infos[]`** — the raw, authoritative records. Each:
    ```json
    {"provider_network_id": "066", "in_network": true, "tin": "274322240",
     "network_source": "2019_fl_optum_p",
     "start_date": {"year":2026,"month":1,"day":1}, "end_date": null}
    ```
    → Filter to `provider_network_id == <target networkId>`, pick the record whose
    `[start_date, end_date]` covers the query date (end_date `null` = open) → read `in_network`.
  - `visibility_infos[]` — per-network display visibility flags.
  - `tier_infos`, `tier_label`, `last_inn_date`, `going_oon_soon` — supporting signals.

For Jessica (entity `8oHqRKWUYDqgNR`), office `network_infos` has
`{provider_network_id:066, in_network:true, 2026-01-01 .. null}` → **IN_NETWORK** for 066/2026. ✓

### 6. Specialty/browse results — `/member/search/results/doctors/api`  (NOT for name search)
`?network_id=066&state=FL&year=2026&zip_code=33409` (+ specialty params)
- This is the **browse-by-specialty / distance-sorted** list (`results[].provider`, `totalResultCount`).
- It **ignores `query`/`nameQuery` as URL params** (`requestInfo.nameQuery` stays `null`) — so it is
  **not** the name-search endpoint. Name search = endpoint 4 (autocomplete). Keep this only if a
  future need is "enumerate the whole network" (capped/paginated at 30/page).

---

## Verdict logic the probe should implement (Oscar)

1. **Resolve network:** plan_hint + state + year → `networkId` via endpoint 3 (or a cached map;
   FL test plan → `066`). If unresolved → `UNKNOWN`.
2. **Search by last name** in that network via endpoint 4 (`categories=2`).
3. **Match by NPI (exact)** against returned providers' `doctor_name_fields.npi`.
   - If NPI absent in response, fall back to strict name match (first+last) — but prefer NPI.
4. **If matched** → fetch endpoint 5 with the `entity_id` → read `network_infos` for
   `(networkId, year/date)`:
   - `in_network == true` → **IN_NETWORK** (confidence high).
   - record exists, `in_network == false` (or only past records) → **OUT_OF_NETWORK** (high).
5. **If NOT matched AND result count < 10** (cap not hit, and the search clearly worked because
   other same-surname providers came back) → **OUT_OF_NETWORK** (confidence high/medium).
   *(This is the Kyle Herron case: 4 Herrons returned, none is Kyle.)*
6. **If NOT matched AND result count == 10** (cap hit — provider could be hidden) → narrow the
   query (add first name) and retry; if still capped/ambiguous → **UNKNOWN**.
   **Never default ambiguity to OUT_OF_NETWORK.**

Always populate `source_url` with the exact endpoint(s) queried and which `networkId` was checked.

---

## Known limitations / gotchas

- **10-result autocomplete cap** → common surnames need first-name narrowing or yield `UNKNOWN`.
- **No NPI search** → must search by name, then verify identity by NPI from the response.
- **Network selection is everything** → wrong network = wrong verdict. The plan→network map must be
  right; resolve it from endpoint 3, don't guess.
- **Per-office participation** → a provider with multiple offices/TINs can have mixed records; treat
  "in-network in any covering office record for the target network+date" as IN_NETWORK.
- `categories=2,4,5,6,8` mixes drugs/facilities and fuzzy-matches multi-word queries to drug names
  (`"Kyle Herron"` matched `Kyleena`, `Cleocin`…). Use single last-name token + `categories=2`.

## Evidence files (`./.discovery/`)
- `provider-profile-jessica.json` — full profile for Jessica Herron (positive control, IN 066).
- `search-results-herron.json` — sample of the specialty/browse endpoint (shows `nameQuery:null`).
