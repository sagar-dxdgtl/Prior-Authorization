# Community Care Plan (FL) PDF-Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Community Care Plan (FL Medicaid MCO) to its real, live, monthly-updated provider directory — 3 per-county PDFs (Broward, Miami-Dade, Palm Beach) in a new, third parser format, feeding one roster row via an extended multi-URL load pipeline.

**Architecture:** A new `parse_lines_ccp()` in `directory_pdf.py` (third format alongside `allyalign`/`aaneel`). `directory_load.py` gains `resolve_pdf_urls()` (always returns a list — 1 URL for existing payers, 3 for CCP) and `load_directory()` downloads+parses each URL, concatenates rows, and does one atomic DB replace — aborting with no partial replace if any URL fails. `roster_seed.py` flips `directory_access` from `"none"` to `"pdf-directory"`. The read side (`DbDirectoryAdapter`) is untouched — confirmed payer-agnostic in the design spec's research.

**Tech Stack:** Python 3.12, pytest, PyMuPDF (`fitz`), httpx, SQLAlchemy (only for the pre-existing `_replace_rows`, unchanged).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-community-care-plan-pdf-design.md` — read it first; this plan implements it exactly.
- **CCP names are surname-first** (`"CLARKE DESIREE"`, not `"DESIREE CLARKE"`) — confirmed empirically against this client's own physician Desiree Clarke, found by name in the live Palm Beach PDF. This is the *opposite* of `allyalign`'s `_split_name()` (which assumes first-name-first) — do **not** reuse `_split_name()` for CCP; write a dedicated CCP name-split function.
- CCP's per-record last field is always `"Performance Indicator:"` — this is the anchor for record boundaries, not `allyalign`'s `"Available As Of:"` or `aaneel`'s provider-id pattern.
- CCP's running 3-line page header (`"<section title>" / "<COUNTY>" / "N of M"`) repeats on **every page** of the PDF, not once per specialty section — must be stripped before per-record parsing, wherever it occurs in the line stream.
- CCP's city/state/zip format is `"CITY, ST ZIP"` (space before zip) — the existing `_CSZ` regex requires `"CITY, ST, ZIP"` (comma before state) and will not match; needs its own regex.
- The PDF also contains non-physician facility/hospital records (e.g. `"Broward General Medical Center"` / `"Hospital"`, with a `Language:` field and no `Age Limitations:` field) mixed into the same specialty sections as individual physicians. Do not special-case or filter these out — they parse into harmless `DirectoryEntry` rows that will never match a real physician's name during lookup (matching this codebase's precedent of not over-engineering for input variety it doesn't need to reject).
- No change to `DbDirectoryAdapter`, `directory_match.py`, or the `PayerDirectoryEntry` schema.
- `load_directory()`'s existing `pdf_path`/`pdf_bytes` single-file override parameters must keep their exact current behavior for existing callers/tests — the new multi-URL path only activates when neither override is given and the resolved config has more than one URL.
- If any one of several URLs fails to download or parse, the whole `load_directory()` call must raise before `_replace_rows()` is ever called — no partial replace.

---

## File Structure

- **Modify** `src/network_probe/domain/directory_pdf.py` — add CCP-specific regexes, `_strip_ccp_page_headers()`, `_split_name_ccp()`, `parse_lines_ccp()`; wire into `parse_directory_pdf()`'s `fmt` dispatch.
- **Modify** `src/network_probe/domain/directory_load.py` — add `resolve_pdf_urls()`, add `PDF_DIRECTORIES["community-care-plan-fl-south-florida"]`, refactor `load_directory()` to route through the multi-URL path.
- **Modify** `src/network_probe/payers/roster_seed.py` — flip `SOURCES["Community Care Plan"]`'s `directory_access`.
- **Modify** `docs/payer-sources/MATRIX.md` — update the existing Community Care Plan row + narrative note.
- **Modify** `tests/test_directory.py` — add CCP parser tests and `load_directory()` multi-URL tests (this file currently has zero coverage of `directory_load.py` at all — these are the first).

No new files.

---

### Task 1: Add the `"ccp"` PDF format

**Files:**
- Modify: `src/network_probe/domain/directory_pdf.py`
- Test: `tests/test_directory.py`

**Interfaces:**
- Produces: `parse_lines_ccp(lines: list[str]) -> list[DirectoryEntry]` — same `DirectoryEntry` return shape as `parse_lines`/`parse_lines_aaneel` (already defined, unchanged). `parse_directory_pdf(path, fmt="ccp", ...)` becomes a valid call for later tasks and for `directory_load.py`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_directory.py`, add near the top, after the existing `AANEEL_LINES` block and `test_parse_lines_aaneel` (currently ending around line 98), a new import and two real-data-derived test blocks:

Update the import line (currently line 11):
```python
from network_probe.domain.directory_pdf import parse_lines, parse_lines_aaneel
```
to:
```python
from network_probe.domain.directory_pdf import parse_lines, parse_lines_aaneel, parse_lines_ccp
```

Then add, after `test_parse_lines_aaneel` (before the `# --- matcher ---` comment):

```python
# Community Care Plan (FL Medicaid) layout: each record is fully self-contained (no shared
# multi-location name header like allyalign), surname-first names, and a running 3-line page
# header ("<section>" / "<COUNTY>" / "N of M") that PyMuPDF re-extracts on every page. Transcribed
# verbatim from the live Broward PDF (docs/superpowers/specs/2026-07-15-community-care-plan-pdf-design.md).
CCP_LINES = [
    "PCP - ADOLESCENT MEDICINE",
    "BROWARD",
    "4 of 1933",
    "FLORENT-CARRE MARIE",
    "ADOLESCENT MEDICINE",
    "9241 W BROWARD BLVD",
    "PLANTATION, FL 33324",
    "Phone: 9542624100",
    "Office Hours: M-F 8:00-5:00p",
    "Gender Accepted: All",
    "Cultural Competence: Yes",
    "WheelChair Accessible: Yes",
    "Board Certification: No",
    "Accepting New Patients: Yes",
    "Age Limitations: 18Y-99Y",
    "Website:",
    "Performance Indicator: Not yet rated",
    "IGLESIAS ELBA AMALIA",
    "ADOLESCENT MEDICINE",
    "1150 N 35TH AVE 560",
    "HOLLYWOOD, FL 33021",
    "Phone: 9542651460",
    "Office Hours: M-F 8:00-5:00p",
    "Gender Accepted: All",
    "Cultural Competence: Yes",
    "WheelChair Accessible: Yes",
    "Board Certification: No",
    "Accepting New Patients: Yes",
    "Age Limitations: 00Y-21Y",
    "Website:",
    "Performance Indicator: Not yet rated",
    "PCP - ADOLESCENT MEDICINE",
    "BROWARD",
    "5 of 1933",
    "FLORENT-CARRE MARIE",
    "ADOLESCENT MEDICINE",
    "3200 S UNIVERSITY DR",
    "DAVIE, FL 33328",
    "Phone: 9542624100",
    "Office Hours: M-F 8:00-5:00p ; Sa 9:00-2:00",
    "Gender Accepted: All",
    "Cultural Competence: Yes",
    "WheelChair Accessible: Yes",
    "Board Certification: No",
    "Accepting New Patients: Yes",
    "Age Limitations: 18Y-99Y",
    "Website:",
    "Performance Indicator: Not yet rated",
]


def test_parse_lines_ccp_extracts_records_surname_first():
    es = parse_lines_ccp(CCP_LINES)
    assert len(es) == 3
    e = es[0]
    assert e.name == "FLORENT-CARRE MARIE"
    # CCP is surname-first: "FLORENT-CARRE MARIE" -> last="FLORENT-CARRE", first="MARIE" --
    # the OPPOSITE of allyalign's _split_name(), confirmed against a real match (this client's
    # own physician Desiree Clarke appears in the live Palm Beach PDF as "CLARKE DESIREE").
    assert e.last_name == "FLORENT-CARRE"
    assert e.first_name == "MARIE"
    assert e.specialty == "ADOLESCENT MEDICINE"
    assert e.accepting_new is True
    assert e.locations[0] == {
        "address": "9241 W BROWARD BLVD",
        "city": "PLANTATION",
        "state": "FL",
        "zip": "33324",
    }
    iglesias = es[1]
    assert iglesias.last_name == "IGLESIAS"
    assert iglesias.first_name == "ELBA AMALIA"


def test_parse_lines_ccp_two_locations_are_two_entries():
    """A provider at 2 addresses is 2 full separate records in CCP's PDF (unlike allyalign, which
    shares one name header with a multi-location list) -- confirmed live: FLORENT-CARRE MARIE
    appears as two complete, separate blocks in the real Broward PDF."""
    es = parse_lines_ccp(CCP_LINES)
    florent = [e for e in es if e.name == "FLORENT-CARRE MARIE"]
    assert len(florent) == 2
    assert len(florent[0].locations) == 1
    assert len(florent[1].locations) == 1
    assert {florent[0].locations[0]["zip"], florent[1].locations[0]["zip"]} == {"33324", "33328"}


def test_parse_lines_ccp_strips_page_header_mid_stream():
    """The 3-line running header ("PCP - ADOLESCENT MEDICINE" / "BROWARD" / "5 of 1933") appears
    a second time in CCP_LINES, between the 2nd and 3rd records -- must not be misread as part of
    a record or leak into any field."""
    es = parse_lines_ccp(CCP_LINES)
    for e in es:
        assert "BROWARD" not in e.name
        assert "of 1933" not in e.name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py::test_parse_lines_ccp_extracts_records_surname_first tests/test_directory.py::test_parse_lines_ccp_two_locations_are_two_entries tests/test_directory.py::test_parse_lines_ccp_strips_page_header_mid_stream -v`
Expected: FAIL with `ImportError: cannot import name 'parse_lines_ccp'` (the function doesn't exist yet).

- [ ] **Step 3: Add the CCP regexes, header-stripper, name-splitter, and parser**

In `src/network_probe/domain/directory_pdf.py`, find this block (currently lines 27-36, the module-level regex constants):

```python
_ANCHOR = "Available As Of:"
_CSZ = re.compile(r"^(.+?),\s*([A-Z]{2}),\s*(\d{5})(?:-\d{4})?$")  # CAPE CORAL, FL, 33914
_FOOTER = re.compile(r"^H\d{4}_.*ProviderDirectory.*\s*\d*$")  # AllyAlign page footer
_PAGE_FOOTER = re.compile(r"^\d+\s*\|\s*P\s*a\s*g\s*e\s*$", re.I)  # AaNeel "41 | P a g e" footer
_ACCEPTING = re.compile(r"Accepting New Patients:\s*(\w+)", re.I)
_AVAIL = re.compile(r"Available As Of:\s*([\d/]+)", re.I)
_PHONE = re.compile(r"Phone:")
# AaNeel/eternalHealth: internal provider id like "P0191519-258948" anchors each record
_PROVIDER_ID = re.compile(r"^[A-Z]\d{4,}-\d{4,}$")
_GENDER_SUFFIX = re.compile(r"\((?:M|F)\)\s*$")
```

Replace with (adding the 3 new CCP-specific patterns at the end):

```python
_ANCHOR = "Available As Of:"
_CSZ = re.compile(r"^(.+?),\s*([A-Z]{2}),\s*(\d{5})(?:-\d{4})?$")  # CAPE CORAL, FL, 33914
_FOOTER = re.compile(r"^H\d{4}_.*ProviderDirectory.*\s*\d*$")  # AllyAlign page footer
_PAGE_FOOTER = re.compile(r"^\d+\s*\|\s*P\s*a\s*g\s*e\s*$", re.I)  # AaNeel "41 | P a g e" footer
_ACCEPTING = re.compile(r"Accepting New Patients:\s*(\w+)", re.I)
_AVAIL = re.compile(r"Available As Of:\s*([\d/]+)", re.I)
_PHONE = re.compile(r"Phone:")
# AaNeel/eternalHealth: internal provider id like "P0191519-258948" anchors each record
_PROVIDER_ID = re.compile(r"^[A-Z]\d{4,}-\d{4,}$")
_GENDER_SUFFIX = re.compile(r"\((?:M|F)\)\s*$")
# Community Care Plan (CCP): "CITY, ST ZIP" -- space before zip, not allyalign's comma-separated
# "CITY, ST, ZIP". Confirmed live: "PLANTATION, FL 33324".
_CCP_CSZ = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$")
# CCP's running page header ends with a literal "N of M" page-count line, repeated on every page.
_CCP_PAGE_MARKER = re.compile(r"^\d+\s+of\s+\d+$")
# CCP's last field in every record (physician or facility) is always Performance Indicator --
# the reliable anchor for "this record just ended".
_CCP_PERF_INDICATOR = re.compile(r"^Performance Indicator:", re.I)
```

- [ ] **Step 4: Add `_split_name_ccp`, `_strip_ccp_page_headers`, and `parse_lines_ccp`**

In the same file, find `parse_directory_pdf` (currently lines 78-96):

```python
def parse_directory_pdf(
    path: str, fmt: str = "allyalign", specialties: set[str] | None = None
) -> list[DirectoryEntry]:
    """Parse the directory PDF at `path` into DirectoryEntry rows.

    `fmt`: "allyalign" (Align Senior Care — anchored on 'Available As Of:') or "aaneel"
    (eternalHealth — anchored on the internal provider id). `specialties`: optional TOC
    specialty headers (allyalign only); matching never needs specialty.
    """
    import fitz  # PyMuPDF

    lines: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            for raw in page.get_text().splitlines():
                s = raw.strip()
                if s and not _FOOTER.match(s) and not _PAGE_FOOTER.match(s):
                    lines.append(s)
    return parse_lines_aaneel(lines) if fmt == "aaneel" else parse_lines(lines, specialties)
```

Replace with:

```python
def parse_directory_pdf(
    path: str, fmt: str = "allyalign", specialties: set[str] | None = None
) -> list[DirectoryEntry]:
    """Parse the directory PDF at `path` into DirectoryEntry rows.

    `fmt`: "allyalign" (Align Senior Care — anchored on 'Available As Of:'), "aaneel"
    (eternalHealth — anchored on the internal provider id), or "ccp" (Community Care Plan —
    anchored on 'Performance Indicator:'). `specialties`: optional TOC specialty headers
    (allyalign only); matching never needs specialty.
    """
    import fitz  # PyMuPDF

    lines: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            for raw in page.get_text().splitlines():
                s = raw.strip()
                if s and not _FOOTER.match(s) and not _PAGE_FOOTER.match(s):
                    lines.append(s)
    if fmt == "aaneel":
        return parse_lines_aaneel(lines)
    if fmt == "ccp":
        return parse_lines_ccp(lines)
    return parse_lines(lines, specialties)
```

Then, at the end of the file, after `toc_specialties` (currently ending at line 202), add:

```python


def _split_name_ccp(full: str) -> tuple[str, str]:
    """CCP prints 'LASTNAME FIRSTNAME [MIDDLENAME]' -- surname first, the reverse of allyalign's
    _split_name(). Confirmed against a real match: this client's own physician Desiree Clarke
    appears in the live Palm Beach PDF as "CLARKE DESIREE"."""
    parts = full.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _strip_ccp_page_headers(lines: list[str]) -> list[str]:
    """Drop the 3-line running page header ('<section title>' / '<COUNTY>' / 'N of M') that
    PyMuPDF re-extracts on every single page of a CCP directory PDF -- confirmed live across
    multiple consecutive pages, not just once per specialty section."""
    out: list[str] = []
    for line in lines:
        if _CCP_PAGE_MARKER.match(line) and len(out) >= 2:
            out.pop()  # county
            out.pop()  # section title
            continue
        out.append(line)
    return out


def parse_lines_ccp(lines: list[str]) -> list[DirectoryEntry]:
    """Community Care Plan (FL Medicaid) layout: each record is fully self-contained --

        NAME
        SPECIALTY (or a facility type like "Hospital")
        STREET ADDRESS
        CITY, ST ZIP                 <- space before zip, unlike allyalign's "CITY, ST, ZIP"
        Phone: ... / Office Hours: ... / other labeled fields we don't store ...
        Accepting New Patients: Yes|No
        ... zero or more further labeled fields ...
        Performance Indicator: ...   <- always the last field; anchors the record boundary

    A provider at two locations appears as two complete, separate records (not one record with
    two `locations` entries like allyalign) -- this parser reflects that directly, no
    multi-location walk-loop needed. Facility/hospital records (no "Age Limitations:" field,
    different name shape) parse the same way and simply never match a real physician's name
    during lookup -- not filtered out, matching this codebase's precedent of not rejecting input
    shapes it doesn't need to reject."""
    clean = _strip_ccp_page_headers(lines)
    entries: list[DirectoryEntry] = []
    i, n = 0, len(clean)
    while i + 3 < n:
        name, specialty, addr, csz = clean[i], clean[i + 1], clean[i + 2], clean[i + 3]
        m = _CCP_CSZ.match(csz)
        if not m:
            i += 1
            continue
        accepting: bool | None = None
        j = i + 4
        while j < n and not _CCP_PERF_INDICATOR.match(clean[j]):
            am = _ACCEPTING.search(clean[j])
            if am:
                accepting = am.group(1).lower().startswith("y")
            j += 1
        last, first = _split_name_ccp(name)
        e = DirectoryEntry(
            name=name, last_name=last, first_name=first, specialty=specialty, accepting_new=accepting
        )
        e.locations.append({"address": addr, "city": m.group(1).strip(), "state": m.group(2), "zip": m.group(3)})
        entries.append(e)
        i = j + 1  # past the "Performance Indicator:" line
    return entries
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py::test_parse_lines_ccp_extracts_records_surname_first tests/test_directory.py::test_parse_lines_ccp_two_locations_are_two_entries tests/test_directory.py::test_parse_lines_ccp_strips_page_header_mid_stream -v`
Expected: all 3 PASS.

- [ ] **Step 6: Run the full test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py -v`
Expected: all pass (13 passed — the pre-existing 10 plus this task's 3 new tests).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/domain/directory_pdf.py tests/test_directory.py
git commit -m "$(cat <<'EOF'
feat(directory): add the "ccp" PDF format (Community Care Plan)

Third format alongside allyalign/aaneel. CCP's records are fully
self-contained (no multi-location walk-loop needed), anchored on
"Performance Indicator:" as the last field per record, with a running
3-line page header repeated on every page (not just once per
specialty). Critically, CCP prints names surname-first ("CLARKE
DESIREE") -- the reverse of allyalign's _split_name() -- confirmed
against a real match: this client's own physician Desiree Clarke
appears by name in the live Palm Beach PDF. A dedicated
_split_name_ccp() avoids silently swapping every last/first name.
EOF
)"
```

---

### Task 2: Multi-URL support in the load pipeline

**Files:**
- Modify: `src/network_probe/domain/directory_load.py`
- Test: `tests/test_directory.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (this task's tests use monkeypatched fakes, not the real CCP parser) — but conceptually enables `parse_directory_pdf(fmt="ccp")` from Task 1 to be reached via the real config path added in Task 3.
- Produces: `resolve_pdf_urls(cfg: dict) -> list[str]`. `load_directory()` keeps its exact existing signature and its `pdf_path`/`pdf_bytes` override behavior; internally it now supports configs with multiple URLs.

- [ ] **Step 1: Write the failing tests**

In `tests/test_directory.py`, add these imports at the top of the file (after the existing imports, currently ending at line 13):

```python
import httpx
import pytest

from network_probe.domain import directory_load
```

Then add, after the CCP tests added in Task 1 (before the `# --- matcher ---` comment):

```python
# --- directory_load.py: multi-URL support (no existing coverage before this task) -------------


def test_load_directory_concatenates_multiple_urls(monkeypatch):
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-multi",
        {"label": "Test", "format": "ccp", "pdf_urls": ["https://x/a.pdf", "https://x/b.pdf", "https://x/c.pdf"]},
    )
    monkeypatch.setattr(directory_load, "download_pdf", lambda url, timeout=180.0: b"fake-pdf-bytes")
    call_rows = iter([[{"a": 1}, {"a": 2}], [{"a": 3}], [{"a": 4}, {"a": 5}, {"a": 6}]])
    monkeypatch.setattr(
        directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": next(call_rows)
    )
    replaced = {}
    monkeypatch.setattr(
        directory_load,
        "_replace_rows",
        lambda payer_key, rows, engine=None: replaced.update(payer_key=payer_key, rows=rows),
    )
    n = directory_load.load_directory("test-multi")
    assert n == 6
    assert replaced["payer_key"] == "test-multi"
    assert replaced["rows"] == [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}, {"a": 5}, {"a": 6}]


def test_load_directory_aborts_on_partial_failure(monkeypatch):
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-multi-fail",
        {"label": "Test", "format": "ccp", "pdf_urls": ["https://x/a.pdf", "https://x/BAD.pdf", "https://x/c.pdf"]},
    )

    def fake_download(url, timeout=180.0):
        if "BAD" in url:
            raise httpx.HTTPError("boom")
        return b"fake-pdf-bytes"

    monkeypatch.setattr(directory_load, "download_pdf", fake_download)
    monkeypatch.setattr(
        directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": [{"a": 1}]
    )
    replace_called = []
    monkeypatch.setattr(
        directory_load, "_replace_rows", lambda payer_key, rows, engine=None: replace_called.append(True)
    )
    with pytest.raises(httpx.HTTPError):
        directory_load.load_directory("test-multi-fail")
    assert replace_called == [], "must not replace rows on partial failure"


def test_resolve_pdf_urls_singular_config_returns_one_item_list():
    cfg = {"pdf_url": "https://example.org/one.pdf"}
    assert directory_load.resolve_pdf_urls(cfg) == ["https://example.org/one.pdf"]


def test_resolve_pdf_urls_plural_config_returns_all_items():
    cfg = {"pdf_urls": ["https://example.org/a.pdf", "https://example.org/b.pdf"]}
    assert directory_load.resolve_pdf_urls(cfg) == ["https://example.org/a.pdf", "https://example.org/b.pdf"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py::test_load_directory_concatenates_multiple_urls tests/test_directory.py::test_load_directory_aborts_on_partial_failure tests/test_directory.py::test_resolve_pdf_urls_singular_config_returns_one_item_list tests/test_directory.py::test_resolve_pdf_urls_plural_config_returns_all_items -v`
Expected: FAIL with `AttributeError: module 'network_probe.domain.directory_load' has no attribute 'resolve_pdf_urls'` (the first two tests error at the `monkeypatch.setattr`-adjacent call inside `load_directory` once it's reached, but practically all 4 fail immediately since `resolve_pdf_urls` doesn't exist yet and `load_directory` doesn't call it).

- [ ] **Step 3: Add `resolve_pdf_urls()` and refactor `load_directory()`**

In `src/network_probe/domain/directory_load.py`, find this block (currently lines 49-61):

```python
def resolve_pdf_url(cfg: dict) -> str:
    """Return the PDF URL — static `pdf_url`, or discovered from `page_url` via `link_pattern`."""
    if cfg.get("pdf_url"):
        return cfg["pdf_url"]
    page, pat = cfg.get("page_url"), cfg.get("link_pattern")
    if not (page and pat):
        raise ValueError("PDF-directory config needs either pdf_url or page_url+link_pattern")
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"user-agent": DEFAULT_UA}) as c:
        html = c.get(page).text
    m = _re.search(pat, html)
    if not m:
        raise ValueError(f"no PDF link matching {pat!r} on {page}")
    return m.group(0)
```

Replace with (the existing function is untouched; a new wrapper is added after it):

```python
def resolve_pdf_url(cfg: dict) -> str:
    """Return the PDF URL — static `pdf_url`, or discovered from `page_url` via `link_pattern`."""
    if cfg.get("pdf_url"):
        return cfg["pdf_url"]
    page, pat = cfg.get("page_url"), cfg.get("link_pattern")
    if not (page and pat):
        raise ValueError("PDF-directory config needs either pdf_url or page_url+link_pattern")
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"user-agent": DEFAULT_UA}) as c:
        html = c.get(page).text
    m = _re.search(pat, html)
    if not m:
        raise ValueError(f"no PDF link matching {pat!r} on {page}")
    return m.group(0)


def resolve_pdf_urls(cfg: dict) -> list[str]:
    """Return every PDF URL this payer's directory is split across. Most payers publish one file
    (`pdf_url` static, or discovered via `page_url`+`link_pattern`) -- `resolve_pdf_url` already
    handles both, so this just wraps its result in a single-item list. Payers whose directory is
    split into several files (e.g. Community Care Plan's per-county PDFs) set `pdf_urls` (plural)
    directly instead."""
    if cfg.get("pdf_urls"):
        return list(cfg["pdf_urls"])
    return [resolve_pdf_url(cfg)]
```

Then find `load_directory()` (currently lines 119-143):

```python
def load_directory(
    payer_key: str, *, pdf_path: str | None = None, pdf_bytes: bytes | None = None,
    version: str | None = None, engine=None,
) -> int:
    """Download (or use the given) PDF, parse it, and atomically replace this payer's rows.
    Returns the number of rows loaded."""
    cfg = PDF_DIRECTORIES.get(payer_key)
    if cfg is None and pdf_path is None and pdf_bytes is None:
        raise ValueError(f"unknown PDF-directory payer {payer_key!r}")
    version = version or _month()
    fmt = (cfg or {}).get("format", "allyalign")
    tmp_path = None
    try:
        if pdf_path is None:
            data = pdf_bytes if pdf_bytes is not None else download_pdf(resolve_pdf_url(cfg))
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            pdf_path = tmp_path
        rows = rows_from_pdf(pdf_path, payer_key, version, fmt=fmt)
        _replace_rows(payer_key, rows, engine)
        return len(rows)
    finally:
        if tmp_path:
            os.unlink(tmp_path)
```

Replace with:

```python
def _rows_from_url(url: str, payer_key: str, version: str, fmt: str) -> list[dict]:
    """Download one PDF and parse it into rows, cleaning up its temp file afterward."""
    data = download_pdf(url)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        return rows_from_pdf(tmp_path, payer_key, version, fmt=fmt)
    finally:
        if tmp_path:
            os.unlink(tmp_path)


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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py::test_load_directory_concatenates_multiple_urls tests/test_directory.py::test_load_directory_aborts_on_partial_failure tests/test_directory.py::test_resolve_pdf_urls_singular_config_returns_one_item_list tests/test_directory.py::test_resolve_pdf_urls_plural_config_returns_all_items -v`
Expected: all 4 PASS.

- [ ] **Step 5: Run the full test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py -v`
Expected: all pass (17 passed — Task 1's 13 plus this task's 4 new tests).

- [ ] **Step 6: Commit**

```bash
git add src/network_probe/domain/directory_load.py tests/test_directory.py
git commit -m "$(cat <<'EOF'
feat(directory): support multiple PDF URLs feeding one payer's rows

resolve_pdf_urls() is the one URL-resolution entry point load_directory()
now calls -- a single-item list for every existing payer (Align,
EternalHealth, unchanged behavior via the existing resolve_pdf_url()),
or the full pdf_urls list for a payer whose directory is split across
several files (Community Care Plan's per-county PDFs, wired next).
If any URL fails to download or parse, the whole load raises before
_replace_rows() is reached -- no partial replace, previous data stays.
The existing pdf_path/pdf_bytes single-file test overrides keep their
exact prior behavior.
EOF
)"
```

---

### Task 3: Wire Community Care Plan into the config and roster

**Files:**
- Modify: `src/network_probe/domain/directory_load.py` (`PDF_DIRECTORIES`)
- Modify: `src/network_probe/payers/roster_seed.py` (`SOURCES["Community Care Plan"]`)
- Test: `tests/test_payer_sources.py`

**Interfaces:**
- Consumes: `"ccp"` format (Task 1), `pdf_urls` config support (Task 2).
- Produces: nothing new consumed by later tasks (Task 4 is documentation-only, independent).

- [ ] **Step 1: Write the failing test**

In `tests/test_payer_sources.py`, find the `_FHIR_PAYERS`-driven test area is for FHIR payers only — Community Care Plan is a `pdf-directory` payer, so instead extend the existing PDF-directory-focused test. Find `test_align_seeded_as_pdf_directory` (currently lines 161-164):

```python
def test_align_seeded_as_pdf_directory():
    row = {r["label"]: r for r in payer_rows()}["Align Senior Health Plan"]
    assert row["directory_access"] == "pdf-directory"
    assert row["fhir_base_url"] is None
```

Add immediately after it:

```python
def test_community_care_plan_seeded_as_pdf_directory():
    row = {r["label"]: r for r in payer_rows()}["Community Care Plan"]
    assert row["directory_access"] == "pdf-directory"
    assert row["fhir_base_url"] is None
    assert row["tic_url"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_community_care_plan_seeded_as_pdf_directory -v`
Expected: FAIL — `AssertionError: assert 'none' == 'pdf-directory'`.

- [ ] **Step 3: Flip `SOURCES["Community Care Plan"]`**

In `src/network_probe/payers/roster_seed.py`, find:

```python
    "Community Care Plan": (
        None,
        None,
        "https://providerdirectory.ccpcares.org/mma",
        "none",
    ),
```

Replace with:

```python
    "Community Care Plan": (
        # FL Medicaid MCO -- no FHIR API, network published only as 3 per-county monthly PDFs
        # (Broward/Miami-Dade/Palm Beach, the client's FL-South Florida market). Verified live
        # 2026-07-15: all 3 static, ~18MB/~6,100 pages total, dated "As of 07/13/2026". Wired via
        # the "ccp" PDF format (parse_lines_ccp in directory_pdf.py) and PDF_DIRECTORIES'
        # multi-URL support (directory_load.py) -- see docs/superpowers/specs/
        # 2026-07-15-community-care-plan-pdf-design.md.
        None,
        None,
        "https://providerdirectory.ccpcares.org/mma",
        "pdf-directory",
    ),
```

- [ ] **Step 4: Add the `PDF_DIRECTORIES` entry**

In `src/network_probe/domain/directory_load.py`, find:

```python
    "eternalhealth-az": {
        "label": "eternalHealth",
        "format": "aaneel",
        # the wp-content URL is date-stamped (…ProviderDirectory-AZ-11212025.pdf) and changes each
        # update — discover the current AZ PDF link from the find-a-provider page.
        "page_url": "https://www.eternalhealth.com/for-members/find-a-provider-or-pharmacy/",
        "link_pattern": r"https://www\.eternalhealth\.com/wp-content/uploads/[^\"'<>]*ProviderDirectory-AZ-[^\"'<>]*\.pdf",
    },
}
```

Replace with:

```python
    "eternalhealth-az": {
        "label": "eternalHealth",
        "format": "aaneel",
        # the wp-content URL is date-stamped (…ProviderDirectory-AZ-11212025.pdf) and changes each
        # update — discover the current AZ PDF link from the find-a-provider page.
        "page_url": "https://www.eternalhealth.com/for-members/find-a-provider-or-pharmacy/",
        "link_pattern": r"https://www\.eternalhealth\.com/wp-content/uploads/[^\"'<>]*ProviderDirectory-AZ-[^\"'<>]*\.pdf",
    },
    "community-care-plan-fl-south-florida": {
        "label": "Community Care Plan",
        "format": "ccp",
        # 3 static per-county PDFs (Broward/Miami-Dade/Palm Beach — the client's FL-South Florida
        # market), confirmed live 2026-07-15. No date-stamp discovery needed (unlike eternalHealth
        # above) — these URLs are stable month to month, only the PDF content changes.
        "pdf_urls": [
            "https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_Broward.pdf",
            "https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_MiamiDade.pdf",
            "https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_PalmBeach.pdf",
        ],
    },
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_community_care_plan_seeded_as_pdf_directory -v`
Expected: PASS.

- [ ] **Step 6: Run both full test files to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass (25 passed, 2 deselected — the pre-existing 24 plus this task's 1 new test).

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py -v`
Expected: all pass (17 passed — unchanged from Task 2's checkpoint; this task doesn't touch `test_directory.py`).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/domain/directory_load.py src/network_probe/payers/roster_seed.py tests/test_payer_sources.py
git commit -m "$(cat <<'EOF'
feat(payers): wire Community Care Plan to its 3 live county PDFs

SOURCES["Community Care Plan"]: directory_access "none" -> "pdf-directory".
New PDF_DIRECTORIES["community-care-plan-fl-south-florida"] entry:
format "ccp", 3 static county URLs (Broward/Miami-Dade/Palm Beach --
this client's FL-South Florida market), all confirmed live 2026-07-15.
EOF
)"
```

---

### Task 4: Update `docs/payer-sources/MATRIX.md`

**Files:**
- Modify: `docs/payer-sources/MATRIX.md`

**Interfaces:**
- Consumes: the directory_access change from Task 3 (documents it; does not affect behavior).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Update the table row**

Find:

```
| Community Care Plan | FL-South Florida | Managed Medicaid | — | — | — | none | review: FL Medicaid MCO; resolver candidate 59064 unconfirmed; PDF-only directory. |
```

Replace with:

```
| Community Care Plan | FL-South Florida | Managed Medicaid | — | — | — | pdf-directory | **Directory LIVE 2026-07-15** — no FHIR API; network published only as 3 per-county monthly PDFs (Broward/Miami-Dade/Palm Beach, this client's full FL-South Florida market), all confirmed live and static (~18MB/~6,100 pages total, dated "As of 07/13/2026"). Wired via the "ccp" PDF format + `PDF_DIRECTORIES` multi-URL support. review: Stedi id candidate 59064 still unconfirmed (separate from directory access). |
```

- [ ] **Step 2: Update the narrative "Needs authorized API / no public source" note**

Find:

```
- **Community Care Plan** (none) — review: FL Medicaid MCO; resolver candidate 59064 unconfirmed; PDF-only directory.
```

Replace with:

```
- **Community Care Plan** (pdf-directory) — **Directory LIVE 2026-07-15** — 3 per-county monthly
  PDFs (Broward/Miami-Dade/Palm Beach), no FHIR API. review: Stedi id candidate 59064 still
  unconfirmed (separate concern from directory access, tracked below).
```

- [ ] **Step 3: Leave the "Review queue" section's Community Care Plan entry unchanged**

`docs/payer-sources/MATRIX.md`'s "Review queue" section also mentions Community Care Plan — that
entry is about the *Stedi id* (59064, unconfirmed), a completely separate concern from directory
access. Confirm you have not touched it: `grep -n "Community Care Plan" docs/payer-sources/MATRIX.md`
should show exactly 3 matches (the table row and narrative note you just edited, plus this
untouched review-queue line).

- [ ] **Step 4: Verify**

Run: `grep -c "^| Community Care Plan |" docs/payer-sources/MATRIX.md`
Expected: `1` (still exactly one table row — this task edits it in place, doesn't add a new one).

Run: `grep -n "Community Care Plan" docs/payer-sources/MATRIX.md`
Expected: 3 lines total (table row, narrative note, untouched review-queue line) — confirm the
table row and narrative note now say `pdf-directory`/"Directory LIVE 2026-07-15", and the
review-queue line is byte-identical to what `git show HEAD:docs/payer-sources/MATRIX.md` shows
for that same line (i.e., untouched).

- [ ] **Step 5: Commit**

```bash
git add docs/payer-sources/MATRIX.md
git commit -m "$(cat <<'EOF'
docs(payer-sources): document Community Care Plan's live PDF directory

Updates the table row and narrative note (both previously "none") to
reflect the 3 live per-county PDFs wired in the prior 3 commits. The
separate Stedi-id review-queue note (id 59064, unconfirmed) is
untouched -- unrelated to directory access.
EOF
)"
```

---

### Task 5: Full verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run both full test files**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_directory.py tests/test_payer_sources.py -k "not db" -v`
Expected: all pass, 0 failures (17 + 25 = 42 passed across the two files, 2 deselected from `test_payer_sources.py`'s db-marked test).

- [ ] **Step 2: Run the broader test suite to check for unrelated regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/ -k "not db" -q`
Expected: no new failures compared to the pre-Task-1 baseline. (If you see 2 failures in
`tests/test_override_seed.py` about a missing `.overrides/overrides.json` file, that's a
pre-existing, worktree-environment-only gap unrelated to this change — confirmed in two prior
sub-projects this session; it passes in the main repo checkout, just not in a fresh worktree that
never had that local, gitignored file. Don't treat it as a regression, but do record it in your
report.)

- [ ] **Step 3: Manual live verification — real download, real parse, real match**

This hits the real live CCP servers (~18MB total download) and writes real rows to the local
`payer_directory_entries` table — expected and intentional, matching the equivalent live-verification
step in the prior 3 plans this session. Requires `ENABLE_DIRECTORY_REFRESH` is NOT required for a
direct `load_directory()` call (that env flag only gates the *automatic* `monthly_refresh_loop`,
not a manual call) — but this DOES need a real database connection, so run it against this repo's
configured dev DB (same `.env` used throughout this session).

Run:
```bash
source .venv/bin/activate && python3 -c "
import sys
sys.path.insert(0, 'src')
from network_probe.domain.directory_load import load_directory
from network_probe.payers.adapters.db_directory import DbDirectoryAdapter
from network_probe.domain.models import ProviderQuery

n = load_directory('community-care-plan-fl-south-florida')
print('rows loaded:', n)
assert n > 10000, f'expected a large real row count across 3 counties, got {n}'

# Spot-check the read side (DbDirectoryAdapter) end-to-end -- zero code changes there, per the
# design's finding #5. 'Florent-Carre' is a real provider confirmed live in the Broward PDF.
adapter = DbDirectoryAdapter(payer_name='community-care-plan-fl-south-florida', payer_label='Community Care Plan')
q = ProviderQuery(
    payer='community-care-plan-fl-south-florida', plan_hint='', npi='0000000000',
    provider_first_name='Marie', provider_last_name='Florent-Carre', state='FL',
)
v = adapter.check_network(q)
print('Florent-Carre status:', v.status)
print('matched provider:', v.matched_provider)
assert str(v.status).endswith('IN_NETWORK'), v.status
print('ALL CHECKS PASSED')
"
```
Expected: prints `rows loaded:` with a large number (several thousand, summed across all 3
counties), `Florent-Carre status: NetworkStatus.IN_NETWORK`, a `matched provider` dict showing
`'npi': '0000000000'` (our side's NPI, not the directory's — matching the existing
`test_db_directory_adapter_attaches_our_npi` precedent), and `ALL CHECKS PASSED`.

If this fails, do not treat it as flaky and move on — report BLOCKED with the exact output. A row
count that's too low or zero would mean the parser or multi-URL loading is broken against the real
PDFs (not just the transcribed test fixture); a failed match would mean either the parser or the
already-existing, untouched read side has a real problem worth investigating before merging.

- [ ] **Step 4: Review the full diff before wrap-up**

Run: `git log --oneline -5` and `git diff <task-1-base>..HEAD --stat` (use the actual base commit
you recorded before Task 1).
Expected: 4 commits from this plan (Tasks 1-4), touching `src/network_probe/domain/directory_pdf.py`,
`src/network_probe/domain/directory_load.py`, `src/network_probe/payers/roster_seed.py`,
`tests/test_directory.py`, `tests/test_payer_sources.py`, `docs/payer-sources/MATRIX.md`.

No further commit needed for this task — it's verification-only. If Step 1, 2, or 3 fails, stop
and fix the responsible task before proceeding to close out this plan.

---

## After this plan

Aetna Better Health remains on hold (unrelated, carried over from the Medicaid sub-project). No
other Community-Care-Plan-specific follow-ups — the design's scope (3 counties, `"Managed
Medicaid"` product only) is now fully wired.
