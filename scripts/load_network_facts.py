"""CLI: load an ingest_tic crosswalk CSV into the `provider_network_facts` table.

`scripts/ingest_tic.py` produces a `npi,tin,payer` CSV; the TiC signal
(`domain/tic_network.tic_network_status`) reads `provider_network_facts`. This is the step that
joins them — without it an MRF pull is silently inert.

Usage:
    python -m scripts.load_network_facts <crosswalk.csv> --payer-key oscar-ga-atlanta
        [--source tic] [--network-name "Individual Georgia HMO Open Access"] [--dry-run]

`--payer-key` is the market-qualified ROSTER key (e.g. "oscar-ga-atlanta"), not the short adapter
key ingest_tic writes into the CSV's payer column. Loading is idempotent — ProviderNetworkStore
skips rows already present by (payer_key, npi, tin, source) — so re-running is a no-op.
"""

from __future__ import annotations

import argparse
import sys

from network_probe.domain.tic_facts import facts_from_crosswalk_csv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="crosswalk CSV produced by scripts/ingest_tic.py")
    ap.add_argument("--payer-key", required=True, help="market-qualified roster key, e.g. oscar-ga-atlanta")
    ap.add_argument("--source", default="tic", help='fact source (default: "tic")')
    ap.add_argument("--network-name", default=None, help="optional network label to record on each fact")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, write nothing")
    args = ap.parse_args(argv)

    rows = facts_from_crosswalk_csv(
        args.csv_path, args.payer_key, source=args.source, network_name=args.network_name
    )
    if not rows:
        print(f"No usable rows in {args.csv_path} — nothing to load.")
        return 0

    tins = sorted({r["tin"] for r in rows})
    print(f"{len(rows)} fact(s) for payer_key={args.payer_key} across {len(tins)} TIN(s): {tins}")
    if args.dry_run:
        for r in rows[:20]:
            print(f"  would write: NPI {r['npi']}  TIN {r['tin']}  in_network=True  source={r['source']}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        return 0

    from network_probe.domain.network_facts import default_provider_network_store

    written = default_provider_network_store().upsert(rows)
    print(f"Wrote {written} new fact(s); {len(rows) - written} already present (idempotent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
