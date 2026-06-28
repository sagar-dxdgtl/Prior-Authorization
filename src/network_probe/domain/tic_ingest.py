"""Streaming TiC in-network MRF → NPI→TIN crosswalk CSV ingester.

Extracts provider_groups from BOTH top-level ``provider_references[]`` and
``in_network[].negotiated_rates[]``, deduplicates, and writes a CSV with
columns ``npi,tin,payer``.  Constant memory: the file is never fully loaded;
ijson streams events one object at a time.

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
import re
from pathlib import Path

import ijson


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


def ingest_tic(
    tic_path: str,
    out_csv: str,
    npi_filter=None,
    tin_filter=None,
    payer: str | None = None,
) -> int:
    """Stream a TiC in-network MRF (.json/.json.gz) → npi,tin,payer CSV.

    Returns the number of unique (npi, tin) rows written.

    Extracts provider_groups from BOTH ``provider_references[]`` (top-level)
    and ``in_network[].negotiated_rates[]`` (embedded).  Constant memory:
    never materialises the whole file.

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
    """
    nf = set(map(str, npi_filter)) if npi_filter else None
    tf = {_normalize_tin(str(t)) for t in tin_filter} if tin_filter else None
    seen: set[tuple[str, str]] = set()

    with open(out_csv, "w", newline="", encoding="utf-8") as outf:
        writer = csv.writer(outf)
        writer.writerow(["npi", "tin", "payer"])

        # Pass 1 — top-level provider_references
        with _open(tic_path) as f:
            for pr in ijson.items(f, "provider_references.item"):
                _emit(pr.get("provider_groups"), nf, tf, payer, writer, seen)

        # Pass 2 — provider_groups embedded inside each negotiated_rate
        with _open(tic_path) as f:
            for rate in ijson.items(f, "in_network.item.negotiated_rates.item"):
                _emit(rate.get("provider_groups"), nf, tf, payer, writer, seen)

    return len(seen)
