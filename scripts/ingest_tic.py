"""CLI: stream a TiC in-network MRF into an NPI→TIN crosswalk CSV.

Usage:
    python -m scripts.ingest_tic <tic_path> <out_csv> [--payer PAYER] [--npi-file NPI_FILE]

Arguments:
    tic_path    Path to the TiC in-network MRF (.json or .json.gz).
    out_csv     Destination CSV (columns: npi, tin, payer).

Options:
    --payer     Payer label to write in the ``payer`` column (e.g. "uhc").
    --npi-file  Path to a plain-text file of NPIs to keep (one per line);
                all other NPIs are skipped, reducing output size.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_probe.domain.tic_ingest import ingest_tic


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
    args = parser.parse_args(argv)

    npi_filter = None
    if args.npi_file:
        lines = Path(args.npi_file).read_text(encoding="utf-8").splitlines()
        npi_filter = {line.strip() for line in lines if line.strip()}

    rows = ingest_tic(args.tic_path, args.out_csv, npi_filter=npi_filter, payer=args.payer)
    print(f"Wrote {rows} unique NPI→TIN rows to {args.out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
