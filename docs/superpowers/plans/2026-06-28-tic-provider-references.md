# TiC External provider_reference.location Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `ingest_tic()` to resolve external `provider_references[].location` URLs (Cigna/Aetna style) so these payers no longer silently return 0 rows.

**Architecture:** Add an injectable `reference_resolver` callable to `ingest_tic()`. A new third pass collects location URLs from `provider_references` items that have no inline `provider_groups`, resolves them concurrently via `ThreadPoolExecutor`, extracts `provider_groups` from the referenced JSON (handling dict-with-key, bare-list, and single-group shapes), and pipes them through the existing `_emit()` path. Inline behavior (UHC-style) is fully unchanged.

**Tech Stack:** Python 3.12, ijson (streaming), httpx (default resolver), gzip/urllib, concurrent.futures.ThreadPoolExecutor, pytest, ruff

## Global Constraints

- Python 3.12 — no walrus-operator workarounds needed
- `src/` layout, absolute imports (`from network_probe.domain.tic_ingest import ...`)
- Branch: `feat/tic-provider-references` off `main`
- `pytest -m "not live and not db"` must stay green
- `ruff check src tests scripts` must be clean
- No live network in tests — inject a fake resolver
- Do NOT stage `.env`
- httpx already in dependencies (version 0.28.1)
- Line length: 120 (ruff config)

---

### Task 1: Write failing tests for external location resolution

**Files:**
- Create: `tests/fixtures/tic-location-refs.json` — Cigna-style fixture (location pointers, no inline groups)
- Create: `tests/fixtures/tic-mixed.json` — mixed file (some inline groups + some location refs)
- Modify: `tests/test_tic_ingest.py` — add four new test functions

**Interfaces:**
- Consumes: `ingest_tic(tic_path, out_csv, ..., reference_resolver=None)` (does not exist yet — tests will FAIL until Task 2)
- Produces: four new test functions that will turn green after Task 2

- [ ] **Step 1: Create the Cigna-style fixture (location refs only)**

Create `tests/fixtures/tic-location-refs.json`:
```json
{"provider_references": [
   {"provider_group_id": 1, "location": "loc://az/1"},
   {"provider_group_id": 2, "location": "loc://az/2"}
 ],
 "in_network": []}
```

- [ ] **Step 2: Create the mixed fixture (inline + location)**

Create `tests/fixtures/tic-mixed.json`:
```json
{"provider_references": [
   {"provider_group_id": 1, "provider_groups": [
       {"npi": [1679766943], "tin": {"type": "ein", "value": "463812940"}}]},
   {"provider_group_id": 2, "location": "loc://az/1"}
 ],
 "in_network": []}
```

- [ ] **Step 3: Write the four failing test functions**

Add to `tests/test_tic_ingest.py` (after the existing tests):

```python
# ---------------------------------------------------------------------------
# External provider_reference.location resolution (Cigna-style)
# ---------------------------------------------------------------------------

import pytest

_FAKE_RESOLVER_DATA = {
    "loc://az/1": {
        "provider_groups": [
            {"npi": [1972603934, 1710305735], "tin": {"type": "ein", "value": "933510922"}}
        ]
    },
    "loc://az/2": {
        "provider_groups": [
            {"npi": [1689726403], "tin": {"type": "ein", "value": "112233445"}}
        ]
    },
}

_FIXTURE_LOCATION = Path(__file__).parent / "fixtures" / "tic-location-refs.json"
_FIXTURE_MIXED = Path(__file__).parent / "fixtures" / "tic-mixed.json"


def _fake_resolver(url: str):
    return _FAKE_RESOLVER_DATA[url]


def test_location_refs_emits_rows_from_resolved_files(tmp_path):
    """Pure location-ref file: resolver called, 3 rows emitted across both refs."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_LOCATION),
        out,
        payer="cigna",
        reference_resolver=_fake_resolver,
    )
    assert n == 3
    rows = _read_rows(out)
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1972603934", "933510922") in pairs
    assert ("1710305735", "933510922") in pairs
    assert ("1689726403", "112233445") in pairs


def test_location_refs_with_tin_filter(tmp_path):
    """tin_filter={"933510922"} with location refs → only 2 NPIs under that TIN emitted."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_LOCATION),
        out,
        tin_filter={"933510922"},
        payer="cigna",
        reference_resolver=_fake_resolver,
    )
    assert n == 2
    rows = _read_rows(out)
    npis = {r["npi"] for r in rows}
    assert npis == {"1972603934", "1710305735"}


def test_mixed_inline_and_location_both_contribute(tmp_path):
    """Mixed file: inline group (463812940) + resolved location (933510922) both appear."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_MIXED),
        out,
        payer="aetna",
        reference_resolver=_fake_resolver,
    )
    assert n == 3
    rows = _read_rows(out)
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1679766943", "463812940") in pairs   # inline
    assert ("1972603934", "933510922") in pairs   # resolved
    assert ("1710305735", "933510922") in pairs   # resolved


def test_resolver_error_skips_ref_continues_run(tmp_path):
    """When resolver raises for one URL, that ref is skipped; others still emitted."""
    def _flaky_resolver(url: str):
        if url == "loc://az/1":
            raise RuntimeError("simulated CDN error")
        return _FAKE_RESOLVER_DATA[url]

    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_LOCATION),
        out,
        payer="cigna",
        reference_resolver=_flaky_resolver,
    )
    # loc://az/2 still resolves → 1 NPI from it
    assert n == 1
    rows = _read_rows(out)
    assert rows[0]["npi"] == "1689726403"
    assert rows[0]["tin"] == "112233445"


def test_no_resolver_with_location_file_returns_0(tmp_path):
    """Without a resolver, location-only file silently returns 0 (existing behavior guard)."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(str(_FIXTURE_LOCATION), out, payer="cigna")
    assert n == 0
```

- [ ] **Step 4: Run the new tests to confirm they FAIL for the right reason**

```bash
cd C:\Users\user\Desktop\code\dxdgtl\Prior-Authorization
python -m pytest tests/test_tic_ingest.py::test_location_refs_emits_rows_from_resolved_files tests/test_tic_ingest.py::test_location_refs_with_tin_filter tests/test_tic_ingest.py::test_mixed_inline_and_location_both_contribute tests/test_tic_ingest.py::test_resolver_error_skips_ref_continues_run tests/test_tic_ingest.py::test_no_resolver_with_location_file_returns_0 -v
```

Expected: FAIL — `TypeError: ingest_tic() got an unexpected keyword argument 'reference_resolver'`

- [ ] **Step 5: Confirm existing tests still pass**

```bash
python -m pytest tests/test_tic_ingest.py -v -m "not live and not db" --ignore-glob="*live*"
```

Expected: the original 9 tests PASS, the 5 new ones FAIL.

- [ ] **Step 6: Commit fixtures and failing tests**

```bash
git add tests/fixtures/tic-location-refs.json tests/fixtures/tic-mixed.json tests/test_tic_ingest.py
git commit -m "test(tic): failing tests for external provider_reference.location resolution"
```

---

### Task 2: Implement reference_resolver support in ingest_tic

**Files:**
- Modify: `src/network_probe/domain/tic_ingest.py`

**Interfaces:**
- Consumes: existing `_emit()`, `_open()`, `_normalize_tin()` helpers
- Produces: `ingest_tic(..., reference_resolver=None, max_workers=16)` — new signature; `_default_resolver(url: str) -> dict` — module-level default; `_extract_provider_groups(data) -> list` — normalizes the three JSON shapes

- [ ] **Step 1: Write the full updated `tic_ingest.py`**

Replace the body of `src/network_probe/domain/tic_ingest.py` with:

```python
"""Streaming TiC in-network MRF → NPI→TIN crosswalk CSV ingester.

Extracts provider_groups from three sources:

1. Top-level ``provider_references[]`` with **inline** ``provider_groups``
   (UHC-style).
2. ``in_network[].negotiated_rates[]`` embedded ``provider_groups``.
3. Top-level ``provider_references[]`` with a ``location`` URL pointing to
   an **external** file containing provider_groups (Cigna / modern Aetna
   style).  URLs are resolved concurrently; failures on individual URLs are
   logged and skipped — the run continues.

Filtering
---------
* ``npi_filter`` — keep only rows whose NPI is in the set.
* ``tin_filter`` — keep only provider_groups whose TIN is in the set.
  Normalization strips all non-digit characters before comparison, so
  ``"93-3510922"`` and ``"933510922"`` are equivalent.
* Both filters — a row must satisfy **both** (intersection).
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import ijson

logger = logging.getLogger(__name__)


def _open(path: str):
    p = Path(path)
    return gzip.open(p, "rb") if str(p).endswith(".gz") else open(p, "rb")


def _normalize_tin(tin: str) -> str:
    """Strip all non-digit characters from a TIN string."""
    return re.sub(r"\D", "", tin)


def _emit(groups, npi_filter, tin_filter, payer: str | None, writer, seen: set) -> None:
    """Write unique (npi, tin) rows from a provider_groups list.

    When ``tin_filter`` is provided, skip any provider_group whose normalized
    ``tin.value`` is not in the set.  When ``npi_filter`` is provided, skip
    any NPI not in the set.  Both filters compose as an intersection.
    """
    for g in groups or []:
        tin = (g.get("tin") or {}).get("value")
        if not tin:
            continue
        if tin_filter is not None and _normalize_tin(str(tin)) not in tin_filter:
            continue
        for npi in g.get("npi") or []:
            npi = str(npi)
            if npi_filter is not None and npi not in npi_filter:
                continue
            key = (npi, str(tin))
            if key not in seen:
                seen.add(key)
                writer.writerow([npi, tin, payer or ""])


def _default_resolver(url: str) -> dict | list:
    """Fetch *url* and return the parsed JSON body.

    Handles both plain JSON and gzip-compressed responses.  Uses httpx so
    that the same HTTP client config (timeouts, proxies) applies everywhere.
    Raises on any HTTP or parse error — callers must handle exceptions.
    """
    import httpx

    with httpx.Client(follow_redirects=True, timeout=60) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.content
        # Some CDNs serve .json.gz with Content-Type: application/json
        if url.endswith(".gz") or resp.headers.get("content-encoding") == "gzip":
            content = gzip.decompress(content)
        return __import__("json").loads(content)


def _extract_provider_groups(data) -> list:
    """Normalise the three shapes a resolved reference file may take.

    * ``{"provider_groups": [...]}``  — standard shape
    * ``[{...}, ...]``                — bare list of groups
    * ``{"npi": [...], "tin": {...}}`` — single group (treat as one-element list)
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "provider_groups" in data:
            return data["provider_groups"] or []
        # single-group dict
        if "npi" in data and "tin" in data:
            return [data]
    return []


def ingest_tic(
    tic_path: str,
    out_csv: str,
    npi_filter=None,
    tin_filter=None,
    payer: str | None = None,
    reference_resolver: Callable[[str], dict | list] | None = None,
    max_workers: int = 16,
) -> int:
    """Stream a TiC in-network MRF (.json/.json.gz) → npi,tin,payer CSV.

    Returns the number of unique (npi, tin) rows written.

    Extracts provider_groups from three sources:

    1. ``provider_references[]`` with inline ``provider_groups`` (UHC-style).
    2. ``in_network[].negotiated_rates[]`` embedded ``provider_groups``.
    3. ``provider_references[]`` with ``location`` URLs resolved via
       ``reference_resolver`` (Cigna / modern Aetna style).

    Parameters
    ----------
    tic_path:
        Path to the TiC in-network MRF file (.json or .json.gz).
    out_csv:
        Destination CSV path (columns: npi, tin, payer).
    npi_filter:
        Optional iterable of NPIs (strings or ints) to keep; others skipped.
    tin_filter:
        Optional iterable of TINs (strings) to keep.  Non-digit characters
        are stripped before comparison, so ``"93-3510922"`` matches
        ``"933510922"``.  When set, only provider_groups whose TIN normalizes
        to a value in the set are included.  When combined with
        ``npi_filter``, a row must pass both filters.
    payer:
        Payer label written to the ``payer`` column.
    reference_resolver:
        Callable ``(url: str) -> parsed_json`` used to fetch external
        provider-reference files.  Defaults to ``_default_resolver`` which
        uses httpx and handles gzip.  Pass a fake callable in tests to avoid
        network access.  Errors on individual URLs are logged and skipped —
        the run does not abort.  When ``None`` and no ``location`` URLs are
        present, behaviour is identical to the previous version.
    max_workers:
        Maximum concurrent threads for resolving location URLs (default 16).
    """
    nf = set(map(str, npi_filter)) if npi_filter else None
    tf = {_normalize_tin(str(t)) for t in tin_filter} if tin_filter else None
    seen: set[tuple[str, str]] = set()

    with open(out_csv, "w", newline="", encoding="utf-8") as outf:
        writer = csv.writer(outf)
        writer.writerow(["npi", "tin", "payer"])

        # Pass 1 — top-level provider_references (inline provider_groups)
        location_urls: list[str] = []
        with _open(tic_path) as f:
            for pr in ijson.items(f, "provider_references.item"):
                if pr.get("provider_groups"):
                    _emit(pr["provider_groups"], nf, tf, payer, writer, seen)
                elif pr.get("location") and reference_resolver is not None:
                    loc = str(pr["location"])
                    if loc not in location_urls:
                        location_urls.append(loc)

        # Pass 2 — provider_groups embedded inside each negotiated_rate
        with _open(tic_path) as f:
            for rate in ijson.items(f, "in_network.item.negotiated_rates.item"):
                _emit(rate.get("provider_groups"), nf, tf, payer, writer, seen)

        # Pass 3 — resolve external location URLs concurrently
        if location_urls and reference_resolver is not None:
            resolver = reference_resolver
            failed = 0

            def _resolve_one(url: str) -> tuple[str, list | None]:
                try:
                    data = resolver(url)
                    return url, _extract_provider_groups(data)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("provider_reference resolver failed for %s: %s", url, exc)
                    return url, None

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_resolve_one, u): u for u in location_urls}
                for fut in as_completed(futures):
                    _, groups = fut.result()
                    if groups is None:
                        failed += 1
                    else:
                        _emit(groups, nf, tf, payer, writer, seen)

            resolved = len(location_urls) - failed
            logger.info(
                "resolved %d external provider-reference files (%d failed)",
                resolved,
                failed,
            )

    return len(seen)
```

- [ ] **Step 2: Run the new tests — they should now PASS**

```bash
python -m pytest tests/test_tic_ingest.py -v -m "not live and not db"
```

Expected: all 14 tests (9 original + 5 new) PASS.

- [ ] **Step 3: Run ruff on the modified file**

```bash
python -m ruff check src/network_probe/domain/tic_ingest.py
```

Expected: no output (clean).

- [ ] **Step 4: Commit**

```bash
git add src/network_probe/domain/tic_ingest.py
git commit -m "feat(tic): resolve external provider_reference.location files (Cigna-style backreferenced MRFs)"
```

---

### Task 3: Update scripts/ingest_tic.py with --resolve-references flag

**Files:**
- Modify: `scripts/ingest_tic.py`

**Interfaces:**
- Consumes: `ingest_tic(..., reference_resolver=None, max_workers=16)` from Task 2
- Produces: `--resolve-references/--no-resolve-references` CLI flags, `--max-workers N`, updated print output

- [ ] **Step 1: Update scripts/ingest_tic.py**

Replace the file with:

```python
"""CLI: stream a TiC in-network MRF into an NPI→TIN crosswalk CSV.

Usage:
    python -m scripts.ingest_tic <tic_path> <out_csv> [--payer PAYER]
        [--npi-file NPI_FILE]
        [--tin-file TIN_FILE] [--tin TIN ...]
        [--resolve-references | --no-resolve-references]
        [--max-workers N]

Arguments:
    tic_path    Path to the TiC in-network MRF (.json or .json.gz).
    out_csv     Destination CSV (columns: npi, tin, payer).

Options:
    --payer      Payer label to write in the ``payer`` column (e.g. "uhc").
    --npi-file   Path to a plain-text file of NPIs to keep (one per line);
                 all other NPIs are skipped, reducing output size.
    --tin-file   Path to a plain-text file of TINs (EINs) to keep, one per
                 line.  Lines beginning with ``#`` are treated as comments
                 and ignored.  Non-digit characters (e.g. dashes) are
                 stripped before comparison, so ``93-3510922`` matches
                 ``933510922``.
    --tin        Individual TIN to keep (repeatable).  May be combined with
                 ``--tin-file``.
    --resolve-references
                 Resolve external ``provider_references[].location`` URLs
                 (Cigna / modern Aetna style).  Default: enabled.
    --no-resolve-references
                 Disable external URL resolution (inline-only mode; safe
                 for UHC-style files and air-gapped environments).
    --max-workers N
                 Number of concurrent threads for resolving location URLs
                 (default: 16).

At least one of ``--npi-file``/``--tin-file``/``--tin`` is optional; if none
are supplied, all provider_groups in the file are written.  When both NPI and
TIN filters are active a row must satisfy both (intersection).

Note on geo-restriction
-----------------------
The default resolver fetches location URLs over the public internet.  Cigna's
provider-reference files are served from a geo-restricted CloudFront CDN that
is only accessible from **US IP addresses**.  UHC's Azure CDN is open.  Always
run this script from a US IP (EC2/Fargate in us-east-1 or us-west-2) when
processing Cigna or Aetna MRFs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from network_probe.domain.tic_ingest import _default_resolver, ingest_tic


def _normalize_tin(tin: str) -> str:
    """Strip all non-digit characters from a TIN string."""
    return re.sub(r"\D", "", tin)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest_tic",
        description="Stream a TiC in-network MRF (.json/.json.gz) → NPI→TIN crosswalk CSV.",
    )
    parser.add_argument("tic_path", help="TiC in-network MRF file (.json or .json.gz)")
    parser.add_argument("out_csv", help="Output CSV path (columns: npi, tin, payer)")
    parser.add_argument("--payer", default=None, help='Payer label for the "payer" column (e.g. "uhc")')
    parser.add_argument(
        "--npi-file",
        default=None,
        metavar="NPI_FILE",
        help="Plain-text file of NPIs to keep (one per line); others are skipped",
    )
    parser.add_argument(
        "--tin-file",
        default=None,
        metavar="TIN_FILE",
        help=(
            "Plain-text file of TINs (EINs) to keep, one per line; lines starting "
            "with '#' are comments.  Non-digit chars are stripped before comparison."
        ),
    )
    parser.add_argument(
        "--tin",
        action="append",
        default=[],
        metavar="TIN",
        help="Individual TIN to keep (repeatable); non-digit chars stripped.",
    )
    parser.add_argument(
        "--resolve-references",
        dest="resolve_references",
        action="store_true",
        default=True,
        help=(
            "Resolve external provider_references[].location URLs (Cigna/Aetna style). "
            "Default: enabled."
        ),
    )
    parser.add_argument(
        "--no-resolve-references",
        dest="resolve_references",
        action="store_false",
        help="Disable external URL resolution (inline-only mode; safe for UHC-style files).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        metavar="N",
        help="Concurrent threads for resolving location URLs (default: 16).",
    )
    args = parser.parse_args(argv)

    npi_filter = None
    if args.npi_file:
        lines = Path(args.npi_file).read_text(encoding="utf-8").splitlines()
        npi_filter = {line.strip() for line in lines if line.strip()}

    tin_filter = None
    raw_tins: list[str] = list(args.tin)
    if args.tin_file:
        lines = Path(args.tin_file).read_text(encoding="utf-8").splitlines()
        raw_tins.extend(
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        )
    if raw_tins:
        tin_filter = {_normalize_tin(t) for t in raw_tins}

    resolver = _default_resolver if args.resolve_references else None

    rows = ingest_tic(
        args.tic_path,
        args.out_csv,
        npi_filter=npi_filter,
        tin_filter=tin_filter,
        payer=args.payer,
        reference_resolver=resolver,
        max_workers=args.max_workers,
    )
    print(f"Wrote {rows} unique NPI→TIN rows to {args.out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run ruff on the updated script**

```bash
python -m ruff check scripts/ingest_tic.py
```

Expected: clean.

- [ ] **Step 3: Run the full test suite to confirm nothing regressed**

```bash
python -m pytest -m "not live and not db" -q
```

Expected: all tests pass (including the 14 tic tests).

- [ ] **Step 4: Commit**

```bash
git add scripts/ingest_tic.py
git commit -m "feat(tic): add --resolve-references / --max-workers CLI flags to ingest_tic script"
```

---

### Task 4: Update docstring and TIC-BATCH-RUNBOOK.md

**Files:**
- Modify: `docs/payer-sources/TIC-BATCH-RUNBOOK.md` — add Cigna section

**Interfaces:**
- Consumes: nothing from code (documentation only)
- Produces: updated runbook with Cigna/external-refs guidance

- [ ] **Step 1: Append Cigna section to the runbook**

Add the following section to the end of `docs/payer-sources/TIC-BATCH-RUNBOOK.md`:

```markdown

## Cigna-style payers (external provider_reference.location files)

Cigna and many modern Aetna plans put their NPI/TIN data in **separate referenced
files** instead of inline `provider_groups`.  The top-level MRF lists only:

```json
{"provider_references": [
    {"provider_group_id": 1, "location": "https://mrf.cigna.com/ref/abc123.json.gz"}
]}
```

The ingester resolves these automatically — **`--resolve-references` is ON by default**.

### Important: geo-restriction

Cigna's provider-reference files are served from a **geo-restricted AWS CloudFront**
distribution.  Requests from outside the US return 403.  Always run the ingester from a
**US IP address** (EC2 / Fargate in `us-east-1` or `us-west-2`) for Cigna and Aetna MRFs.
UHC's Azure CDN is open and works from anywhere.

### Usage

```bash
# Cigna MRF (external refs resolved automatically)
python scripts/ingest_tic.py cigna-plan.json.gz cigna.csv \
    --payer cigna --tin-file practice-tins.txt

# Force inline-only mode (disables resolver — use for UHC or air-gapped environments)
python scripts/ingest_tic.py uhc-plan.json.gz uhc.csv \
    --payer uhc --tin-file practice-tins.txt --no-resolve-references

# Tune concurrency (default 16 threads)
python scripts/ingest_tic.py cigna-plan.json.gz cigna.csv \
    --payer cigna --tin-file practice-tins.txt --max-workers 32
```

The script prints:
```
Wrote 47 unique NPI→TIN rows to cigna.csv
```

Resolver failures per URL are logged at WARNING level; the run continues and
non-failing refs are still written.  Pass `--log-level WARNING` or set
`PYTHONWARNINGS` as needed to surface them.
```

- [ ] **Step 2: Run the full test suite one final time**

```bash
python -m pytest -m "not live and not db" -q
```

Expected: all tests PASS (report exact count).

- [ ] **Step 3: Run ruff on everything**

```bash
python -m ruff check src tests scripts
```

Expected: no output (clean).

- [ ] **Step 4: Final commit**

```bash
git add docs/payer-sources/TIC-BATCH-RUNBOOK.md
git commit -m "docs(tic): note Cigna-style external provider_reference.location + geo-restriction warning"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|-------------|------|
| `reference_resolver=None` param | Task 2 |
| Default resolver: httpx, gzip, plain JSON | Task 2 (`_default_resolver`) |
| Resolver injectable for tests | Tasks 1+2 |
| New pass: collect location URLs from provider_references | Task 2, Pass 1 |
| Concurrent resolution (ThreadPoolExecutor, max_workers=16) | Task 2, Pass 3 |
| Per-URL error → skip, count, don't abort | Task 2 + test in Task 1 |
| `_emit()` same npi_filter/tin_filter/dedup path | Task 2 (`_extract_provider_groups` → `_emit`) |
| Inline behavior unchanged | Task 2 (Pass 1 guards on `pr.get("provider_groups")`) |
| Three JSON shapes: dict-with-key, bare-list, single-group | Task 2 (`_extract_provider_groups`) |
| `scripts/ingest_tic.py` --resolve-references/--no-resolve-references | Task 3 |
| `--max-workers N` flag | Task 3 |
| Print counts (rows + resolved N, M failed) | Task 3 (note: resolved/failed counts go to logger, rows to print) |
| Fixture + 5 tests: location-only, mixed, error-tolerant, guard-no-resolver | Task 1 |
| Existing UHC tests unchanged | Task 1 (confirmed at step 5) |
| Docstring updated | Task 2 |
| TIC-BATCH-RUNBOOK.md updated | Task 4 |
| ruff clean | Each task |
| pytest -m "not live and not db" green | Each task |
| No network in tests | Tasks 1+2 (fake resolver) |

**Placeholder scan:** None — all steps contain real code.

**Type consistency:** `_extract_provider_groups` → `list`; passed to `_emit(groups, ...)` which accepts `list | None` (guarded by `for g in groups or []`). `reference_resolver: Callable[[str], dict | list] | None` matches `_default_resolver` and test fakes. Consistent.
