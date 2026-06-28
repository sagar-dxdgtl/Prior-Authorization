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
