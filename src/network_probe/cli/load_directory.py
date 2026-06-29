"""Manually download + parse + load a payer's monthly PDF provider directory into the DB.

    python -m network_probe.cli.load_directory                      # load all known PDF directories
    python -m network_probe.cli.load_directory align-senior-health-plan-fl-south-florida

In production the app refreshes these automatically (ENABLE_DIRECTORY_REFRESH=1); this CLI is
for a one-off / cron-driven load.
"""

from __future__ import annotations

import sys

from network_probe.domain.directory_load import PDF_DIRECTORIES, load_directory


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    keys = args or list(PDF_DIRECTORIES)
    rc = 0
    for k in keys:
        try:
            n = load_directory(k)
            print(f"{k}: loaded {n} rows")
        except Exception as exc:  # noqa: BLE001
            print(f"{k}: ERROR {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
