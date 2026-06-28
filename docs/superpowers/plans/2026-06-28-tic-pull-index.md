# TiC Pull-Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/pull_tic_index.py` — a payer-agnostic helper that, given a CMS TiC index (table-of-contents) URL, selects relevant in-network files, downloads them, and runs the existing `ingest_tic` ingester filtered to a practice's TINs/NPIs.

**Architecture:** Three pure functions (`parse_index`, `select_files`, `run`) compose a pipeline: download index JSON → flatten → filter → download each selected file → ingest each → merge and dedup → write final CSV. A `downloader` parameter is injectable so tests never touch the network. The `run` function reuses `network_probe.domain.tic_ingest.ingest_tic` directly, including its default HTTP reference resolver for external `provider_reference.location` files (Pass-3).

**Tech Stack:** Python 3.12, stdlib (`argparse`, `csv`, `pathlib`, `tempfile`, `concurrent.futures`, `re`), `httpx` (already in dependencies) for the default stream-downloader, `pytest` (8.3.4), `ruff`.

## Global Constraints

- Python 3.12; `src/` layout; absolute imports (`from network_probe.domain.tic_ingest import ingest_tic`)
- Branch: `feat/tic-pull-index` off `main`
- `pytest -m "not live and not db" -q` must stay green; no live network in tests (inject fake downloader)
- `ruff check src tests scripts` must be clean (line-length 120, select E F I B UP, ignore B008 B904 E741 UP042)
- `from __future__ import annotations` at top of every new Python file
- Do NOT stage `.env`
- Commit message: `feat(tic): pull_tic_index.py — index-driven multi-file TiC pull (Cigna/any payer)`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/pull_tic_index.py` | **Create** | Pure functions + CLI entry point |
| `tests/test_pull_tic_index.py` | **Create** | Pure offline tests (fake downloader) |
| `docs/payer-sources/TIC-BATCH-RUNBOOK.md` | **Modify** | Add "One-command pull from an index" section |

---

## Task 1: Create branch and write `parse_index` + tests

**Files:**
- Create: `scripts/pull_tic_index.py` (skeleton + `parse_index`)
- Create: `tests/test_pull_tic_index.py` (parse_index tests only)

**Interfaces:**
- Produces: `parse_index(data: dict) -> list[dict]`
  - Each dict has keys: `location` (str), `plans` (list[str]), `market` (list[str]), `description` (str)
  - Flattens `data["reporting_structure"][i]["in_network_files"][j]` carrying the sibling `reporting_plans[k].plan_name` and `reporting_plans[k].plan_market_type` from the same `reporting_structure[i]`

- [ ] **Step 1: Create the branch**

```bash
git checkout main
git checkout -b feat/tic-pull-index
```

Expected: `Switched to a new branch 'feat/tic-pull-index'`

- [ ] **Step 2: Write the failing tests for `parse_index`**

Create `tests/test_pull_tic_index.py`:

```python
"""Tests for scripts/pull_tic_index.py (pure / offline)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

# scripts/ is not on sys.path by default — add it so we can import the module directly.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from pull_tic_index import parse_index, select_files, run  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tic-sample.json"

# ---------------------------------------------------------------------------
# Fixture index data
# ---------------------------------------------------------------------------

_INDEX_DATA = {
    "reporting_structure": [
        {
            "reporting_plans": [
                {"plan_name": "Cigna AZ HMO", "plan_market_type": "group"},
                {"plan_name": "Cigna AZ PPO", "plan_market_type": "group"},
            ],
            "in_network_files": [
                {
                    "description": "Cigna Arizona HMO network",
                    "location": "https://mrf.cigna.com/az_hmo.json.gz",
                },
                {
                    "description": "Cigna Arizona PPO network",
                    "location": "https://mrf.cigna.com/az_ppo.json.gz",
                },
            ],
        },
        {
            "reporting_plans": [
                {"plan_name": "Cigna TX HMO", "plan_market_type": "group"},
            ],
            "in_network_files": [
                {
                    "description": "Cigna Texas HMO network",
                    "location": "https://mrf.cigna.com/tx_hmo.json.gz",
                },
            ],
        },
    ]
}


# ---------------------------------------------------------------------------
# parse_index tests
# ---------------------------------------------------------------------------


def test_parse_index_entry_count():
    entries = parse_index(_INDEX_DATA)
    assert len(entries) == 3


def test_parse_index_carries_plan_names():
    entries = parse_index(_INDEX_DATA)
    az_entry = next(e for e in entries if "az_hmo" in e["location"])
    assert "Cigna AZ HMO" in az_entry["plans"]
    assert "Cigna AZ PPO" in az_entry["plans"]


def test_parse_index_carries_market():
    entries = parse_index(_INDEX_DATA)
    az_entry = next(e for e in entries if "az_hmo" in e["location"])
    assert "group" in az_entry["market"]


def test_parse_index_carries_description():
    entries = parse_index(_INDEX_DATA)
    az_entry = next(e for e in entries if "az_hmo" in e["location"])
    assert az_entry["description"] == "Cigna Arizona HMO network"


def test_parse_index_location_field():
    entries = parse_index(_INDEX_DATA)
    locations = {e["location"] for e in entries}
    assert "https://mrf.cigna.com/az_hmo.json.gz" in locations
    assert "https://mrf.cigna.com/tx_hmo.json.gz" in locations


def test_parse_index_empty_structure():
    assert parse_index({"reporting_structure": []}) == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd C:\Users\user\Desktop\code\dxdgtl\Prior-Authorization
python -m pytest tests/test_pull_tic_index.py -q 2>&1 | head -20
```

Expected: `ImportError` or `ModuleNotFoundError` (file doesn't exist yet)

- [ ] **Step 4: Create `scripts/pull_tic_index.py` skeleton with `parse_index`**

Create `scripts/pull_tic_index.py`:

```python
"""Index-driven CMS TiC in-network MRF downloader and ingester.

Given a CMS TiC index (table-of-contents) URL, this script:
1. Downloads the index JSON.
2. Parses it to a flat list of in-network file entries (each with its plan
   names, market types, description, and download URL).
3. Optionally filters entries by state or plan name substring.
4. Downloads each selected in-network file, runs the existing ingest_tic
   ingester filtered to the practice's TINs/NPIs, and writes a deduplicated
   NPI→TIN crosswalk CSV.

Usage
-----
python scripts/pull_tic_index.py \\
    --index-url '<signed index url>' \\
    --state AZ \\
    --payer cigna \\
    --tin-file practice-tins.txt \\
    --out cigna-az.csv

Use --list to preview selected files without downloading.

Note: Cigna's provider-reference CDN is geo-restricted to US IPs.  Run from
EC2/Fargate in us-east-1 or us-west-2.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_index(data: dict) -> list[dict]:
    """Flatten a CMS TiC index JSON to a list of in-network file entries.

    Each entry dict has:
        location    (str)  — URL to the in-network MRF (.json.gz)
        plans       (list[str]) — plan names from the sibling reporting_plans
        market      (list[str]) — plan_market_type values from sibling plans
        description (str)  — file description from the index

    Parameters
    ----------
    data:
        Parsed JSON dict from a CMS TiC index file.  Expected shape::

            {"reporting_structure": [
                {
                    "reporting_plans": [{"plan_name": ..., "plan_market_type": ...}],
                    "in_network_files": [{"description": ..., "location": ...}]
                }
            ]}
    """
    entries: list[dict] = []
    for rs in data.get("reporting_structure") or []:
        plans = [p.get("plan_name") or "" for p in rs.get("reporting_plans") or []]
        market = [p.get("plan_market_type") or "" for p in rs.get("reporting_plans") or []]
        for f in rs.get("in_network_files") or []:
            entries.append(
                {
                    "location": f.get("location") or "",
                    "plans": plans,
                    "market": market,
                    "description": f.get("description") or "",
                }
            )
    return entries
```

- [ ] **Step 5: Run tests to verify `parse_index` tests pass**

```bash
python -m pytest tests/test_pull_tic_index.py -k "parse_index" -q
```

Expected: 6 tests pass. (`select_files` and `run` tests will fail with `ImportError` since those functions don't exist yet — that's fine; we're only running the parse_index subset.)

- [ ] **Step 6: Commit the skeleton + parse_index**

```bash
git add scripts/pull_tic_index.py tests/test_pull_tic_index.py
git commit -m "feat(tic): parse_index — flatten CMS TiC index to entry list"
```

---

## Task 2: Add `select_files` + tests

**Files:**
- Modify: `scripts/pull_tic_index.py` (add `select_files`)
- Modify: `tests/test_pull_tic_index.py` (add `select_files` tests)

**Interfaces:**
- Consumes: `parse_index` output — `list[dict]` where each dict has `location`, `plans`, `market`, `description`
- Produces: `select_files(entries: list[dict], state: str | None = None, plan_contains: str | None = None) -> list[dict]`
  - Case-insensitive substring search of `state` (e.g. "AZ") against the `location` URL + plan names + market values + description
  - `plan_contains` is matched against plan names + market values
  - If no filter given → return all entries
  - If a filter matches nothing → return `[]`
  - Both filters can be active simultaneously; an entry must match both (intersection)

- [ ] **Step 1: Add `select_files` tests to `tests/test_pull_tic_index.py`**

Append to `tests/test_pull_tic_index.py` (after the existing parse_index tests):

```python
# ---------------------------------------------------------------------------
# select_files tests
# ---------------------------------------------------------------------------


def test_select_files_no_filter_returns_all():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries)
    assert len(selected) == 3


def test_select_files_state_az_returns_2():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, state="AZ")
    assert len(selected) == 2
    for e in selected:
        assert "az" in e["location"].lower() or any("az" in p.lower() for p in e["plans"])


def test_select_files_state_case_insensitive():
    entries = parse_index(_INDEX_DATA)
    lower = select_files(entries, state="az")
    upper = select_files(entries, state="AZ")
    assert len(lower) == len(upper) == 2


def test_select_files_state_no_match_returns_empty():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, state="NY")
    assert selected == []


def test_select_files_plan_contains_filters():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, plan_contains="HMO")
    # "Cigna AZ HMO" and "Cigna TX HMO" match; "Cigna AZ PPO" entry carries both plan names
    # az_hmo has ["Cigna AZ HMO", "Cigna AZ PPO"] — matches because "Cigna AZ HMO" contains "HMO"
    # az_ppo has ["Cigna AZ HMO", "Cigna AZ PPO"] — same plans! matches because "Cigna AZ HMO" is there
    # tx_hmo has ["Cigna TX HMO"] — matches
    assert len(selected) == 3


def test_select_files_plan_contains_no_match():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, plan_contains="NONEXISTENT")
    assert selected == []


def test_select_files_both_filters_intersection():
    entries = parse_index(_INDEX_DATA)
    # state=TX and plan_contains=HMO -> only tx_hmo
    selected = select_files(entries, state="TX", plan_contains="HMO")
    assert len(selected) == 1
    assert "tx_hmo" in selected[0]["location"]
```

- [ ] **Step 2: Run tests to verify `select_files` tests fail**

```bash
python -m pytest tests/test_pull_tic_index.py -k "select_files" -q
```

Expected: `ImportError` for `select_files` (not yet defined)

- [ ] **Step 3: Implement `select_files` in `scripts/pull_tic_index.py`**

Add after `parse_index` in `scripts/pull_tic_index.py`:

```python
def select_files(
    entries: list[dict],
    state: str | None = None,
    plan_contains: str | None = None,
) -> list[dict]:
    """Filter index entries to those matching the given criteria.

    Matching is case-insensitive substring search.

    ``state``         — matched against each entry's location URL, plan names,
                        market types, and description.
    ``plan_contains`` — matched against plan names and market types only.

    If no filter is given, all entries are returned.  If a filter is given but
    matches nothing, ``[]`` is returned (the caller should warn and list what
    is available).

    When both filters are active, an entry must satisfy both (intersection).
    """
    result = entries

    if state is not None:
        needle = state.lower()

        def _state_match(e: dict) -> bool:
            if needle in e["location"].lower():
                return True
            if any(needle in p.lower() for p in e["plans"]):
                return True
            if any(needle in m.lower() for m in e["market"]):
                return True
            if needle in e["description"].lower():
                return True
            return False

        result = [e for e in result if _state_match(e)]

    if plan_contains is not None:
        needle = plan_contains.lower()

        def _plan_match(e: dict) -> bool:
            if any(needle in p.lower() for p in e["plans"]):
                return True
            if any(needle in m.lower() for m in e["market"]):
                return True
            return False

        result = [e for e in result if _plan_match(e)]

    return result
```

- [ ] **Step 4: Run `select_files` tests**

```bash
python -m pytest tests/test_pull_tic_index.py -k "select_files or parse_index" -q
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_tic_index.py tests/test_pull_tic_index.py
git commit -m "feat(tic): select_files — case-insensitive state/plan filter"
```

---

## Task 3: Add the default stream-downloader helper

**Files:**
- Modify: `scripts/pull_tic_index.py` (add `_stream_download`)

**Interfaces:**
- Produces: `_stream_download(url: str, workdir: str | None = None) -> str`
  - Downloads `url` to a temp file under `workdir` (or system temp if None), preserving the filename extension (`.json.gz` etc.), returns the local path as a string.
  - Uses `httpx` with `follow_redirects=True, timeout=120`.
  - No tests needed for this — it touches the network and will be covered by the live marker in integration; pure tests inject a fake.

- [ ] **Step 1: Add `_stream_download` to `scripts/pull_tic_index.py`**

Add after `select_files`:

```python
def _stream_download(url: str, workdir: str | None = None) -> str:
    """Stream-download *url* to a temp file; return the local path.

    Preserves the file extension from the URL path for correct gzip detection
    by :func:`ingest_tic`.  Uses httpx with ``follow_redirects=True`` so
    signed-URL redirects (e.g. Cigna S3 pre-signed URLs) work transparently.

    Parameters
    ----------
    url:
        Full HTTPS URL to download.
    workdir:
        Directory to write the temp file into.  If ``None``, the OS temp
        directory is used.
    """
    import httpx

    # Derive a sensible suffix from the URL path (e.g. ".json.gz").
    url_path = url.split("?")[0]  # strip query string before extracting extension
    suffix = "".join(Path(url_path).suffixes[-2:]) or ".json"
    with tempfile.NamedTemporaryFile(
        dir=workdir, suffix=suffix, delete=False
    ) as tmp:
        local_path = tmp.name

    with httpx.Client(follow_redirects=True, timeout=120) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):  # 1 MB chunks
                    f.write(chunk)

    return local_path
```

- [ ] **Step 2: Run existing tests to ensure nothing broke**

```bash
python -m pytest tests/test_pull_tic_index.py -k "parse_index or select_files" -q
```

Expected: 13 tests pass.

- [ ] **Step 3: Commit**

```bash
git add scripts/pull_tic_index.py
git commit -m "feat(tic): _stream_download — httpx streaming downloader helper"
```

---

## Task 4: Implement `run` function + end-to-end tests

This is the main integration function. It ties together the downloader, `parse_index`, `select_files`, and `ingest_tic`.

**Files:**
- Modify: `scripts/pull_tic_index.py` (add `run`)
- Modify: `tests/test_pull_tic_index.py` (add end-to-end tests)

**Interfaces:**
- Consumes:
  - `parse_index(data: dict) -> list[dict]`
  - `select_files(entries, state, plan_contains) -> list[dict]`
  - `ingest_tic(tic_path, out_csv, npi_filter=None, tin_filter=None, payer=None, reference_resolver=None) -> int`
  - `downloader: Callable[[str], str]` — injectable; default is `_stream_download`
- Produces: `run(...) -> int` — unique row count written to `out_csv`

**Signature:**
```python
def run(
    index_url: str,
    out_csv: str,
    *,
    tin_file: str | None = None,
    npi_file: str | None = None,
    state: str | None = None,
    plan_contains: str | None = None,
    payer: str | None = None,
    workdir: str | None = None,
    downloader: Callable[[str], str] | None = None,
    list_only: bool = False,
    max_workers: int = 16,
    keep: bool = False,
) -> int
```

- [ ] **Step 1: Add end-to-end tests to `tests/test_pull_tic_index.py`**

Append to `tests/test_pull_tic_index.py`:

```python
# ---------------------------------------------------------------------------
# End-to-end tests with fake downloader
# ---------------------------------------------------------------------------


def _make_fake_downloader(url_to_path: dict[str, str]):
    """Return a downloader that maps URLs to pre-existing local paths."""

    def _fake(url: str) -> str:
        return url_to_path[url]

    return _fake


def test_run_end_to_end_tin_filter(tmp_path):
    """run() with a tin_file → out_csv has only the 2 rows for that TIN."""
    # Build a minimal index whose only location points to our fixture
    index_url = "fake://index.json"
    sample_url = "fake://sample.json"

    index_data = {
        "reporting_structure": [
            {
                "reporting_plans": [{"plan_name": "Cigna AZ HMO", "plan_market_type": "group"}],
                "in_network_files": [
                    {"description": "AZ network", "location": sample_url}
                ],
            }
        ]
    }

    import json

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index_data))

    # Fake downloader: index URL -> temp index file; sample URL -> fixture
    url_to_path = {
        index_url: str(index_path),
        sample_url: str(FIXTURE),
    }
    fake_dl = _make_fake_downloader(url_to_path)

    # Write a tin_file containing only TIN 933510922
    tin_file = tmp_path / "tins.txt"
    tin_file.write_text("933510922\n")

    out_csv = str(tmp_path / "out.csv")

    row_count = run(
        index_url=index_url,
        out_csv=out_csv,
        tin_file=str(tin_file),
        downloader=fake_dl,
        payer="cigna",
    )

    assert row_count == 2
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    npis = {r["npi"] for r in rows}
    assert npis == {"1972603934", "1710305735"}
    tins = {r["tin"] for r in rows}
    assert tins == {"933510922"}


def test_run_two_file_index_deduped(tmp_path):
    """2-file index: rows from both files, deduped (same provider in both → 1 row)."""
    import json

    index_url = "fake://index.json"
    sample_url_a = "fake://sample_a.json"
    sample_url_b = "fake://sample_b.json"

    # Both files point to the same fixture → same 3 rows → deduplicated to 3 unique rows
    index_data = {
        "reporting_structure": [
            {
                "reporting_plans": [{"plan_name": "Cigna AZ HMO", "plan_market_type": "group"}],
                "in_network_files": [
                    {"description": "AZ HMO", "location": sample_url_a},
                    {"description": "AZ PPO", "location": sample_url_b},
                ],
            }
        ]
    }

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index_data))

    url_to_path = {
        index_url: str(index_path),
        sample_url_a: str(FIXTURE),
        sample_url_b: str(FIXTURE),
    }
    fake_dl = _make_fake_downloader(url_to_path)
    out_csv = str(tmp_path / "out.csv")

    row_count = run(
        index_url=index_url,
        out_csv=out_csv,
        downloader=fake_dl,
        payer="cigna",
    )

    # tic-sample.json has 3 unique (npi, tin) pairs — dedup means we get 3, not 6
    assert row_count == 3
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3


def test_run_list_only_returns_0_no_csv(tmp_path):
    """--list mode: prints entries, returns 0, does not create out_csv."""
    import json

    index_url = "fake://index.json"
    sample_url = "fake://sample.json"
    index_data = {
        "reporting_structure": [
            {
                "reporting_plans": [{"plan_name": "Cigna AZ HMO", "plan_market_type": "group"}],
                "in_network_files": [{"description": "AZ network", "location": sample_url}],
            }
        ]
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index_data))

    fake_dl = _make_fake_downloader({index_url: str(index_path)})
    out_csv = str(tmp_path / "out.csv")

    result = run(
        index_url=index_url,
        out_csv=out_csv,
        downloader=fake_dl,
        list_only=True,
    )
    assert result == 0
    assert not Path(out_csv).exists()


def test_run_select_no_match_warns_returns_0(tmp_path, capsys):
    """state filter that matches nothing → 0 rows, warning printed."""
    import json

    index_url = "fake://index.json"
    index_data = {
        "reporting_structure": [
            {
                "reporting_plans": [{"plan_name": "Cigna AZ HMO", "plan_market_type": "group"}],
                "in_network_files": [
                    {"description": "AZ network", "location": "fake://sample.json"}
                ],
            }
        ]
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index_data))
    fake_dl = _make_fake_downloader({index_url: str(index_path)})
    out_csv = str(tmp_path / "out.csv")

    result = run(
        index_url=index_url,
        out_csv=out_csv,
        state="NY",
        downloader=fake_dl,
    )
    assert result == 0
    captured = capsys.readouterr()
    assert "no files" in captured.out.lower() or "0 files" in captured.out.lower() or "warning" in captured.out.lower()
```

- [ ] **Step 2: Run to confirm tests fail (function not defined yet)**

```bash
python -m pytest tests/test_pull_tic_index.py -k "run" -q
```

Expected: `ImportError` for `run`

- [ ] **Step 3: Implement `run` in `scripts/pull_tic_index.py`**

Add after `_stream_download`:

```python
def _normalize_tin(tin: str) -> str:
    """Strip all non-digit characters from a TIN string."""
    return re.sub(r"\D", "", tin)


def run(
    index_url: str,
    out_csv: str,
    *,
    tin_file: str | None = None,
    npi_file: str | None = None,
    state: str | None = None,
    plan_contains: str | None = None,
    payer: str | None = None,
    workdir: str | None = None,
    downloader: Callable[[str], str] | None = None,
    list_only: bool = False,
    max_workers: int = 16,
    keep: bool = False,
) -> int:
    """Download a CMS TiC index and ingest selected in-network files.

    Parameters
    ----------
    index_url:
        URL to the TiC index JSON (e.g. Cigna's signed ``_index.json?...`` URL).
    out_csv:
        Destination CSV path (columns: npi, tin, payer).
    tin_file:
        Path to a plain-text file of TINs to keep (one per line; lines starting
        with ``#`` are comments; non-digit chars stripped before comparison).
    npi_file:
        Path to a plain-text file of NPIs to keep (one per line).
    state:
        Case-insensitive substring filter applied to location URL, plan names,
        market types, and description (e.g. ``"AZ"`` or ``"arizona"``).
    plan_contains:
        Case-insensitive substring filter applied to plan names and market types.
    payer:
        Payer label written to the ``payer`` column (e.g. ``"cigna"``).
    workdir:
        Directory for temporary downloads.  Defaults to the OS temp directory.
    downloader:
        ``(url: str) -> local_path`` callable.  Defaults to
        :func:`_stream_download`.  **Inject a fake for tests.**
    list_only:
        When ``True``, print selected files and return 0 without downloading.
    max_workers:
        Concurrent threads passed to ``ingest_tic`` for resolver calls.
    keep:
        When ``True``, downloaded temp files are not deleted after ingestion.

    Returns
    -------
    int
        Number of unique (npi, tin) rows written to *out_csv*.
    """
    import json

    from network_probe.domain.tic_ingest import ingest_tic

    _dl = downloader if downloader is not None else _stream_download

    # --- Step 1: download + parse the index ---
    index_local = _dl(index_url)
    try:
        with open(index_local, encoding="utf-8") as f:
            index_data = json.load(f)
    finally:
        if not keep and downloader is None:
            # only clean up files we actually downloaded (not injected fixtures)
            Path(index_local).unlink(missing_ok=True)

    entries = parse_index(index_data)

    # --- Step 2: filter ---
    selected = select_files(entries, state=state, plan_contains=plan_contains)
    if not selected:
        total_count = len(entries)
        print(
            f"WARNING: no files matched your filter (state={state!r}, "
            f"plan_contains={plan_contains!r}). "
            f"{total_count} file(s) available in index:"
        )
        for e in entries:
            print(f"  {e['location']}  [{', '.join(e['plans'][:3])}]")
        return 0

    print(f"Selected {len(selected)} file(s) from index:")
    for e in selected:
        print(f"  {e['location']}  [{', '.join(e['plans'][:3])}]")

    if list_only:
        return 0

    # --- Step 3: load TIN + NPI filters ---
    tin_filter: set[str] | None = None
    if tin_file:
        lines = Path(tin_file).read_text(encoding="utf-8").splitlines()
        raw_tins = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        tin_filter = {_normalize_tin(t) for t in raw_tins}

    npi_filter: set[str] | None = None
    if npi_file:
        lines = Path(npi_file).read_text(encoding="utf-8").splitlines()
        npi_filter = {ln.strip() for ln in lines if ln.strip()}

    # --- Step 4: download + ingest each selected file, accumulate rows ---
    # We collect all (npi, tin, payer) tuples across files then dedup.
    all_rows: set[tuple[str, str, str]] = set()
    tmp_files: list[str] = []

    def _ingest_one(entry: dict) -> set[tuple[str, str, str]]:
        local = _dl(entry["location"])
        tmp_files.append(local)
        with tempfile.NamedTemporaryFile(
            dir=workdir, suffix=".csv", delete=False, mode="w", encoding="utf-8"
        ) as tmp_csv_f:
            tmp_csv_path = tmp_csv_f.name

        try:
            ingest_tic(
                local,
                tmp_csv_path,
                npi_filter=npi_filter,
                tin_filter=tin_filter,
                payer=payer,
                max_workers=max_workers,
            )
            rows: set[tuple[str, str, str]] = set()
            with open(tmp_csv_path, newline="", encoding="utf-8") as f:
                import csv as _csv

                for row in _csv.DictReader(f):
                    rows.add((row["npi"], row["tin"], row.get("payer", payer or "")))
            return rows
        finally:
            Path(tmp_csv_path).unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(selected)))) as pool:
        futures = {pool.submit(_ingest_one, e): e for e in selected}
        for fut in as_completed(futures):
            try:
                all_rows.update(fut.result())
            except Exception as exc:  # noqa: BLE001
                entry = futures[fut]
                logger.warning("Failed to ingest %s: %s", entry["location"], exc)

    # Clean up downloaded files unless --keep
    if not keep and downloader is None:
        for p in tmp_files:
            Path(p).unlink(missing_ok=True)

    # --- Step 5: write deduplicated out_csv ---
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        import csv as _csv

        writer = _csv.writer(f)
        writer.writerow(["npi", "tin", "payer"])
        for npi, tin, pay in sorted(all_rows):
            writer.writerow([npi, tin, pay])

    unique = len(all_rows)
    print(f"Wrote {unique} unique NPI→TIN rows to {out_csv}")
    return unique
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_pull_tic_index.py -q
```

Expected: all tests pass (parse_index, select_files, run tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_tic_index.py tests/test_pull_tic_index.py
git commit -m "feat(tic): run() + end-to-end tests with fake downloader"
```

---

## Task 5: Add `main(argv)` CLI entry point

**Files:**
- Modify: `scripts/pull_tic_index.py` (add `main` + `if __name__ == "__main__"`)

**Interfaces:**
- Consumes: `run(...)` from this same module
- Produces: `main(argv: list[str] | None = None) -> int`
  - CLI args: `--index-url` (required), `--out` (required), `--tin-file`, `--npi-file`, `--state`, `--plan-contains`, `--payer`, `--workdir`, `--keep`, `--list`, `--max-workers`

No new tests needed — `run` is already tested. `main` is thin argparse glue.

- [ ] **Step 1: Add `main` and `__main__` block to `scripts/pull_tic_index.py`**

Append to `scripts/pull_tic_index.py`:

```python
# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for pull_tic_index."""
    parser = argparse.ArgumentParser(
        prog="pull_tic_index",
        description=(
            "Download a CMS TiC index and ingest selected in-network files "
            "into an NPI→TIN crosswalk CSV."
        ),
    )
    parser.add_argument(
        "--index-url",
        required=True,
        metavar="URL",
        help="URL to the TiC index JSON (e.g. Cigna signed _index.json?... URL).",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="CSV",
        help="Output CSV path (columns: npi, tin, payer).",
    )
    parser.add_argument(
        "--tin-file",
        default=None,
        metavar="FILE",
        help="Plain-text file of TINs to keep (one per line; '#' lines are comments).",
    )
    parser.add_argument(
        "--npi-file",
        default=None,
        metavar="FILE",
        help="Plain-text file of NPIs to keep (one per line).",
    )
    parser.add_argument(
        "--state",
        default=None,
        metavar="STATE",
        help="Case-insensitive substring filter applied to location URL and plan names (e.g. AZ).",
    )
    parser.add_argument(
        "--plan-contains",
        default=None,
        metavar="SUBSTR",
        help="Case-insensitive substring filter applied to plan names and market types.",
    )
    parser.add_argument(
        "--payer",
        default=None,
        metavar="PAYER",
        help='Payer label written to the "payer" column (e.g. cigna).',
    )
    parser.add_argument(
        "--workdir",
        default=None,
        metavar="DIR",
        help="Directory for temporary downloads (defaults to OS temp).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        default=False,
        help="Keep downloaded temp files after ingestion.",
    )
    parser.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        default=False,
        help="Preview selected files without downloading or ingesting.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        metavar="N",
        help="Concurrent threads for resolver + download (default: 16).",
    )

    args = parser.parse_args(argv)

    return run(
        index_url=args.index_url,
        out_csv=args.out,
        tin_file=args.tin_file,
        npi_file=args.npi_file,
        state=args.state,
        plan_contains=args.plan_contains,
        payer=args.payer,
        workdir=args.workdir,
        keep=args.keep,
        list_only=args.list_only,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run all pull_tic_index tests**

```bash
python -m pytest tests/test_pull_tic_index.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add scripts/pull_tic_index.py
git commit -m "feat(tic): add main(argv) CLI entry point"
```

---

## Task 6: Ruff check + fix + full test suite

**Files:**
- Modify: `scripts/pull_tic_index.py` (fix any ruff issues)
- Modify: `tests/test_pull_tic_index.py` (fix any ruff issues)

- [ ] **Step 1: Run ruff over new files**

```bash
python -m ruff check scripts/pull_tic_index.py tests/test_pull_tic_index.py
```

Common issues to expect and fix:
- `E402` for `sys.path.insert` before imports in test file — add `# noqa: E402` to the import line (already done in the test template above)
- `I001` for import ordering — move `import csv as _csv` inside the function or to top
- `F401` for unused imports

Fix any issues ruff reports. The most likely fix in `run()` is moving `import csv as _csv` to the top of the file since it's used in multiple places. If ruff reports it inside the function body (F811/E402-style), move it to the top-level imports.

After fixing, the `run` function's internal `import csv as _csv` should be replaced with a top-level `import csv` and references changed from `_csv.writer`/`_csv.DictReader` to `csv.writer`/`csv.DictReader`.

If ruff reports issues with the `sys.path.insert` + import pattern in the test file, keep the `# noqa: E402` comments.

- [ ] **Step 2: Run ruff over the entire project**

```bash
python -m ruff check src tests scripts
```

Expected: no output (exit 0).

- [ ] **Step 3: Run full offline test suite**

```bash
python -m pytest -m "not live and not db" -q
```

Expected: all tests pass. Note the total count (e.g. "47 passed").

- [ ] **Step 4: Commit fixes**

```bash
git add scripts/pull_tic_index.py tests/test_pull_tic_index.py
git commit -m "fix(tic): ruff clean — move imports to top level"
```

(If there were no fixes needed, skip the commit.)

---

## Task 7: Update the TIC-BATCH-RUNBOOK.md

**Files:**
- Modify: `docs/payer-sources/TIC-BATCH-RUNBOOK.md`

- [ ] **Step 1: Add the "One-command pull from an index" section**

Append to `docs/payer-sources/TIC-BATCH-RUNBOOK.md` (after the existing "Cigna-style payers" section):

```markdown

## One-command pull from an index (Cigna AZ example)

`pull_tic_index.py` automates the entire index → select → download → ingest pipeline. First use case: **Cigna Arizona** (must run from a US IP — Cigna's CDN is geo-restricted).

### Get the signed index URL

Cigna's index page is JavaScript-rendered. Open it in a browser:

```
https://www.cigna.com/legal/compliance/machine-readable-files
```

Click through to the machine-readable files section, find the link that ends in `_index.json?...` (it will be a long signed S3/CloudFront URL), and copy it.

### Preview files before downloading (--list)

```bash
python scripts/pull_tic_index.py \
    --index-url '<paste signed index URL here>' \
    --state AZ \
    --list
```

This prints the matching file URLs and plan names without downloading anything.

### Full pull

```bash
python scripts/pull_tic_index.py \
    --index-url '<signed index url>' \
    --state AZ \
    --payer cigna \
    --tin-file practice-tins.txt \
    --out cigna-az.csv
```

- `practice-tins.txt` — one TIN per line (keep out of git)
- `cigna-az.csv` — output crosswalk with columns `npi,tin,payer`
- The script prints the number of files selected and unique rows written.

### Notes

- **US IP required**: Cigna's provider-reference CDN (CloudFront) returns 403 outside the US. Run from EC2/Fargate in `us-east-1` or `us-west-2`.
- **Signed URL expiry**: Cigna's index URLs are time-limited. Copy a fresh URL from the browser each run.
- **Deduplication**: rows appearing in multiple plan files are deduplicated automatically.
- **--keep**: add `--keep` to retain downloaded `.json.gz` files for debugging.
- **Other payers**: the same command works for any CMS-compliant TiC index. Swap `--index-url` and `--payer`. UHC's CDN is not geo-restricted.
```

- [ ] **Step 2: Verify the file looks correct**

```bash
python -m pytest -m "not live and not db" -q
```

Expected: no new failures (docs change only).

- [ ] **Step 3: Commit docs**

```bash
git add docs/payer-sources/TIC-BATCH-RUNBOOK.md
git commit -m "docs(tic): add 'One-command pull from index' section to runbook"
```

---

## Task 8: Final commit

- [ ] **Step 1: Run the full offline test suite one last time**

```bash
python -m pytest -m "not live and not db" -q
```

Expected: all tests pass. Note the final count.

- [ ] **Step 2: Run ruff over everything**

```bash
python -m ruff check src tests scripts
```

Expected: exit 0 (no output).

- [ ] **Step 3: Create the final feature commit**

```bash
git log --oneline -8
```

Review that all task commits are on `feat/tic-pull-index`.

```bash
git log --oneline main..HEAD
```

All commits should be visible. The overall feature commit message for the PR/report:

```
feat(tic): pull_tic_index.py — index-driven multi-file TiC pull (Cigna/any payer)
```

- [ ] **Step 4: Report**

Record and report:
- Base SHA: `git rev-parse main`
- New HEAD SHA: `git rev-parse HEAD`
- Test count from `pytest -m "not live and not db" -q` final line
- Self-review: pure logic tested with fake downloader; reuses ingester including Pass-3 external ref resolution; geo/size constraints remain (by design — need a US host for Cigna)

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|---|---|
| `parse_index(data) -> list[dict]` with `location, plans, market, description` | Task 1 |
| `select_files` with state/plan_contains, case-insensitive, no-match → [] | Task 2 |
| `run()` with injectable downloader, list_only, dedup, write CSV | Task 4 |
| `main(argv)` with all CLI args | Task 5 |
| Tests: `parse_index` over fixture dict | Task 1 |
| Tests: `select_files` state=AZ filter, no-match → [] | Task 2 |
| Tests: end-to-end fake downloader + tin_file → 2 rows for TIN 933510922 | Task 4 |
| Tests: 2-file index → deduped rows | Task 4 |
| Docs: TIC-BATCH-RUNBOOK.md "One-command pull" section | Task 7 |
| Ruff clean | Task 6 |
| Branch `feat/tic-pull-index` | Task 1 Step 1 |
| `from __future__ import annotations` | All tasks |
| No live network in tests | Tasks 1–4 (fake downloader) |
| Default downloader uses httpx streaming | Task 3 |
| Reuses `ingest_tic` including Pass-3 reference resolver | Task 4 |

All requirements covered.

### Placeholder scan

No TBD/TODO/placeholder steps in this plan — every step shows exact code.

### Type consistency

- `parse_index` produces `list[dict]` with keys `location, plans, market, description`
- `select_files` consumes that same shape and returns `list[dict]`  
- `run` calls both with consistent arg names
- `ingest_tic` called with `tin_filter=tin_filter, npi_filter=npi_filter` (set[str]) — matches `tic_ingest.py` signature
- `downloader: Callable[[str], str]` used consistently

No type mismatches detected.
