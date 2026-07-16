# Live eligibility — searchable payer select + 271-grounded plan — design

## Summary

Turn the Eligibility page from a demo (two free-text boxes: "Payer ID" and "Plan") into a
**live check that works across the whole payer roster and returns correct answers**, by removing
the two places the user is currently asked to *guess*:

1. **Payer** becomes a **searchable select** — curated roster first (vetted Stedi ids), Stedi's
   live payer directory as the long-tail fallback. The user picks from real ids, never types a
   trading-partner id by hand.
2. **Plan is no longer entered up front.** A 270/271 needs **no plan** — only payer + member
   identity + provider NPI. We run the 270/271, read the member's *actual* plan off the 271, use
   it to scope the directory network check, and expose it as a **selectable/searchable control**
   seeded with the plan candidates the payer itself returned. The user can still override, but the
   default is the payer's own answer — which kills what `README.md` calls "the #1 source of wrong
   answers" (plan→network mapping).

The correctness thesis: **the plan should come from the payer's 271, not from a human's memory of
a member card.**

## Prior art in this repo (read, not re-derived)

- **`src/network_probe/stedi/client.py` — `StediEligibilityClient.check`.** Builds the 270 body:
  `tradingPartnerServiceId` (the Stedi payer id) + `provider` (npi/name) + `subscriber`
  (memberId/dob/name) + `encounter.serviceTypeCodes`. **There is no `plan` field in the 270** —
  confirming the plan is unnecessary for eligibility and is only needed for the directory leg.
  The client is constructed with `payer_id=<stedi id>`; with no id it returns
  `_unknown("no Stedi payer id for …")`.

- **`src/network_probe/stedi/parse_271.py` — `parse_271_benefits`.** Today it sets
  `plan_name = planInformation.planName or planInformation.groupDescription`. **Verified against
  the 8 cached real 271s in `.cache/stedi_271/`: `planInformation` is frequently `{}` (Devoted) or
  only the employer group ("DISNEY WORLDWIDE SERVICES") — while the member's real plan lives in
  `benefitsInformation[].planCoverage`**, which this function currently ignores:

  | member | `planCoverage` values in the 271 |
  |---|---|
  | Rodriguez · Devoted CO | `DEVOTED CHOICE GIVEBACK 003 CO (PPO)` |
  | Craig · Devoted TX (dual-eligible) | `DEVOTED GIVEBACK 006 TX (HMO)` **and** `03 - SLMB ONLY (PARTIAL DUAL)` |
  | Herron · Oscar | `BASE SILVER CSR 150` **and** `SILVERSIMPLEPCPSAVER` |
  | Fradkin · UHC | `UHC BRONZE ESSENTIAL` |
  | Franz/Leschak · Humana | `HUM Full Ac Giveback` **and** `AFFILIATION/CENTER` |
  | Kiang · Cigna | `Network` (unusable — see guardrails) |

  These strings are exactly the `plan_hint` the directory adapters already consume
  (`"CO (PPO)"`, `"BASE SILVER CSR 150"`). Most 271s return **2+** descriptors, so plan
  selection is a real choice *inside a single 271*, not an edge case.

- **`src/network_probe/domain/eligibility.py` — `check_eligibility`.** Orchestrates the two legs:
  resolves payer via `DbPayerCatalogue`, runs the Stedi 270/271, then runs `check_network`
  (directory) and **merges** — the reconcile rules are: directory `IN` vs 271 `OUT` → `REVIEW`;
  directory `OUT` vs 271 `IN` → `REVIEW`; 271 `UNKNOWN` + directory decisive → take directory;
  then a tenant golden-record override wins last. This merge block is the correctness core and
  will be **extracted into a pure `reconcile()`** so the network-only re-check can reuse it.

- **`src/network_probe/domain/benefits.py` — `EligibilityResult`.** The result dataclass +
  `to_dict()`. Gains `plan_candidates` and `selected_plan` fields (below).

- **`src/network_probe/api/app.py`.** `POST /api/eligibility` (the real 270/271 + directory +
  merge route the React page calls) and `POST /api/check` (directory-only; note its
  first/last-name are the **provider's**, and it does **not** merge with a 271). `GET /api/payers`
  returns a **hardcoded 6-payer list** used only by the legacy static `index.html`. The React app
  will use a new search endpoint instead; the hardcoded list stays for the static UI.

- **`src/network_probe/payers/catalogue.py` — `DbPayerCatalogue.resolve`.** Maps an adapter key /
  roster key / label → a global `Payer` row (→ `stedi_payer_id`), via `ADAPTER_ALIASES` then slug
  match on `key` then `label`. Roster catalogue keys are `f"{slug(label)}-{slug(state)}"`
  (e.g. `aetna-az`).

- **`src/network_probe/payers/roster_seed.py` — `ROSTER`.** 196 rows / **54 distinct payers** ×
  8 markets (AZ, CO-Denver, NY, FL-South Florida, IL, GA-Atlanta, TX-Houston, TX-Dallas), each
  `(label, benefit_type, state, stedi_payer_id|None, enrollment_status)` where
  `enrollment_status ∈ {supported (34), needs_enrollment (65), needs_payer_id (97)}`.

- **`scripts/resolve_payer_ids.py` — `search_payer`.** Already calls Stedi's live payer directory
  `GET https://healthcare.us.stedi.com/2024-04-01/payers?query=<label>`; each `items[]` has
  `stediId`, `primaryPayerId`, `displayName`, `conciseName`, `names`, `aliases`, and
  `tradingPartnerServiceId` accepts `primaryPayerId`. This is the fallback data source for the
  payer select — the HTTP contract is proven.

- **`web/src/pages/Eligibility.tsx`.** AntD form; today "Payer ID" and "Plan" are plain `Input`s,
  both `required`. Submits `POST /eligibility` and renders coverage stat tiles, a network-verdict
  banner, a TIN-scope card, and the cost-share matrix.

## Design

### 1. Searchable payer select (`GET /api/payers/search`)

New endpoint: `GET /api/payers/search?q=<str>&limit=20` → ranked options:

```jsonc
[
  { "value": "aetna-az", "label": "Aetna", "market": "AZ", "benefit_type": "Commercial",
    "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment", "source": "roster" },
  { "value": "stedi:128KY", "label": "Aetna Better Health of Kentucky",
    "stedi_payer_id": "128KY", "enrollment_status": null, "source": "stedi" }
]
```

- **Roster first.** Query the DB `payers` table (`label`/`key` ILIKE `%q%`), rank exact →
  prefix → substring, tagged with market + `enrollment_status` badge. `value` is the catalogue
  key so `DbPayerCatalogue.resolve` continues to work unchanged and the directory leg keeps its
  adapter.
- **Stedi fallback.** When the roster yields few/no hits, call
  `GET /2024-04-01/payers?query=<q>` (reusing `search_payer`'s client/contract), map `items[]` →
  options with `source:"stedi"`, `stedi_payer_id = primaryPayerId or stediId`, `value =
  "stedi:<id>"`. Dedup against roster by stedi id. Gated by `STEDI_API_KEY`; absent → roster-only.
- **UI:** AntD `Select showSearch filterOption={false}` with debounced `onSearch` → this endpoint.
  Option render shows `label · market · [status badge]`. Roster options carry an
  `enrollment_status` badge; `supported` = green, `needs_enrollment` = amber.

### 2. Request/response wiring for Stedi-only payers

A Stedi-directory payer has no roster row and no directory adapter. To still run its 270/271:

- Add optional **`stedi_payer_id`** to `CheckRequest`. When the selected option's `source` is
  `stedi`, the client sends `payer:"stedi:<id>"` **and** `stedi_payer_id:"<id>"`.
- In `/api/eligibility`: if `stedi_payer_id` is present, use it directly for the Stedi leg
  (skip catalogue resolution for the id). The directory leg still runs via `check_network(q)` and
  simply returns `UNKNOWN` for a payer with no adapter — which the merge already tolerates.
  **Result: coverage + benefits + plan candidates work for any Stedi payer; the network verdict is
  `UNKNOWN` (honest) until that payer gets an adapter.**

### 3. Flow — 270/271 first, plan auto-derived, directory scoped, override available

**Single submit, no plan field up front.** User picks payer + member id/dob/name + provider NPI.

1. `POST /api/eligibility` runs the 270/271.
2. `parse_271_benefits` now also builds **`plan_candidates`** from the distinct
   `benefitsInformation[].planCoverage` values (fallback: `planInformation.planName`/
   `groupDescription`), each `{ plan, rank, is_product }`, and sets **`selected_plan`** = the
   top-ranked candidate. Ranking (see guardrails) prefers a real product/network line over a
   Medicaid-segment line.
3. The directory leg (`check_network`) is scoped by `selected_plan` (used as `plan_hint`), then
   `reconcile()` merges. Response includes `plan_candidates`, `selected_plan`, `network_status`,
   `network_verdict`, `corroboration`, benefits, etc.
4. **UI plan control (after the check):** an AntD `Select showSearch` seeded with
   `plan_candidates`, defaulted to `selected_plan`, plus a free-text entry (AntD `mode` allows a
   typed value) so the user can enter any plan. Changing it calls the re-check route (below) and
   updates the verdict in place. **No second 270 is sent.**

### 4. Network-only re-check (`POST /api/eligibility/recheck-network`)

Body `{ payer, stedi_payer_id?, npi, plan, stedi_network_status }` →
runs **only** `check_network(q)` for the new `plan`, then `reconcile(stedi_network_status, verdict)`
using the extracted pure function, and returns `{ network_status, network_verdict, corroboration }`.
No 270, no subscriber PHI beyond the NPI already in play. The client passes back the 271's own
`network_status` (IN/OON/UNKNOWN) it received in step 3 so the REVIEW-on-conflict logic still fires.

### 5. Correctness guardrails

- **Fix the latent bug:** derive plan from `benefitsInformation[].planCoverage`, not
  just `planInformation.planName` (usually empty). Unit-pinned against `.cache/stedi_271/`.
- **Candidate ranking / dual-eligible safety:** a descriptor is `is_product=true` when it contains
  a network/product token (`HMO|PPO|EPO|POS` or a metal/plan token like
  `SILVER|BRONZE|GOLD|PLATINUM|CHOICE|ESSENTIAL`) and `false` when it looks like a coverage
  segment (`SLMB|QMB|PARTIAL DUAL|MEDICAID|AFFILIATION`). Product lines rank above segments so the
  network check defaults to the MA/commercial plan, **but both are shown** — a dual-eligible member
  legitimately may need the network verified against the MA plan the segment sits under.
- **No fabrication:** if every candidate is unusable (e.g. Cigna's lone `"Network"`) and the payer
  has no adapter catalog, `selected_plan` is empty → directory returns `UNKNOWN`; never invent a
  network. Free-text override remains available.
- **Invariants unchanged:** directory-vs-271 conflict → `REVIEW`; ambiguity → `UNKNOWN` never
  `OUT_OF_NETWORK`; tenant golden-record override is authoritative last word.

## Scope

**In scope:** `GET /api/payers/search`; `CheckRequest.stedi_payer_id`; direct-id Stedi leg for
non-roster payers; `plan_candidates`/`selected_plan` on `EligibilityResult` + `parse_271_benefits`;
extract `reconcile()`; `POST /api/eligibility/recheck-network`; rework `Eligibility.tsx` (payer
searchable select, plan control seeded from the 271, re-check on change); tests.

**Deferred (documented, not built now):** full **live plan-catalog typeahead** (option "B" from
brainstorming) — enumerate a payer's entire plan/network menu for search. Feasible only where the
adapter can enumerate (Oscar walks per-state networks; Devoted has an Algolia network facet); the
FHIR payers (Humana/UHC/Cigna) mostly can't (their PDEX `network-reference` often carries no real
network name — see the UHC/UMR notes in `roster_seed.py`). The MVP satisfies "search from multiple
plans" via the 271 candidates + free-text override; full-catalog search would add an optional
`list_plans()` capability to `PayerAdapter` and a `GET /api/payers/{key}/plans?q=` endpoint. Revisit
after the core ships.

**Non-goals:** batch/CSV eligibility; changing the 270 service-type codes; auth/quota changes;
the legacy static `index.html` UI (keeps `GET /api/payers`).

## Testing

- **Parse:** extend `tests/test_parse_271.py` — assert `plan_candidates`/`selected_plan` from the
  cached real 271s (Devoted dual-eligible ranks the HMO line above `SLMB`; Cigna `"Network"` →
  empty `selected_plan`; Oscar → `BASE SILVER CSR 150`).
- **Reconcile:** unit-test the extracted `reconcile()` for all four conflict/agreement cases.
- **API:** `tests/test_api_eligibility.py` — `/api/payers/search` roster hits + Stedi-fallback
  (mocked transport); `/api/eligibility` returns candidates; `/api/eligibility/recheck-network`
  changes only the network verdict; a `stedi:<id>` payer runs the 270 leg with `UNKNOWN` network.
- **Live (`-m live`):** unchanged; the demo cases still pass end-to-end.

## Risks / open questions

- **Stedi payer-directory result quality** for the searchable fallback (fuzzy names); mitigated by
  roster-first ordering and showing `source` so the user sees curated vs. directory.
- **Plan-string → network match** for less-clean descriptors (Humana `AFFILIATION/CENTER`); the
  override control is the escape hatch, and the verdict degrades to `UNKNOWN` not a wrong `OON`.
- Whether to pull deferred full-catalog search into this build — decide at spec review.
