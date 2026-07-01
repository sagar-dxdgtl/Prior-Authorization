# OON benefits (Stedi) + TiC/TIN roster — demo design

**Date:** 2026-07-01
**Branch:** demo
**Status:** approved, ready for implementation plan

## Goal

For the demo, add two things on top of the existing network-status probe, using the test
data we already have:

1. **Out-of-network benefits from Stedi** — for the 7 test-data members, fetch the full
   271 eligibility response, parse the benefits, and show them in the UI under a new **OON**
   tab. The existing verdict/corroboration/evidence moves under a **Details** tab.
2. **TiC/TIN roster** — persist the 10 United Vein & Vascular Centers (UVC) billing entities
   and any real TiC-extracted NPI↔TIN records as the "result" (the huge MRFs are never
   stored), feeding the existing `TinScopeSource`.

## Non-goals

- No live Stedi fetch inside the running app. OON is **prefetched once** for the members we
  already have, saved, and displayed from the saved copy. Demo only.
- No multi-GB MRF hosting. We store only the extracted slice.
- No new committed PHI. All demo data (OON benefits, TiC result) lives in the gitignored
  `.cache/`.
- Not touching the network verdict logic. OON is additive display; the TiC roster feeds the
  crosswalk exactly as `TinScopeSource` already expects.

## Constraints / decisions (from brainstorming)

- **Storage:** everything demo-specific goes in `.cache/` (already gitignored, alongside the
  eligibility PDFs). No new committed data files.
- **OON:** prefetch the members we have, save, display when a sample is selected. No runtime
  fetch path.
- **TiC:** use *real* TiC data (extracted from the payer MRFs on the operator's laptop). Save
  only the extracted result slice for these 10 TINs. Anything reachable by a live API (NPPES
  identity, payer FHIR) stays a live call — not persisted.

## Deliverable A — OON benefits from Stedi

### Data flow
```
test-data/*.pdf ──parse_report──> {payer_key, member_id, dob, npi, name, state}
      │
      └─ build Stedi 270 (shared body builder) ──CachedClient.post_json──> raw 271 (.cache/<sha>.json)
                                                          │
                                              parse_oon(271) ──> [OonBenefit,...]
                                                          │
                                       write .cache/oon_benefits.json  (npi -> parsed OON)
```

### Components
- **`network_probe/oon_benefits.py`** (new)
  - Factor the Stedi 270 body-builder + endpoint/auth out of `StediSource` into a shared
    helper (`stedi_271(query, client)`), used by both `StediSource` and this module — no
    behavior change to `StediSource`.
  - `parse_oon(resp: dict) -> list[dict]` — normalize `benefitsInformation`, keeping the OON
    lines (`inPlanNetworkIndicatorCode == "N"`) plus network-agnostic lines. Per benefit:
    `code`/`name` (deductible, coinsurance, out-of-pocket, copay, limitation…), amount or
    percent, coverage level (individual/family), time period (remaining/plan-year), service
    type(s), and any free-text messages. "All benefits we get" — nothing dropped.
  - Prefetch entrypoint: `python -m network_probe.oon_benefits test-data/*.pdf`
    reads each PDF → `parse_report` → `stedi_271` (cached) → `parse_oon` → writes
    `.cache/oon_benefits.json` keyed by NPI (with member label). PHI stays in `.cache/`.

- **`network_probe/api.py`**
  - `GET /api/oon?npi=…` → reads `.cache/oon_benefits.json`, returns that member's parsed OON
    (or `[]` when the prefetch hasn't been run / no data).

- **`network_probe/static/index.html`**
  - Result panel becomes two tabs:
    - **OON** — benefit table from `/api/oon`.
    - **Details** — the current verdict / corroboration / evidence / ground-truth block,
      unchanged.
  - Selecting a sample loads both; if OON is empty, the tab shows "run prefetch to populate."

### The 7 members (from `api.py` SAMPLES + test-data PDFs)
Ochoa/Oscar, Craig/Devoted-TX-HMO, Rodriguez/Devoted-CO-PPO, Franz/Humana-PPO,
Schindler/Humana-PPO, Benschneider/Cigna, Salman/UHC. All payers are already mapped in
`StediSource.PAYER_IDS`.

### Open item (first implementation step)
Confirm the `.env` Stedi key returns **real** member benefits vs mock (test keys hit mock
payers only). Do one live probe, report the result. If mock, the parser + UI still work; we
flag it rather than fabricate benefits.

## Deliverable B — TiC/TIN roster

### The 10 UVC entities (authoritative input)
| Entity | Tax ID (EIN) | Group NPI | State |
|---|---|---|---|
| Arizona UVC Medical, PLLC dba United Vein & Vascular Centers | 84-3447602 | 1548800980 | AZ |
| Colorado Medical Group, PLLC dba UVC | 47-5181686 | 1356714638 | CO |
| Georgia UVC Medical, LLC dba UVC | 92-1600050 | 1619681244 | GA |
| Illinois UVC Medical, PLLC dba UVC | 84-3012976 | 1689224719 | IL |
| New York Medical United, PLLC dba UVC | 88-0715104 | 1265121693 | NY |
| NJ UVC Medical, PLLC dba UVC | 93-1867629 | 1053977801 | NJ |
| Wazni, PLLC dba UVC | 46-3812940 | 1114353026 | FL |
| Srinivas Rao MD PA dba Texas Vein & Wellness Institute | 41-2049581 | 1972941318 | TX-Houston |
| Texas UVC Medical, PLLC dba UVC | 93-3510922 | 1447023528 | TX-Dallas |
| Vascular Health LLC | 83-4407175 | 1053977801 | NJ |

Cross-references already in the codebase:
- TIN **463812940 / Wazni PLLC** → `tin_status.py` seed (Cigna OON for Kiang).
- TIN **933510922 / Texas UVC Medical** → `tin_crosswalk.py` seed (UHC TX exchange,
  rendering NPIs 1972603934, 1710305735).
- Group NPI **1053977801** appears twice (NJ UVC Medical + Vascular Health LLC) — preserve
  both mappings.

### Components
- **`.cache/tic_crosswalk.json`** (gitignored demo data) — holds:
  - the 10-entity roster (entity, EIN/TIN, group NPI, state), and
  - real TiC-extracted rendering-NPI → TIN(→payer/network) records for these TINs, seeded
    with the verified UHC-TX slice and extended with whatever the laptop TiC check yields.
- **`network_probe/tin_crosswalk.py`** — `default_crosswalk()` also loads
  `.cache/tic_crosswalk.json` when present (layered on top of the in-code seed, same pattern
  as the existing `TIN_CROSSWALK_PATH`). Accepts the roster + record shapes.

## Testing
- Offline unit tests (no PHI):
  - `parse_oon` over a synthetic 271 fixture (in/out lines, amount vs percent, ind/family).
  - crosswalk loads `.cache/tic_crosswalk.json` and resolves a rendering NPI → TIN.
- One live-marked Stedi test (skipped without key).
- Existing 41 tests stay green.

## Rollout for the demo
1. `python -m network_probe.oon_benefits test-data/*.pdf` (populates `.cache/oon_benefits.json`).
2. Operator drops extracted TiC records into `.cache/tic_crosswalk.json`.
3. `uvicorn network_probe.api:app` → OON + Details tabs live.
