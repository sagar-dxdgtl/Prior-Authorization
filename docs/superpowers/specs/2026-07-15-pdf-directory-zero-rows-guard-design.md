# PDF-directory zero-rows guard — design

## Summary

Harden `load_directory()` (`src/network_probe/domain/directory_load.py`) against silently
replacing a payer's entire provider directory with nothing. Today, if a PDF parses to zero rows
(no exception — a parser/structure drift that just doesn't match anything, rather than an error),
`_replace_rows()` still runs unconditionally, wiping that payer's directory and turning every real
provider into a false `OUT_OF_NETWORK` instead of the honest `UNKNOWN` an empty-directory-detection
would produce. Found during the final whole-branch review of the Community Care Plan sub-project
(already merged); affects all 3 PDF-directory payers already wired (Align, EternalHealth,
Community Care Plan), not just the newest one, since all 3 now share the same
`load_directory()` → `resolve_pdf_urls()` → loop → `_rows_from_url()` path after the multi-URL
refactor.

## Scope

**In scope:**
- A zero-rows guard in `load_directory()`'s per-URL loop: if any single URL's `_rows_from_url()`
  call returns an empty list, raise immediately — before that URL's rows are added to the running
  total, before any further URLs are fetched, and before `_replace_rows()` is ever reached. The
  existing `pdf_path`/`pdf_bytes` single-file override branch gets the same guard.
- Two new tests in `tests/test_directory.py`: one proving a multi-URL payer aborts when one of
  several URLs returns zero rows (parallel to the existing
  `test_load_directory_aborts_on_partial_failure`, but triggered by an empty result instead of an
  exception), one proving a single-URL payer (the common case — Align, EternalHealth) gets the
  same protection.

**Explicitly out of scope (per user decision):**
- Row-count-collapse detection against the previously loaded version (comparing new vs. old
  totals via a DB query) — a real but subtler failure mode (a PDF that still parses successfully
  but has fewer real entries than before), deferred as a separate, more complex enhancement with
  its own threshold-tuning question. Not needed to close the specific gap found.
- Any change to the parsers themselves (`parse_lines`, `parse_lines_aaneel`, `parse_lines_ccp`) —
  this is a load-orchestration guard, not a parsing fix.
- Aetna website-scraping research — unrelated, tracked as a separate sub-project.

## Design

### `src/network_probe/domain/directory_load.py`

In `load_directory()`'s multi-URL loop (the `else` branch that runs when neither `pdf_path` nor
`pdf_bytes` is given), after each `_rows_from_url()` call: if it returned an empty list, raise a
`ValueError` naming the offending URL and payer key, before extending `rows` and before
proceeding to the next URL (so a single bad county aborts the whole load immediately, not after
wastefully downloading the remaining ones — and definitely before `_replace_rows()`).

In the `pdf_path`/`pdf_bytes` override branch: the same check applies to `rows_from_pdf()`'s
result — if it's empty, raise before `_replace_rows()`. This branch is primarily used by tests and
one-off loads today, but keeping it consistent means a future caller of the override path gets the
same safety property, not a silent gap reintroduced later.

Error message should include the payer_key and (for the multi-URL case) the specific URL, so an
on-call engineer reading a failed monthly-refresh log immediately knows which source broke,
without having to reproduce the failure to find out.

### `tests/test_directory.py`

- `test_load_directory_aborts_on_empty_url_result`: mirrors the existing
  `test_load_directory_aborts_on_partial_failure`'s structure (3-URL config, `_replace_rows`
  tracked via monkeypatch) but makes `rows_from_pdf` return `[]` for one URL instead of raising —
  asserts `load_directory()` raises `ValueError` and `_replace_rows` is never called.
- `test_load_directory_aborts_on_empty_single_url_result`: the same guard, but for a single-URL
  config (`pdf_url`, not `pdf_urls`) — proving Align/EternalHealth-shaped payers get the identical
  protection, not just multi-URL ones.

## Testing

- `pytest tests/test_directory.py -v` — existing tests continue to pass unmodified (none of them
  exercise an empty-but-not-exceptional PDF result), plus the 2 new tests above.
- `pytest tests/test_payer_sources.py -k "not db"` — unrelated-regression check; this change
  touches no roster/catalogue code.
- No live verification needed — this is a defensive guard against a failure mode that hasn't
  happened yet with the real live PDFs (all 3 payers currently parse successfully), not a fix for
  an observed live bug. The 2 new offline tests are the coverage.

## Follow-ups (not this change)

- Row-count-collapse detection (comparing against the previously loaded version) — deferred, own
  design question if pursued later.
- Aetna website-scraping feasibility — separate research thread, own spec if it proves viable.
