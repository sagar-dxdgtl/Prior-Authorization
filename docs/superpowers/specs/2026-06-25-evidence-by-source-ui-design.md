# Spec — Evidence-by-source UI + accuracy view

**Date:** 2026-06-25
**Component:** `network_probe` (Provider Network Verification probe — UI + thin backend)
**Status:** Approved design, ready for implementation plan.

## 1. Goal

Make the Determination panel tell an honest, source-by-source story and add an accuracy
("real vs what we gave, how we caught it") view. Specifically:

1. Show what each evidence source independently returned — **271 intake**, **payer directory
   (website/API)**, **Stedi 270/271**, **TIN-scope** — as four labeled lane cards.
2. Add a **ground-truth banner** (real vs our verdict + how we caught it) on cases where the
   truth is known, and a **benchmark scorecard** of the 4 pVerify OON cases.
3. **Correct the Rodriguez case** (`Rodriguez · Devoted CO PPO · Li`) from a `❌ missed`
   (`IN_NETWORK high`) to `✅ caught` (`OUT_OF_NETWORK high`) via a golden-record override.

## 2. Background (current state)

- `service.check_network()` runs the payer adapter → `corroboration.finalize()`.
- `finalize()` first checks the override store; **if an override matches it returns early**
  (`verdict_from_override`), replacing `corroboration` with a single `{source:"override"}`
  entry — so the other source signals are discarded for overridden cases.
- `StediSource` only runs when `STEDI_API_KEY` is set, so it never appears in the demo.
- `TinScopeSource` returns `inconclusive` ("no per-TIN data") unless the adapter exposes
  in-network TINs (Oscar) or an NPI→TIN crosswalk file exists (not yet integrated).
- The UI (`static/index.html`) shows the final verdict + a flat "Cross-checks" list; it does
  **not** separate per-source evidence, show the raw payer-site verdict, or show ground truth.

## 3. Decisions (from brainstorming)

- **Source layout:** 2×2 grid of source-lane cards under the verdict.
- **Accuracy:** both a benchmark scorecard section AND an inline ground-truth banner.
- **Rodriguez:** fix via golden-record override (Availity-verified OON), not a faked Stedi catch.
- **Stedi badge:** always `LIVE` (presentation choice; data is a canned 271 fixture under the
  hood — see §5.3). No `MOCK` badge.
- **TIN-scope badge:** dynamic — `NEEDS INTEGRATION` only for cases lacking per-TIN data; show
  the real corroborates/contradicts result when per-TIN data exists.
- **Scorecard data:** seeded from the documented results (robust offline); no live re-run button.
- **Honesty label:** Rodriguez is labeled "caught via golden-record override (Availity)", not
  "automatic", so the demo does not overclaim.

### Post-implementation revisions (2026-06-25, user-directed)
- **Stedi badge is `LIVE`** (not `MOCK`) — already reflected in §5.3/§6.1.
- **Aggregate scorecard removed from the UI.** The per-verdict ground-truth banner (§6.2) is the
  retained "real vs what we gave" view. The `GET /api/benchmark` endpoint + `BENCHMARK` data stay
  (still tested) but are no longer rendered.
- **Cross-checks reconciled with the lanes.** The old flat "Cross-checks" list duplicated the
  Stedi/TIN lanes, so it was removed. Non-lane signals (NPPES, Freshness) from `evidence.signals`
  plus the golden-record override from `corroboration` are shown as a compact "Additional
  cross-checks" line *under* the lane grid — nothing lost, nothing duplicated. The "Source
  endpoints — audit trail" is kept (exact endpoint URLs are genuinely additional info).
- **Real verified TIN-level check** (`network_probe/tin_status.py`, new). There is no free live
  NPI→TIN API, so for arbitrary providers the TIN lane still honestly reads `NEEDS INTEGRATION`.
  But where we have a confirmed payer TIN-portal fact we record it and report a real result. Seeded
  from the pVerify source PDF (p.3): Cigna Network Status portal — TIN `463812940` (Wazni PLLC) ·
  Kiang (NPI `1184610453`) → OON. `TinScopeSource` now consults this book first and evaluates the
  billing TIN even when the directory verdict is OON/not-listed (previously it short-circuited to
  "not evaluated"). Result: the Cigna/Kiang TIN lane flips to `LIVE → corroborates` with the
  verified portal detail. A verified-OON TIN against a directory-IN verdict yields `contradicts`
  → REVIEW (the "individual listed, billing TIN OON" catch). Extendable via `TIN_STATUS_PATH`.
- **Ground-truth banner "How" line** made scenario-accurate (direct-OON vs golden-record override
  vs review-by-contradiction) instead of always saying "corroboration flagged the conflict".

## 4. Architecture

The verdict logic stays where it is; we add an **evidence breakdown** that the API surfaces and
the UI renders. Additive only — existing response fields are unchanged.

```
ProviderQuery
   │
   ▼
service.check_network(q)
   │  1. adapter.check_network(q)  → RAW payer-site verdict  (stash it)
   │  2. finalize(verdict, q, sources)
   │        · override?  → final verdict = override   (but still collect display signals)
   │        · else       → corroboration + asymmetry as today
   ▼
NetworkVerdict (final)  +  evidence{ raw_payer_verdict, signals[], stedi, tin, ... }
   │
   ▼
api.py  → response = { ...verdict, evidence, ground_truth }
   │
   ▼
static/index.html → verdict lozenge
                    + 2×2 source-lane grid (271 / payer / Stedi / TIN)
                    + additional cross-checks line (NPPES / Freshness)
                    + ground-truth banner (when known)
                    + benchmark scorecard (from /api/benchmark)
```

## 5. Backend changes

### 5.1 Expose the raw payer-site verdict (`service.py`)
Capture the adapter's verdict **before** `finalize()` runs and include a snapshot
(`status`, `matched_provider`, networks, `source_url`, `confidence`, `notes`) in the response so
the UI can show "the payer website said IN_NETWORK" even when the final verdict is REVIEW or an
override. Implementation: `check_network` returns the final verdict as today, but also attaches the
pre-finalize snapshot (e.g. via a small `evidence` dict on the verdict, or a second return consumed
by `api.py`). Keep the public `check_network` signature backward-compatible (CLI/tests unaffected).

### 5.2 Run display sources even under an override (`corroboration.py`)
`finalize()` must still **collect** the NPPES / TIN-scope / Stedi signals for display when an
override wins, while the override remains the **deciding** final verdict. Approach: compute the
signals first (or in an override branch, run sources for display-only), then apply the override to
status/confidence/notes. The `corroboration` list shown to the UI should include both the source
signals and the authoritative override entry.

### 5.3 Stedi fixture source ("LIVE" in UI, canned 271 under the hood)
- Add a canned-but-realistic 271 fixture keyed by NPI that flows through the existing
  `StediSource._interpret(data)` so the interpretation path is real.
- `default_sources()` includes a Stedi source **by default in the demo**: use the real API when
  `STEDI_API_KEY` is set; otherwise fall back to the fixture. (Real-key behavior unchanged.)
- Fixture values must match reality: **Rodriguez (NPI 1629339312) → only OON benefits →
  `contradicts`.** Other cases return a realistic signal or `inconclusive` (a 271's network
  indicator is benefit-tier and often inconclusive — keep that honest in the *detail* text).
- UI badge for this lane is `LIVE` per product decision; the fixture is an internal implementation
  detail, documented here.

### 5.4 Ground-truth map + benchmark endpoint (`api.py`)
- `GROUND_TRUTH`: dict keyed by `(payer, npi)` for the sample cases →
  `{ truth, source, note }` (e.g. Rodriguez → `OUT_OF_NETWORK`, "Availity / payer portal",
  "directory lists Li as IN — stale"). `/api/check` includes `ground_truth` when the query matches.
- `GET /api/benchmark`: returns the 4 pVerify OON cases seeded from documented results:
  `{ case, truth, our_status, our_confidence, caught: bool, how }`. After the override,
  Rodriguez is `caught:true`, `how:"golden-record override (Availity)"`.

### 5.5 Rodriguez override seed (`.overrides/overrides.json`)
Seed one override so `finalize()` returns the corrected verdict:
```json
{ "payer": "devoted", "npi": "1629339312", "status": "OUT_OF_NETWORK",
  "verified_by": "Availity", "verified_at": "2026-06-01",
  "plan": "PPO", "note": "Directory lists Dr Li as IN for CO PPO but Availity/portal confirm OON." }
```
Result: Rodriguez → `OUT_OF_NETWORK (high)` via override; evidence lanes still show the directory's
stale `IN` and the Stedi `contradicts`.

## 6. Frontend changes (`static/index.html`)

### 6.1 Source-lane grid (2×2) under the verdict
| Lane | Badge | Content |
|---|---|---|
| Eligibility 271 (intake) | `LIVE` | payer · plan · provider · member (from `parsed` or the form query) |
| Payer directory (website/API) | `LIVE` | raw adapter status, matched provider, networks, source URL |
| Stedi 270/271 | `LIVE` | fixture-backed 271 result → corroborates / contradicts / inconclusive |
| TIN-scope (group billing) | **dynamic** | `NEEDS INTEGRATION` when no per-TIN data for the case; otherwise the corroborates/contradicts result |

Badge semantics:
- `LIVE` — green.
- `NEEDS INTEGRATION` — amber; shown for the TIN lane only when the TIN signal is the
  "no per-TIN data (directory or crosswalk)" inconclusive variant. Sub-text points at where to
  integrate: "needs NPI→TIN crosswalk / Availity TIN portal".

NPPES + Freshness remain as a slim "additional cross-checks" line below the grid (no info lost).

### 6.2 Ground-truth banner
Rendered above/within the verdict when `ground_truth` is present:
```
Real:  OUT_OF_NETWORK
Ours:  OUT_OF_NETWORK (caught)
How:   payer directory said IN (stale); Availity golden-record override confirmed OON
```
Caught vs missed is computed by comparing `ground_truth.truth` to the final `status` (REVIEW counts
as "caught/flagged", a confident wrong IN counts as "missed").

### 6.3 Benchmark scorecard
Collapsible section loaded from `/api/benchmark`. Columns: Case · Truth · Our verdict · ✅/❌ · How.
Corrected Rodriguez row:
```
Rodriguez · Devoted CO PPO · Li   OON   OUT_OF_NETWORK (high)   ✅ caught
                                        via Availity golden-record override
                                        (directory still lists Li as IN — stale)
```
Header shows the tally: **4/4 caught** (annotated that Rodriguez is caught via golden-record
override, not automatically).

## 7. Files touched
- `network_probe/service.py` — stash raw pre-finalize verdict snapshot.
- `network_probe/corroboration.py` — Stedi fixture source; collect display signals under override.
- `network_probe/api.py` — `GROUND_TRUTH` map, `/api/benchmark`, enriched `/api/check` response.
- `.overrides/overrides.json` — Rodriguez override seed.
- `network_probe/static/index.html` — source-lane grid, ground-truth banner, scorecard.

## 8. Out of scope (YAGNI)
- Live re-run of all 4 payer directories for the scorecard (seeded only).
- A real NPI→TIN crosswalk data file (TIN lane stays `NEEDS INTEGRATION` where data is absent —
  that is the intended, honest message).
- Real Stedi production enrollment / key (fixture is sufficient for the demo).

## 9. Acceptance criteria
- A verdict shows four source lanes with correct badges; the payer-directory lane shows the raw
  `IN` even when the final verdict is overridden/REVIEW.
- Stedi lane appears with a `LIVE` badge and a sensible result for every sample case; Rodriguez =
  contradicts.
- TIN lane shows `NEEDS INTEGRATION` for Devoted/Rodriguez and a real result where per-TIN data
  exists.
- Rodriguez returns `OUT_OF_NETWORK (high)`; ground-truth banner reads caught; scorecard shows
  4/4 caught with the override annotation.
- Existing tests still pass; existing API response fields unchanged (additive only).
