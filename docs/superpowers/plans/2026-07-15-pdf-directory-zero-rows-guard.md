# PDF-Directory Zero-Rows Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `load_directory()` from silently replacing a payer's entire provider directory with nothing when a PDF parses to zero rows without raising an exception.

**Architecture:** A guard added directly in `load_directory()`'s two code paths (the multi-URL loop, and the single-file `pdf_path`/`pdf_bytes` override branch) — if any source (URL or override file) yields zero rows, raise `ValueError` naming the payer and source, before `_replace_rows()` is ever reached. No new files, no signature changes.

**Tech Stack:** Python 3.12, pytest, monkeypatch (no real network/DB in tests).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-pdf-directory-zero-rows-guard-design.md` — read it first.
- Scope is exactly a zero-rows-per-source abort — **not** row-count-collapse detection against a previous version (explicitly deferred, per user decision — do not add it).
- No change to any parser (`parse_lines`, `parse_lines_aaneel`, `parse_lines_ccp`) — this is a load-orchestration guard only.
- The guard must apply to BOTH code paths in `load_directory()`: the multi-URL loop (`else` branch) and the `pdf_path`/`pdf_bytes` override branch — not just one.
- Error message must name both the payer_key and the specific failing source (URL, or the file path for the override case) — an on-call engineer reading a failed monthly-refresh log must be able to tell which source broke without reproducing the failure.

---

## File Structure

- **Modify** `src/network_probe/domain/directory_load.py` — add the guard to `load_directory()`.
- **Modify** `tests/test_directory.py` — add 2 new tests.

No new files.

---

### Task 1: Add the zero-rows guard

**Files:**
- Modify: `src/network_probe/domain/directory_load.py:156-190` (`load_directory()`)
- Test: `tests/test_directory.py`

**Interfaces:**
- No signature change to `load_directory()`. It now raises `ValueError` (in addition to its existing failure modes) when any single source produces zero rows.

- [ ] **Step 1: Write the failing tests**

In `tests/test_directory.py`, find `test_load_directory_aborts_on_partial_failure` and the two `test_resolve_pdf_urls_*` tests that follow it (currently ending around line 264, right before the `# --- matcher ---` comment). Add these two new tests immediately after `test_resolve_pdf_urls_plural_config_returns_all_items`:

```python
def test_load_directory_aborts_on_empty_url_result(monkeypatch):
    """A PDF that parses successfully but yields zero rows (a structure-drift failure mode, not
    an exception) must abort the whole load, not silently replace the payer's directory with a
    partial set from the other URLs."""
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-multi-empty",
        {"label": "Test", "format": "ccp", "pdf_urls": ["https://x/a.pdf", "https://x/b.pdf", "https://x/c.pdf"]},
    )
    monkeypatch.setattr(directory_load, "download_pdf", lambda url, timeout=180.0: b"fake-pdf-bytes")
    call_rows = iter([[{"a": 1}, {"a": 2}], [], [{"a": 3}]])  # the 2nd URL yields zero rows
    monkeypatch.setattr(
        directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": next(call_rows)
    )
    replace_called = []
    monkeypatch.setattr(
        directory_load, "_replace_rows", lambda payer_key, rows, engine=None: replace_called.append(True)
    )
    with pytest.raises(ValueError, match="zero rows"):
        directory_load.load_directory("test-multi-empty")
    assert replace_called == [], "must not replace rows when a URL yields zero rows"


def test_load_directory_aborts_on_empty_single_url_result(monkeypatch):
    """Single-URL payers (Align, EternalHealth) go through the same loop as multi-URL payers --
    confirm they get the identical protection, not just the multi-URL case."""
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-single-empty",
        {"label": "Test", "format": "allyalign", "pdf_url": "https://x/only.pdf"},
    )
    monkeypatch.setattr(directory_load, "download_pdf", lambda url, timeout=180.0: b"fake-pdf-bytes")
    monkeypatch.setattr(directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": [])
    replace_called = []
    monkeypatch.setattr(
        directory_load, "_replace_rows", lambda payer_key, rows, engine=None: replace_called.append(True)
    )
    with pytest.raises(ValueError, match="zero rows"):
        directory_load.load_directory("test-single-empty")
    assert replace_called == [], "must not replace rows when the only URL yields zero rows"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py::test_load_directory_aborts_on_empty_url_result tests/test_directory.py::test_load_directory_aborts_on_empty_single_url_result -v`
Expected: both FAIL. `test_load_directory_aborts_on_empty_url_result` fails with `Failed: DID NOT RAISE <class 'ValueError'>` (today's code silently returns `3` and calls `_replace_rows` with the partial 3-row set from URLs 1 and 3, dropping URL 2's empty contribution without complaint). `test_load_directory_aborts_on_empty_single_url_result` fails the same way (returns `0` silently, calls `_replace_rows([])`).

- [ ] **Step 3: Add the guard**

In `src/network_probe/domain/directory_load.py`, find `load_directory()` (currently lines 156-190):

```python
def load_directory(
    payer_key: str, *, pdf_path: str | None = None, pdf_bytes: bytes | None = None,
    version: str | None = None, engine=None,
) -> int:
    """Download (or use the given) PDF(s), parse them, and atomically replace this payer's rows.
    Returns the number of rows loaded.

    `pdf_path`/`pdf_bytes` override a single file (used by tests / one-off loads) exactly as
    before. Without an override, every URL `resolve_pdf_urls()` returns for this payer is
    downloaded and parsed in turn and the rows concatenated -- if any one fails, the whole call
    raises before `_replace_rows()` is reached, so a payer's directory is never left partially
    replaced from some counties/files but not others."""
    cfg = PDF_DIRECTORIES.get(payer_key)
    if cfg is None and pdf_path is None and pdf_bytes is None:
        raise ValueError(f"unknown PDF-directory payer {payer_key!r}")
    version = version or _month()
    fmt = (cfg or {}).get("format", "allyalign")
    if pdf_path is not None or pdf_bytes is not None:
        tmp_path = None
        try:
            if pdf_path is None:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                pdf_path = tmp_path
            rows = rows_from_pdf(pdf_path, payer_key, version, fmt=fmt)
        finally:
            if tmp_path:
                os.unlink(tmp_path)
    else:
        rows = []
        for url in resolve_pdf_urls(cfg):
            rows.extend(_rows_from_url(url, payer_key, version, fmt=fmt))
    _replace_rows(payer_key, rows, engine)
    return len(rows)
```

Replace with:

```python
def load_directory(
    payer_key: str, *, pdf_path: str | None = None, pdf_bytes: bytes | None = None,
    version: str | None = None, engine=None,
) -> int:
    """Download (or use the given) PDF(s), parse them, and atomically replace this payer's rows.
    Returns the number of rows loaded.

    `pdf_path`/`pdf_bytes` override a single file (used by tests / one-off loads) exactly as
    before. Without an override, every URL `resolve_pdf_urls()` returns for this payer is
    downloaded and parsed in turn and the rows concatenated -- if any one fails, the whole call
    raises before `_replace_rows()` is reached, so a payer's directory is never left partially
    replaced from some counties/files but not others.

    A source that parses successfully but yields ZERO rows also aborts the load (raises, before
    `_replace_rows()`) -- a real PDF with real providers never legitimately produces zero rows, so
    an empty result means the parser silently stopped recognizing the page structure (a drift, not
    a genuine "this payer has no providers"). Without this guard the atomic replace would still
    proceed, wiping that payer's directory to empty and turning every real provider into a false
    OUT_OF_NETWORK instead of an honest UNKNOWN. See docs/superpowers/specs/
    2026-07-15-pdf-directory-zero-rows-guard-design.md."""
    cfg = PDF_DIRECTORIES.get(payer_key)
    if cfg is None and pdf_path is None and pdf_bytes is None:
        raise ValueError(f"unknown PDF-directory payer {payer_key!r}")
    version = version or _month()
    fmt = (cfg or {}).get("format", "allyalign")
    if pdf_path is not None or pdf_bytes is not None:
        tmp_path = None
        try:
            if pdf_path is None:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                pdf_path = tmp_path
            rows = rows_from_pdf(pdf_path, payer_key, version, fmt=fmt)
            if not rows:
                raise ValueError(
                    f"{pdf_path!r} produced zero rows for payer {payer_key!r} -- "
                    "refusing to replace (parser/structure drift?)"
                )
        finally:
            if tmp_path:
                os.unlink(tmp_path)
    else:
        rows = []
        for url in resolve_pdf_urls(cfg):
            url_rows = _rows_from_url(url, payer_key, version, fmt=fmt)
            if not url_rows:
                raise ValueError(
                    f"{url} produced zero rows for payer {payer_key!r} -- "
                    "refusing to replace (parser/structure drift?)"
                )
            rows.extend(url_rows)
    _replace_rows(payer_key, rows, engine)
    return len(rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py::test_load_directory_aborts_on_empty_url_result tests/test_directory.py::test_load_directory_aborts_on_empty_single_url_result -v`
Expected: both PASS.

- [ ] **Step 5: Run the full test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py -v`
Expected: all pass (19 passed — the pre-existing 17 plus this task's 2 new tests).

- [ ] **Step 6: Run `tests/test_payer_sources.py` as an unrelated-regression check**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -q`
Expected: `25 passed, 2 deselected` — unchanged (this task touches no roster/catalogue code).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/domain/directory_load.py tests/test_directory.py
git commit -m "$(cat <<'EOF'
fix(directory): abort load_directory() when a PDF yields zero rows

A PDF that parses successfully but produces zero rows (a parser/page-
structure drift, not an exception) previously still proceeded to
_replace_rows(), silently wiping that payer's directory and turning
every real provider into a false OUT_OF_NETWORK instead of an honest
UNKNOWN. Now raises ValueError naming the payer and the specific
failing source (URL or override file) before any DB write, in both
the multi-URL loop and the pdf_path/pdf_bytes override branch --
affects all 3 wired PDF-directory payers (Align, EternalHealth,
Community Care Plan) since they share this same code path.

Found during the Community Care Plan sub-project's final review.
EOF
)"
```

---

## After this plan

Row-count-collapse detection against the previously loaded version (a subtler failure mode: a PDF
that still parses but with fewer real entries than before) remains a deferred follow-up, not
pursued here per user decision. Aetna website-scraping was researched separately this session and
found not viable (WAF-protected, backend is CVS Health's authenticated OAuth2 API, `robots.txt`
explicitly disallows the relevant paths) — no further action there; Aetna stays
`needs-authorized-api`, on hold pending real credentials.
