# Centene PractitionerRole reference-format bug — design

## Summary

Fix `FhirPdexAdapter._networks_for()` (`src/network_probe/payers/adapters/fhir_pdex.py`), which
currently queries `PractitionerRole?practitioner=<bare_id>` and silently returns zero roles for
every Centene-family payer (Ambetter, Wellcare, AZ Complete Health, Peach State, Superior
HealthPlan, Meridian Health) because Centene's HAPI FHIR server requires the full
`Practitioner/<id>` reference form for that search parameter. Found during live verification of
the Meridian Health fix (previous sub-project); confirmed pre-existing (this adapter method is
untouched by that work) and shared by every FHIR-PDEX-routed payer, not Centene-specific in its
blast radius even though the root cause is.

## Research findings

1. **Confirmed live, three ways, that no single query format is universally safe.** Tested the
   `PractitionerRole?practitioner=...` parameter against three already-verified-live PDEX
   servers using real practitioners:
   - **Centene** (`iopc-pd.api.centene.com`): bare id → `total: 0`; `Practitioner/<id>` → `total: 16` (real roles).
   - **HCSC** (`api.hcsc.net`): bare id → `total: 24` (real roles); `Practitioner/<id>` → `total: 0`.
   - **UHC/Optum** (`flex.optum.com`): bare id → real roles (confirmed during the prior UMR
     sub-project's research).

   HCSC and Centene take the *opposite* answer from each other — a blanket switch to the full
   reference form would fix Centene but silently break HCSC, which is live in production today
   for BCBS commercial/Medicare Advantage/Medicaid. Neither format is safe to hardcode.

2. **`ScanDirectoryAdapter` is unaffected.** It's a separate class (`src/network_probe/payers/adapters/scan.py`)
   with its own `check_network()` — it does not inherit `FhirPdexAdapter` or call `_networks_for()`.
   Confirmed by the existing test `test_scan_routes_to_presence_adapter`
   (`tests/test_payer_sources.py`), which asserts `not isinstance(adapter, FhirPdexAdapter)`.

3. **This codebase already has the right pattern for exactly this situation.**
   `FhirPdexAdapter._find_practitioner()` (same file) already tries identifier search first,
   catches `httpx.HTTPStatusError`, and falls back to name search — a precedent for "try the
   default, detect failure, retry a different way" rather than a hardcoded per-server config
   table that needs manual upkeep every time a new PDEX payer is onboarded.

4. **Existing test infrastructure fits without new tooling.** `tests/test_fhir_pdex.py` already
   mocks `httpx.MockTransport` with query-param-based routing to fixture JSON files (see
   `_fhir_handler`, matching on the `practitioner=` query value). A regression test for this fix
   is a natural extension of that existing pattern, not a new one.

## Scope

**In scope:**
- `FhirPdexAdapter._networks_for()`: extract the "fetch + parse one page-walk of
  `PractitionerRole` for a given `practitioner=` value" logic into a small private helper, call
  it once with the bare practitioner id (today's default — every already-verified server stays
  on its current, working path), and if that returns zero roles, retry once with the full
  `Practitioner/<id>` reference form before falling through to Organization-reference resolution
  and de-duplication (unchanged).
- A new offline regression test in `tests/test_fhir_pdex.py`, following the existing
  `httpx.MockTransport` fixture pattern, proving: (a) a Centene-shaped server (zero roles on bare
  id, real roles on full reference) now resolves correctly, and (b) an already-working
  bare-id-only server (the existing Humana/Kyle fixture) is unaffected — no retry triggered when
  the first attempt already found roles.
- A test proving a genuinely zero-role practitioner (both formats legitimately return zero) still
  correctly reports "no active network roles" — not an error, not a false positive.

**Explicitly out of scope:**
- The separate, previously-flagged Organization-dereference quirk on UHC/Optum's server (where
  resolving a bare Organization reference sometimes returns an unrelated organization). Different
  server, different symptom, different code path (`_org_name()`, not the `PractitionerRole`
  query) — not touched here.
- Any change to `_find_practitioner()` (the identifier/name-search fallback) — already correct,
  not implicated in this bug.
- Any change to `ScanDirectoryAdapter` — confirmed unaffected (finding #2).
- Community Care Plan (FL) — separate sub-project, next after this one.

## Design

### `src/network_probe/payers/adapters/fhir_pdex.py`

Extract the page-walking body of the current `_networks_for()` into a new private helper,
`_fetch_practitioner_roles(self, practitioner_ref: str) -> tuple[list[str], list[str], set[str], int]`,
returning `(inline_names, org_refs, specialties, role_count)` for one query attempt — no
Organization-reference resolution yet, that stays in `_networks_for()` after the winning attempt
is chosen. `_networks_for()` becomes:

1. Call the helper with the bare `practitioner_id` (unchanged default path).
2. If `role_count == 0`, call the helper again with `f"Practitioner/{practitioner_id}"` and use
   that attempt's results instead — with a comment explaining why (citing this spec and the
   concrete Centene-vs-HCSC evidence from finding #1, so a future reader doesn't "simplify" this
   back to one hardcoded format).
3. Everything after that point (Organization-reference resolution via `_org_name()`, de-dup,
   `MAX_ORG_RESOLVE` capping) stays exactly as it is today, just operating on whichever attempt's
   `names`/`refs` won.

This is a pure internal refactor of one method — no signature change to `_networks_for()` itself,
no change to `check_network()`, no change to any caller, no change to `KNOWN_ENDPOINTS` or any
other payer's configuration.

### `tests/test_fhir_pdex.py`

Add a new mock handler (following the existing `_fhir_handler`/`_refserver_handler`/`_uhc_handler`
pattern already in this file) that simulates a Centene-shaped server: `PractitionerRole` returns
`total: 0` when queried with a bare id, and real role entries when queried with the
`Practitioner/<id>` form. Add:
- A test proving a practitioner on this mock server now resolves to their real networks (the
  fallback fires and succeeds).
- A test proving the existing bare-id-only Humana fixture path is unaffected (no second request
  is made when the first attempt already found roles — assert on a request-count or
  request-log fixture, not just the final result, so a future regression that always retries
  wouldn't slip through unnoticed).
- A test proving a practitioner absent from both formats' role lists still reports "no active
  network roles" (`check_network()` status, via the existing `test_kyle_no_hint_lists_networks_in_network`-style
  assertions or a dedicated one) rather than erroring.

## Testing

- `pytest tests/test_fhir_pdex.py -v` — all existing tests continue to pass unmodified, plus the
  new tests above.
- `pytest tests/test_payer_sources.py -k "not db"` — unaffected (no roster/catalogue changes in
  this sub-project), run as a regression check anyway.
- Manual live smoke test: run the exact live Meridian Health check that surfaced this bug during
  the prior sub-project's Task 4 (Kevin Petermann, NPI `1588744650`, via the shared Centene
  endpoint) and confirm it now returns real network names instead of "no active network roles."
  Also re-run the equivalent HCSC live check (Jeffery Friedman-style query) to confirm the
  already-working path is genuinely untouched, not just passing by coincidence.

## Follow-ups (not this change)

- The separate UHC/Optum Organization-dereference quirk (scope item explicitly excluded above)
  remains open, tracked from the earlier UMR sub-project's spec.
- Community Care Plan (FL) — next sub-project after this one ships.
