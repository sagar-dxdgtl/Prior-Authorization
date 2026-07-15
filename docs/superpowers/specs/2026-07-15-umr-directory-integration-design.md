# UMR provider-directory integration — design

## Summary

Add UMR (UnitedHealth Group's third-party-administrator brand for self-funded employer
plans) to the payer catalogue as a directory-integrated payer by aliasing it to the
already-wired UnitedHealthcare/Optum FHIR endpoint. No new adapter code. Along the way,
fix a live bug in the identical existing pattern for "UnitedHealthcare Community Plan"
discovered while verifying this design.

## Research findings

1. **UMR rides UnitedHealthcare's own networks, not a UMR-specific one.** UMR's own
   "find a provider" tool (`umr.com/find-a-provider`) lists ~15 named networks — Choice
   Plus, Core, Options PPO, Select Plus, NexusACO, and state-tiered variants — every one
   a UnitedHealthcare-branded national network product. Independent sources confirm most
   UMR plans use UnitedHealthcare Choice Plus specifically. Two named networks on that
   page are explicitly out of scope: **Oxford Freedom** (external Rally-platform tool —
   Oxford is a distinct legacy UHG brand for NY/NJ/CT) and **UnitedHealthcare Dental PPO**
   (different product line, not relevant to a vascular practice).

2. **The existing `flex.optum.com` FHIR endpoint (already wired as the `"uhc"` adapter)
   is live and returns real data for this client's own roster.** Verified directly
   against three in-roster NPIs (Manayan/GA, Naar/FL, Bui/AZ) — all three resolved and
   came back `IN_NETWORK`.

3. **Known limitation (pre-existing, not introduced by this change):** the PDEX
   `network-reference` extension on this server never carries an inline network name —
   only a bare `Organization/{id}` reference. Dereferencing that id via the adapter's
   existing fallback path returns the *same* organization name regardless of which id is
   requested (confirmed across 3 different practitioners/ids). Today this integration can
   answer "is this provider in the UHC-affiliated network at all" but not "which specific
   UHC/UMR sub-network." This already silently applies to every UHC-family roster row
   today, not just the new UMR rows — flagged as a follow-up, not fixed here.

4. **UMR has no single Stedi/EDI payer id.** Stedi lists 19+ distinct UMR-associated ids
   (`UMR01`, `UMRWAU`, `XXUMR`, `10394`, `GEHA`, …) because UMR's eligibility routing is
   per self-funded employer group, not one umbrella id like UHC's `87726`. Per this
   codebase's existing policy (see `roster_seed.py` header comment), an unconfirmed id is
   left blank for human review rather than guessed.

5. **UMR's TiC machine-readable files are published on UHC's own portal.** "UnitedHealthcare,
   UMR, and HealthSCOPE Benefits create and publish Machine-Readable Files on behalf of
   group health plans... posted at transparency-in-coverage.uhc.com" — same `tic_url`
   as the existing UnitedHealthcare rows.

6. **Bug found while verifying the pattern this design copies:** `SOURCES["UnitedHealthcare
   Community Plan"]` has `fhir_base_url=None`. Its roster label never collapses (via
   `.lower()`) to the `"uhc"` adapter-key shortcut the way a bare `"uhc"` payer key does,
   and unlike `"UnitedHealthcare"` it has no populated catalogue `fhir_base_url` to fall
   back on — so `get_adapter("UnitedHealthcare Community Plan", ...)` currently raises
   `ValueError: No adapter for payer...` at runtime. Confirmed via
   `pytest tests/test_payer_sources.py`: `test_public_fhir_rows_have_fhir_or_existing_adapter`
   already fails on this exact row today. Since this design adds a *third* label
   (UMR) sharing the identical `flex.optum.com` endpoint, fixing this now (not copying the
   same bug forward) is the same one-line change applied consistently.

## Scope

**In scope:**
- One new shared constant for the `flex.optum.com` FHIR base URL (currently duplicated
  inline once; about to be duplicated a third time).
- Fix `SOURCES["UnitedHealthcare Community Plan"]` to use that constant instead of `None`.
- New `SOURCES["UMR"]` entry, same URL, `directory_access="public-fhir"`.
- New `ROSTER` rows for `"UMR"` / `"Commercial"` across every market the catalogue
  currently covers (AZ, CO-Denver, NY, FL-South Florida, FL, IL, GA-Atlanta, TX-Houston,
  TX-Dallas — 9 rows), `stedi_payer_id=None`, `enrollment_status="needs_payer_id"`
  (matches the existing Molina precedent: live FHIR directory, unconfirmed EDI id).
- `docs/payer-sources/MATRIX.md` rows for the new UMR entries + a note on the
  UnitedHealthcare Community Plan fix (this file is hand-maintained, not generated —
  confirmed no generator script exists under `scripts/`).
- Test coverage in `tests/test_payer_sources.py` mirroring the existing UHC/Community Plan
  assertions, plus a regression test for the bug fix.

**Out of scope (explicitly not doing here):**
- Fixing the Organization-name-resolution quirk in `FhirPdexAdapter._org_name` (finding
  #3) — pre-existing, affects all UHC-family rows equally, separate investigation.
- Oxford Freedom, UnitedHealthcare Dental PPO.
- Resolving a specific Stedi payer id for UMR (genuinely ambiguous — 19+ candidates,
  no way to pick one without per-employer-group data).
- Medicaid work — separate sub-project, next after this one.

## Design

### `src/network_probe/payers/roster_seed.py`

- Add `_UHC_FHIR = "https://flex.optum.com/fhirpublic/R4"` near the other shared
  constants (`_CENTENE_FHIR`, `_HCSC_FHIR`).
- `SOURCES["UnitedHealthcare"]` and `SOURCES["UnitedHealthcare Community Plan"]`: replace
  the inline URL / `None` with `_UHC_FHIR`.
- Add `SOURCES["UMR"] = (_UHC_FHIR, "https://transparency-in-coverage.uhc.com/",
  "https://www.umr.com/find-a-provider", "public-fhir")`, with a comment documenting
  finding #1 (rides UHC's own networks) and pointing at this spec for the fuller writeup.
- Add 9 `ROSTER` tuples: `("UMR", "Commercial", <state>, None, "needs_payer_id")` for
  each state market listed above, placed alongside the existing UnitedHealthcare rows in
  each state's section.

### `tests/test_payer_sources.py`

- Extend `test_seeded_fhir_base_urls_present` (or add a new test) asserting
  `SOURCES["UMR"]["fhir_base_url"] == _UHC_FHIR` and that every UMR roster row carries
  `directory_access == "public-fhir"`.
- Add a regression test asserting `SOURCES["UnitedHealthcare Community Plan"][0]` is
  truthy (catches the bug from recurring).
- Confirm `test_public_fhir_rows_have_fhir_or_existing_adapter` passes with no changes
  needed to `existing_adapter_labels` (both fixes route through the `fhir_base_url`
  branch of that assertion, not the label-exception branch).

### `docs/payer-sources/MATRIX.md`

- Add UMR rows (one per market) following the existing table format and Dir-access
  legend, citing this spec for the sourcing note.
- Add a short note under the existing UnitedHealthcare Community Plan row (or in the
  "Not yet researched" / errata area, whichever the surrounding structure suggests)
  documenting the bug fix.

## Testing

- `pytest tests/test_payer_sources.py -k "not db"` must go from 1 failing / 21 passing to
  all passing, plus the new UMR-specific assertions.
- Manual live-endpoint spot check (already done during research, will re-run post-change):
  `get_adapter("UMR", catalogue=DbPayerCatalogue())` should return a `FhirPdexAdapter`
  bound to `flex.optum.com`, and `get_adapter("UnitedHealthcare Community Plan", ...)`
  should no longer raise.

## Follow-ups (not this change)

- Investigate the Optum Organization-dereference quirk (finding #3) — could improve
  network-name precision for every UHC-family row, including UMR.
- Medicaid sub-project: Aetna Better Health, Community Care Plan (FL), Meridian Health
  (IL), and HCSC's "Blue Cross Community Health Plans" (IL Medicaid, id `G00621` already
  known) — separate research + design cycle, next.
