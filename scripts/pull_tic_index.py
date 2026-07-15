"""Index-driven CMS TiC in-network MRF downloader and ingester.

Given a CMS TiC index (table-of-contents) URL, this script:

1. Downloads the index JSON.
2. Parses it to a flat list of in-network file entries (each with its plan
   names, market types, description, and download URL).
3. Optionally filters entries by state or plan-name substring.
4. Downloads each selected in-network file, runs the existing ``ingest_tic``
   ingester filtered to the practice's TINs/NPIs, and writes a deduplicated
   NPI->TIN crosswalk CSV.

Usage
-----
    python scripts/pull_tic_index.py \\
        --index-url '<signed index url>' \\
        --state AZ \\
        --payer cigna \\
        --tin-file practice-tins.txt \\
        --out cigna-az.csv

Use ``--list`` to preview the selected files without downloading.

Geo-restriction
---------------
Cigna's provider-reference files are served from a geo-restricted AWS
CloudFront distribution; requests from outside the US return 403.  Run this
script from a **US IP address** (EC2 / Fargate in ``us-east-1`` or
``us-west-2``) for Cigna and Aetna MRFs.  UHC's Azure CDN is open.

This module reuses :func:`network_probe.domain.tic_ingest.ingest_tic`, which
streams each file (constant memory), honours ``tin_filter`` / ``npi_filter``,
and resolves external ``provider_references[].location`` files (Pass-3) using
its own default HTTP resolver.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_index(data: dict) -> list[dict]:
    """Flatten a CMS TiC index JSON to a list of in-network file entries.

    Each entry dict has:

    * ``location``    (str)       -- URL to the in-network MRF (.json.gz)
    * ``plans``       (list[str]) -- plan names from the sibling reporting_plans
    * ``market``      (list[str]) -- plan_market_type values from sibling plans
    * ``description`` (str)       -- file description from the index

    Parameters
    ----------
    data:
        Parsed JSON dict from a CMS TiC index file.  Expected shape::

            {"reporting_structure": [
                {
                    "reporting_plans": [
                        {"plan_name": ..., "plan_market_type": ...}
                    ],
                    "in_network_files": [
                        {"description": ..., "location": ...}
                    ]
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


def select_files(
    entries: list[dict],
    state: str | None = None,
    plan_contains: str | None = None,
) -> list[dict]:
    """Filter index entries to those matching the given criteria.

    Matching is case-insensitive substring search.

    * ``state``         -- matched against each entry's location URL, plan
      names, market types, and description (e.g. ``"AZ"`` or ``"arizona"``).
    * ``plan_contains`` -- matched against plan names and market types only.

    If no filter is given, all entries are returned.  If a filter is given but
    matches nothing, ``[]`` is returned (the caller should warn and list what
    is available).  When both filters are active, an entry must satisfy both
    (intersection).
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
            return needle in e["description"].lower()

        result = [e for e in result if _state_match(e)]

    if plan_contains is not None:
        needle = plan_contains.lower()

        def _plan_match(e: dict) -> bool:
            if any(needle in p.lower() for p in e["plans"]):
                return True
            return any(needle in m.lower() for m in e["market"])

        result = [e for e in result if _plan_match(e)]

    return result


def _stream_download(url: str, workdir: str | None = None) -> str:
    """Stream-download *url* to a temp file; return the local path.

    Preserves the file extension from the URL path (e.g. ``.json.gz``) so
    :func:`ingest_tic` correctly detects gzip.  Uses httpx with
    ``follow_redirects=True`` so signed-URL redirects (e.g. Cigna pre-signed
    URLs) work transparently.

    Parameters
    ----------
    url:
        Full HTTPS URL to download.
    workdir:
        Directory to write the temp file into.  ``None`` -> OS temp directory.
    """
    import httpx

    # Derive a sensible suffix from the URL path (strip the query string first).
    url_path = url.split("?")[0]
    suffix = "".join(Path(url_path).suffixes[-2:]) or ".json"
    with tempfile.NamedTemporaryFile(dir=workdir, suffix=suffix, delete=False) as tmp:
        local_path = tmp.name

    with httpx.Client(follow_redirects=True, timeout=120) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):  # 1 MB chunks
                    f.write(chunk)

    return local_path


def _normalize_tin(tin: str) -> str:
    """Strip all non-digit characters from a TIN string."""
    return re.sub(r"\D", "", tin)


def _load_tin_filter(tin_file: str | None) -> set[str] | None:
    """Load a TIN filter set from *tin_file* (one per line; '#' = comment)."""
    if not tin_file:
        return None
    lines = Path(tin_file).read_text(encoding="utf-8").splitlines()
    raw = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return {_normalize_tin(t) for t in raw}


def _load_npi_filter(npi_file: str | None) -> set[str] | None:
    """Load an NPI filter set from *npi_file* (one per line)."""
    if not npi_file:
        return None
    lines = Path(npi_file).read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip()}


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
    limit: int | None = None,
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
        Case-insensitive substring filter applied to plan names and markets.
    payer:
        Payer label written to the ``payer`` column (e.g. ``"cigna"``).
    workdir:
        Directory for temporary downloads.  Defaults to the OS temp directory.
    downloader:
        ``(url: str) -> local_path`` callable.  Defaults to
        :func:`_stream_download`.  **Inject a fake for tests** so no network is
        touched.  When a custom downloader is supplied, returned paths are
        treated as caller-owned and are never deleted (so fixtures survive).
    list_only:
        When ``True``, print selected files and return 0 without downloading.
    max_workers:
        Concurrent threads for downloads and ``ingest_tic`` resolver calls.
    keep:
        When ``True``, downloaded temp files are not deleted after ingestion.
    limit:
        Cap the selected file list to the first *limit* entries (after
        ``select_files()`` filtering) before downloading/ingesting.  ``None``
        (default) means unlimited -- unchanged existing behaviour.  Useful for
        bounding a run against very large in-network files where an exhaustive
        sweep would take too long to finish in one session.

    Returns
    -------
    int
        Number of unique (npi, tin) rows written to *out_csv*.
    """
    from network_probe.domain.tic_ingest import ingest_tic

    _dl = downloader if downloader is not None else _stream_download
    # Only clean up files we actually downloaded ourselves; never delete paths
    # returned by an injected downloader (those may be shared fixtures).
    _owns_downloads = downloader is None and not keep

    # --- Step 1: download + parse the index -------------------------------
    index_local = _dl(index_url)
    try:
        import gzip as _gzip

        _opener = _gzip.open if index_local.endswith(".gz") else open
        with _opener(index_local, "rt", encoding="utf-8") as f:
            index_data = json.load(f)
    finally:
        if _owns_downloads:
            Path(index_local).unlink(missing_ok=True)

    entries = parse_index(index_data)

    # --- Step 2: filter ----------------------------------------------------
    selected = select_files(entries, state=state, plan_contains=plan_contains)
    selected = selected[:limit] if limit else selected
    if not selected:
        print(
            f"WARNING: no files matched your filter "
            f"(state={state!r}, plan_contains={plan_contains!r}). "
            f"{len(entries)} file(s) available in index:"
        )
        for e in entries:
            print(f"  {e['location']}  [{', '.join(e['plans'][:3])}]")
        return 0

    print(f"Selected {len(selected)} file(s) from index:")
    for e in selected:
        print(f"  {e['location']}  [{', '.join(e['plans'][:3])}]")

    if list_only:
        return 0

    # --- Step 3: load TIN + NPI filters -----------------------------------
    tin_filter = _load_tin_filter(tin_file)
    npi_filter = _load_npi_filter(npi_file)

    # --- Step 4: download + ingest each selected file ---------------------
    all_rows: set[tuple[str, str, str]] = set()
    tmp_files: list[str] = []

    def _ingest_one(entry: dict) -> set[tuple[str, str, str]]:
        local = _dl(entry["location"])
        if _owns_downloads:
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
                for row in csv.DictReader(f):
                    rows.add((row["npi"], row["tin"], row.get("payer") or (payer or "")))
            return rows
        finally:
            Path(tmp_csv_path).unlink(missing_ok=True)

    workers = max(1, min(max_workers, len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_ingest_one, e): e for e in selected}
        for fut in as_completed(futures):
            try:
                all_rows.update(fut.result())
            except Exception as exc:  # noqa: BLE001
                entry = futures[fut]
                logger.warning("Failed to ingest %s: %s", entry["location"], exc)

    if _owns_downloads:
        for p in tmp_files:
            Path(p).unlink(missing_ok=True)

    # --- Step 5: write deduplicated out_csv -------------------------------
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["npi", "tin", "payer"])
        for npi, tin, pay in sorted(all_rows):
            writer.writerow([npi, tin, pay])

    unique = len(all_rows)
    print(f"Wrote {unique} unique NPI->TIN rows to {out_csv}")
    return unique


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for pull_tic_index."""
    parser = argparse.ArgumentParser(
        prog="pull_tic_index",
        description=(
            "Download a CMS TiC index and ingest selected in-network files "
            "into an NPI->TIN crosswalk CSV."
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
        help="Case-insensitive substring filter on location URL + plan names (e.g. AZ).",
    )
    parser.add_argument(
        "--plan-contains",
        default=None,
        metavar="SUBSTR",
        help="Case-insensitive substring filter on plan names + market types.",
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
        help="Concurrent threads for download + resolver (default: 16).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Cap the selected file list to the first N files (after filtering) "
            "before downloading/ingesting. Default: unlimited. Useful for "
            "bounding a run against very large in-network files."
        ),
    )

    args = parser.parse_args(argv)

    run(
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
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
