# Community Care Plan (FL) PDF-directory design

## Summary

Wire Community Care Plan (FL Medicaid MCO, `"Community Care Plan"` / `"Managed Medicaid"` /
`"FL-South Florida"` in the roster, currently `directory_access="none"`) to its real, live,
monthly-updated provider directory — published as 3 per-county PDFs (Broward, Miami-Dade, Palm
Beach), not a FHIR API. Adds a third PDF parser format (`"ccp"`) alongside the existing
`allyalign`/`aaneel` formats, and extends the load pipeline to support multiple PDF URLs feeding
one payer's directory rows (a genuine gap — today's config only supports one PDF per payer).

## Research findings

1. **All 3 relevant county PDFs confirmed live and structurally identical.** Broward (5.7MB,
   1,933 pages), Miami-Dade (9.1MB, 3,140 pages), Palm Beach (3.2MB, 1,046 pages) — all dated "As
   of 07/13/2026", all static URLs
   (`https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_<County>.pdf`, no
   date-stamp discovery needed, simpler than `eternalhealth-az`'s `page_url`+`link_pattern`
   pattern). All 3 feed the single existing `"FL-South Florida"` roster row — confirmed with the
   user this covers the full relevant market, not just one county.

2. **CCP's format is a genuine third structure, not a variant of the existing two.** Each record
   is fully self-contained: `NAME / SPECIALTY / ADDRESS / CITY,ST ZIP / Phone: / Office Hours: /
   Gender Accepted: / Cultural Competence: / WheelChair Accessible: / Board Certification: /
   Accepting New Patients: / Age Limitations: / Website: / Performance Indicator:`. Unlike
   `allyalign` (one name header, multiple location sub-blocks needing a walk-loop), a CCP provider
   at 2 locations just appears as 2 complete, separate records — simpler to parse, not harder.
   Two concrete incompatibilities with existing regexes in `directory_pdf.py`:
   - The existing `_CSZ` regex expects `"CITY, ST, ZIP"` (comma before the state). CCP prints
     `"CITY, ST ZIP"` (space before zip, e.g. `"PLANTATION, FL 33324"`) — needs its own regex.
   - CCP's specialty-section header is a 3-line group (`"PCP - ADOLESCENT MEDICINE"` / county name
     / `"4 of 1933"`), not `allyalign`'s single-line `"CARDIOLOGY (261)"`.

3. **Schema fit is clean with no changes needed.** `PayerDirectoryEntry`
   (`src/network_probe/db/models.py`) already has exactly the columns CCP's data needs — name,
   specialty, address, city, state, zip, accepting_new. Most of CCP's extra PDF fields (Gender
   Accepted, Cultural Competence, WheelChair Accessible, Board Certification, Age Limitations,
   Website, Performance Indicator) have no column and should be parsed-and-dropped, exactly how
   `allyalign`/`aaneel` already ignore fields they don't use — not a schema migration.

4. **Real architectural gap: today's config supports exactly one PDF per payer.**
   `PDF_DIRECTORIES[payer_key]` → `resolve_pdf_url(cfg)` → one URL → one download → one parse →
   one `_replace_rows()`. CCP needs 3 URLs feeding the *same* `payer_key`
   (`community-care-plan-fl-south-florida`, matching the roster slug) — not 3 separate roster
   rows, since the roster's `"FL-South Florida"` label doesn't distinguish counties and
   `DbDirectoryAdapter` (the read side) queries by a single `payer_key` string.

5. **`DbDirectoryAdapter` and `directory_match.py` need zero changes.** Confirmed: they query
   `payer_directory_entries` by `payer_key` + `last_name` only, with no awareness of how many
   source PDFs fed that `payer_key`. This constrains the design — the fix belongs entirely in the
   load/ingestion side, never the read side.

6. **Existing test convention is one shared file, not per-format files.**
   `tests/test_directory.py` already covers `allyalign` (`test_parse_lines_extracts_records`,
   `test_parse_lines_multi_location_one_provider`) and `aaneel` (`test_parse_lines_aaneel`) via
   unit tests against raw captured line arrays (not full PDF fixtures — fast, offline). A new
   `"ccp"` format's tests extend this same file, not a new one.

## Scope

**In scope:**
- A new `parse_lines_ccp(lines: list[str]) -> list[DirectoryEntry]` function in
  `src/network_probe/domain/directory_pdf.py`, wired into `parse_directory_pdf()`'s `fmt`
  dispatch alongside the existing two formats. Its own regexes for the two structural
  differences found (finding #2) — not a reuse of `_CSZ` or the `allyalign` specialty-header
  logic, since both are format-specific and CCP's shapes differ.
- Extending `PDF_DIRECTORIES` config entries to accept an optional `pdf_urls: list[str]` (plural)
  alongside the existing singular `pdf_url` / `page_url`+`link_pattern`. `load_directory()`
  downloads and parses each URL when `pdf_urls` is set, concatenates the resulting rows, and
  performs the existing single atomic `_replace_rows()` call once, over the concatenated set.
- **Error handling: all-or-nothing per payer.** If any one of CCP's 3 county downloads or parses
  fails, the whole load for that `payer_key` aborts (raises) before reaching `_replace_rows()` —
  the previous month's data stays in place rather than silently ending up with 2 of 3 counties.
  Matches `monthly_refresh_loop()`'s existing per-payer `try/except: pass` isolation (one payer's
  failure doesn't kill the loop for other payers) — this is about not letting one payer's *partial*
  failure produce inconsistent data for *that* payer, a level below the existing isolation.
- New `PDF_DIRECTORIES["community-care-plan-fl-south-florida"]` entry: `{"label": "Community Care
  Plan", "format": "ccp", "pdf_urls": [the 3 static county URLs]}`.
- `SOURCES["Community Care Plan"]`: `directory_access` flips from `"none"` to `"pdf-directory"` —
  matching the `Align Senior Health Plan`/`EternalHealth` pattern exactly. No `ROSTER` change
  needed (the row already exists with the right label/state/benefit_type).
- Test coverage in `tests/test_directory.py`: unit tests for `parse_lines_ccp()` against raw
  captured line arrays (a real short block, transcribed from the live Broward sample), and a test
  proving `load_directory()` concatenates rows from multiple URLs and — the all-or-nothing
  guarantee — does *not* call `_replace_rows()` at all if one of several URLs fails to download.
- `docs/payer-sources/MATRIX.md`: update the existing Community Care Plan row's `Dir access`
  column and note (currently `none`, "PDF-only directory" already flagged as a lead in the
  existing note — this closes that lead).

**Explicitly out of scope:**
- Any change to `DbDirectoryAdapter`, `directory_match.py`, or the `PayerDirectoryEntry` schema
  (findings #3, #5 — confirmed no changes needed).
- The Florida Healthy Kids (`/fhk`) or CCP-employee (`/ccp`) directories on the same site — the
  existing roster row is specifically `"Managed Medicaid"`, matching only the `/mma` directory
  already scoped in this design.
- Any other PDF-only payer not already in `PDF_DIRECTORIES` (e.g. a hypothetical future payer
  needing this same multi-URL support) — the config change generalizes naturally, but this design
  only wires Community Care Plan.
- Aetna Better Health — remains on hold from the earlier Medicaid sub-project, unrelated to this
  one.

## Design

### `src/network_probe/domain/directory_pdf.py`

- New module-level regexes: a CCP-specific city/state/zip pattern (space before zip, not comma)
  and a specialty-header detector for the 3-line group.
- `parse_lines_ccp(lines: list[str]) -> list[DirectoryEntry]`: walks the flat, self-contained
  record structure. Each record's `NAME`/`SPECIALTY`/`ADDRESS`/`CITY,ST ZIP` lines map directly to
  a single `DirectoryEntry` with one entry in `locations`; `Accepting New Patients:` maps to
  `accepting_new` (reusing the existing `_ACCEPTING` regex, which already matches
  `"Accepting New Patients:\s*(\w+)"` verbatim against CCP's field). All of CCP's other labeled
  fields (Office Hours, Gender Accepted, Cultural Competence, WheelChair Accessible, Board
  Certification, Age Limitations, Website, Performance Indicator) are consumed as line-skip
  during the walk — parsed past, never stored (no `DirectoryEntry` field for them, matching
  finding #3).
- `parse_directory_pdf()`'s `fmt` dispatch gains a third branch: `"ccp"` → `parse_lines_ccp(lines)`.

### `src/network_probe/domain/directory_load.py`

- `PDF_DIRECTORIES` config shape gains an optional `pdf_urls: list[str]` key, used instead of
  (never alongside) the existing singular `pdf_url`.
- New `resolve_pdf_urls(cfg) -> list[str]` becomes the *one* URL-resolution entry point
  `load_directory()` calls, replacing direct calls to the existing singular `resolve_pdf_url()` —
  avoiding two parallel code paths in `load_directory()` for "one URL" vs. "many URLs". It always
  returns a list: `cfg["pdf_urls"]` when plural is set, or a single-item list wrapping whatever
  the existing `resolve_pdf_url(cfg)` resolves to otherwise (which itself is untouched — it
  already handles both the static `pdf_url` case and EternalHealth's `page_url`+`link_pattern`
  discovery case, so `resolve_pdf_urls()` delegates to it rather than duplicating that logic).
  Net effect: `resolve_pdf_url()` keeps its current behavior and signature exactly as-is; only a
  new, thin wrapper is added around it.
- `load_directory()`: always calls `resolve_pdf_urls(cfg)` and iterates over the result (a list of
  1 for every existing payer, 3 for CCP) — no special-casing between single- and multi-URL payers
  in this function's control flow. Downloads and parses each URL in turn, concatenating the
  resulting row lists (via the existing `rows_from_pdf()`, called once per downloaded file with
  the same `payer_key`/`version`) before the single `_replace_rows()` call. If any individual
  download or parse raises, the whole function raises before reaching `_replace_rows()` — no
  partial replace.

### `src/network_probe/payers/roster_seed.py`

- `SOURCES["Community Care Plan"]`: 4th tuple element (`directory_access`) changes from `"none"`
  to `"pdf-directory"`. `fhir_base_url` and `tic_url` stay `None` (no FHIR/TiC exists for this
  payer — a PDF is the only source, same as Align/EternalHealth). `directory_url` stays the
  existing `"https://providerdirectory.ccpcares.org/mma"` link.

### `tests/test_directory.py`

- `test_parse_lines_ccp_extracts_records`: a real short line-array transcribed from the live
  Broward sample, proving name/specialty/address/city/state/zip/accepting_new extraction.
- `test_parse_lines_ccp_two_locations_are_two_entries`: proves the "same name, two full records"
  shape (found live for "FLORENT-CARRE MARIE" in the Broward PDF) produces two `DirectoryEntry`
  objects, not one entry with two locations — confirming this format's simpler contract vs.
  `allyalign`.
- `test_load_directory_concatenates_multiple_urls`: mocks 2-3 small PDFs (or pre-parsed row lists,
  whichever keeps the test fast/offline) and proves `load_directory()`'s resulting row count is
  the sum across URLs.
- `test_load_directory_aborts_on_partial_failure`: one URL's download raises → asserts
  `_replace_rows()` was never called (previous data untouched) and the exception propagates.

## Testing

- `pytest tests/test_directory.py -v` — existing `allyalign`/`aaneel`/match/adapter tests continue
  to pass unmodified, plus the new CCP-format and multi-URL tests above.
- `pytest tests/test_payer_sources.py -k "not db"` — regression check; `directory_access` change
  for Community Care Plan should be the only diff, no other row affected.
- Manual live verification: run `load_directory("community-care-plan-fl-south-florida")` for real
  against the 3 live URLs (this does download ~18MB total — acceptable, matches the existing
  `ENABLE_DIRECTORY_REFRESH`-gated live-load pattern) and confirm a real, non-trivial row count
  lands in `payer_directory_entries`, then spot-check `DbDirectoryAdapter.check_network()` against
  a couple of real provider names pulled from the live PDF sample (e.g. "Florent-Carre" from the
  Broward sample already gathered during research) to prove the read side genuinely works
  end-to-end with no code changes on that side.

## Follow-ups (not this change)

- Aetna Better Health remains on hold (unrelated, carried over from the Medicaid sub-project).
- No other open follow-ups specific to this sub-project — the two adapter-level issues found
  earlier this session (UHC/Optum Organization-dereference quirk; the now-fixed Centene
  practitioner-reference bug) are unrelated to the PDF-directory code path touched here.
