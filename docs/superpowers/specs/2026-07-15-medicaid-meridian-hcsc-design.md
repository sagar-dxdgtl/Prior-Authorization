# Medicaid — Meridian Health + HCSC "Blue Cross Community Health Plans" — design

## Summary

Wire directory access for two of the four Medicaid MCOs originally scoped for this sub-project:
**Meridian Health (IL)** and **HCSC's "Blue Cross Community Health Plans" (IL Medicaid)**. Both
are pure data/config fixes reusing already-wired infrastructure — no new adapter code. The other
two items from the original Medicaid scope are explicitly deferred: **Aetna Better Health** (on
hold — blocked on something external, per direct instruction) and **Community Care Plan (FL)**
(materially bigger scope — a new PDF-parser format — split into its own separate spec/plan cycle,
next after this one).

## Research findings

1. **Meridian Health Plan of Illinois has been a wholly-owned Centene subsidiary since 2018** —
   confirmed via Centene's own press releases and recent (2026-07-09) contract-award coverage.
   It's grouped alongside WellCare, Sunshine Health, Buckeye, and the other Centene-family state
   brands already wired to the shared `_CENTENE_FHIR` endpoint
   (`https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory`).

2. **Verified directly against this client's own data, not a generic test.** Kevin Petermann
   (NPI `1588744650`) — the exact provider this repo's original client physician table flagged as
   Meridian Health, IL — is a real, live hit on the shared Centene FHIR directory. Identifier
   search 400s on this server (a known, already-handled limitation — the existing
   `FhirPdexAdapter._find_practitioner` falls back to name search on any `HTTPStatusError`); name
   search for "Petermann"/"Kevin" returns him with NPI `1588744650` matching exactly. His
   `PractitionerRole` entries carry real Illinois-specific networks: `IL SNP`, `Exchange IL`,
   `Exchange Solutions`, `CC National Medicare HMO`, `Exchange Solutions Marathon`.

3. **Network names don't literally say "Meridian" — this is a Centene-platform-wide pattern, not
   a red flag specific to this payer.** Resolved the `IL SNP` network's full Organization record
   (id `15076`): name is `"IL SNP"`, parent `"IL SNP HMO"`, no Meridian branding anywhere in the
   FHIR data. The existing "Superior HealthPlan (Centene)" roster row already accepts this same
   pattern — Centene's national platform doesn't always surface retail brand names in network
   naming, and the codebase already treats "provider present in the shared Centene directory
   with real state-specific network data" as sufficient evidence for `public-fhir`, not "network
   name matches the retail brand."

4. **HCSC's Sapphire FHIR endpoint — already wired, creds already in `.env`
   (`HCSC_FHIR_CLIENT_ID`) — serves a real, fully-populated Medicaid network.** Queried
   `Organization?name=Blue%20Cross%20Community` on `https://api.hcsc.net/providerfinder/sapphire/fhir`
   (same base URL/credential already live for BCBS IL/TX commercial and Medicare Advantage) and
   found `"Blue Cross Community Health Plans℠"` (org id `network-11152019`), active, with
   **637,497** `PractitionerRole` entries referencing it (`PractitionerRole?network=Organization/network-11152019`
   → `total: 637497`). Stedi id `G00621` was already known (see `docs/payer-sources/MATRIX.md`'s
   existing HCSC note) but had no roster row of its own.

5. **Found a routing bug before writing any code, not after.** `_authed_builder_for()`
   (`src/network_probe/domain/service.py:115-121`) selects the HCSC vs. Anthem credential
   builder by substring-matching `"hcsc"` against the row's `key`/`label`. A brand-new label like
   `"Blue Cross Community Health Plans"` contains neither `"hcsc"` nor `"anthem"`/`"elevance"`, so
   it would silently fall through to `builder = None` and raise a misleading "credentials not
   configured" `ValueError` — even with real, working creds. The chosen design avoids this
   entirely (see below) rather than patching the string match.

## Scope

**In scope:**
- `SOURCES["Meridian Health"]`: `fhir_base_url` `None → _CENTENE_FHIR`, `directory_access`
  `"none" → "public-fhir"`. No `ROSTER` change — its existing `stedi_payer_id="13189"` and
  `enrollment_status="needs_enrollment"` are already correct.
- One new `ROSTER` row: `("BCBS / Empire (Anthem / Elevance)(HCSC)", "Managed Medicaid", "IL",
  "G00621", "needs_enrollment")` — a 4th benefit-type row under the *existing* HCSC label
  (matches the roster's established one-label-multiple-benefit-types convention, e.g. `"Aetna"`
  already carries both Commercial and Medicare Advantage). No new `SOURCES` entry needed — the
  existing `"BCBS / Empire (Anthem / Elevance)(HCSC)"` SOURCES tuple (`_HCSC_FHIR`, `None`,
  `None`, `"authorized-fhir"`) already applies uniformly across every benefit type for this
  label, exactly as it already does for the IL/TX-Houston/TX-Dallas ACA/Commercial/Medicare
  Advantage rows.
- A comment on that new row documenting the retail brand name (`"Blue Cross Community Health
  Plans℠"`) and the live-verification evidence, so a future reader isn't confused by the
  label/brand mismatch.
- `docs/payer-sources/MATRIX.md`: update the Meridian Health row, add the new HCSC Medicaid row,
  update the existing HCSC note that currently says "no roster row of its own yet."
- Test coverage in `tests/test_payer_sources.py` proving both fixes.

**Explicitly out of scope:**
- **Aetna Better Health** — on hold per direct instruction, not touched.
- **Community Care Plan (FL)** — separate sub-project (new PDF-parser format required; its own
  spec/plan cycle, next after this one).
- Creating a new roster **label** for the HCSC Medicaid product — rejected in favor of reusing
  the existing label (see finding #5 and Design below).
- Fixing `_authed_builder_for()`'s string-matching mechanism itself — not needed once the
  existing-label approach is used; not touched.
- Any change to the shared `_CENTENE_FHIR` or `_HCSC_FHIR` constants, or to any adapter class.

## Design

### `src/network_probe/payers/roster_seed.py`

**Meridian Health fix** — find the existing `SOURCES["Meridian Health"]` tuple:
```python
    "Meridian Health": (
        # Illinois Medicaid MCO. Its own "Find a Provider" tool is a JS SPA (no public FHIR/API
        # found) -- same treatment as other directory-access=none payers.
        None,
        None,
        "https://findaprovider.ilmeridian.com",
        "none",
    ),
```
Replace with a tuple using `_CENTENE_FHIR` and `"public-fhir"`, with a comment documenting: (a)
Meridian is a confirmed Centene subsidiary since 2018, (b) the client's own Kevin Petermann
(NPI 1588744650) is a live, verified hit on the shared Centene directory with real IL network
data, (c) the retail "Meridian" brand name doesn't surface in Centene's network naming — a
platform-wide pattern already accepted for Superior HealthPlan, not a Meridian-specific gap.

**HCSC Medicaid row** — add one `ROSTER` tuple in the IL section, alongside the other HCSC IL
rows: `("BCBS / Empire (Anthem / Elevance)(HCSC)", "Managed Medicaid", "IL", "G00621",
"needs_enrollment")`. No `SOURCES` change — the existing HCSC tuple already covers it via the
label match. Two comment additions, matching this file's existing precedent of a per-row comment
block directly above a `ROSTER` tuple (e.g. the existing Meridian/Noridian/Community-Health-Choice
rows already do this):
1. A short comment block directly above the new `ROSTER` line, documenting the retail brand name
   (`"Blue Cross Community Health Plans℠"`) and the live verification (Organization
   `network-11152019`, 637,497 `PractitionerRole` entries).
2. One added sentence in the existing `SOURCES["BCBS / Empire (Anthem / Elevance)(HCSC)"]`
   comment noting that this label's `authorized-fhir` routing now also covers a Managed Medicaid
   row, so a reader of the `SOURCES` block (which doesn't show `ROSTER` rows) isn't left thinking
   this label is commercial/MA-only.

### `tests/test_payer_sources.py`

- Extend the existing `_FHIR_PAYERS`-style verification (or a dedicated assertion) to confirm
  `SOURCES["Meridian Health"]["fhir_base_url"] == CENTENE_FHIR` and
  `directory_access == "public-fhir"`.
- Add an assertion that the new HCSC Medicaid roster row exists with `label="BCBS / Empire
  (Anthem / Elevance)(HCSC)"`, `benefit_type="Managed Medicaid"`, `state="IL"`,
  `stedi_payer_id="G00621"`, and that it inherits `fhir_base_url=_HCSC_FHIR` /
  `directory_access="authorized-fhir"` from the existing label-level SOURCES entry (proving the
  "reuse the existing label" design decision actually works end-to-end, not just in theory).
- A regression test proving finding #5 doesn't recur: assert that routing a payer through
  `get_adapter()` for this label continues to resolve via `_build_hcsc_adapter`, not silently via
  `None`/a misleading credentials error — i.e. confirm the new Managed Medicaid row is *not* a
  separate label that would bypass the `"hcsc"` substring match.

## Testing

- `pytest tests/test_payer_sources.py -k "not db"` — all existing tests continue to pass, plus
  the new assertions above.
- Manual live smoke test (mirrors the UMR plan's Task 4 pattern): resolve `"Meridian Health"` and
  the new HCSC Medicaid row via the real `roster_seed.SOURCES`/`payer_rows()`, confirm
  `get_adapter()` for each returns a `FhirPdexAdapter` bound to the expected base URL, with no
  live network calls required (the org/practitioner-count evidence above was already gathered
  live during research, not deferred to this step).

## Follow-ups (not this change)

- **Community Care Plan (FL)** — separate sub-project: 19 per-county PDF directories exist and
  are live (verified `ProviderDirectory_Broward.pdf`, 1,933 pages, dated 2026-07-13), well
  structured but require a new PDF-parser `format` in `directory_pdf.py` (distinct from the
  existing `allyalign`/`aaneel` formats) plus handling 3 relevant South Florida county files
  (Broward, Miami-Dade, Palm Beach) rather than the loader's current single-PDF-per-payer
  assumption. Own research/design/plan cycle, next.
- **Aetna Better Health** — on hold, revisit when unblocked.
