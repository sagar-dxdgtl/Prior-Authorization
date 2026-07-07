"""CLI: python -m network_probe.cli --payer oscar --npi ... --last-name ... --plan ...

Prints a human-readable verdict, and the full verdict as JSON with --json.
"""

from __future__ import annotations

import argparse
import json
import sys

from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.service import check_network

_STATUS_GLYPH = {
    NetworkStatus.IN_NETWORK: "✓ IN-NETWORK",
    NetworkStatus.OUT_OF_NETWORK: "✗ OUT-OF-NETWORK",
    NetworkStatus.UNKNOWN: "? UNKNOWN",
    NetworkStatus.REVIEW: "⚠ NEEDS REVIEW (sources conflict)",
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m network_probe.cli",
        description="Verify whether a provider is in-network for a given payer plan.",
    )
    p.add_argument("--payer", required=True, help="payer key, e.g. 'oscar'")
    p.add_argument(
        "--plan",
        required=True,
        dest="plan_hint",
        help="plan name / hint, e.g. 'BASE SILVER CSR 150' or 'SILVERSIMPLEPCPSAVER'",
    )
    p.add_argument("--npi", help="provider NPI (preferred match key)")
    p.add_argument("--first-name", dest="first_name")
    p.add_argument("--last-name", dest="last_name")
    p.add_argument("--state", help="2-letter state, e.g. FL")
    p.add_argument("--zip", dest="zip_code", help="patient ZIP, for plan/network scoping")
    p.add_argument("--tin", help="member's billing TIN (group-level network check)")
    p.add_argument("--year", type=int, help="coverage year (default: current year)")
    p.add_argument(
        "--base-url", dest="base_url", help="FHIR PDEX base URL (for --payer fhir, e.g. https://fhir.humana.com/api)"
    )
    p.add_argument("--no-cache", action="store_true", help="disable on-disk response cache")
    p.add_argument(
        "--no-corroborate", action="store_true", help="skip cross-source corroboration (NPPES) + confidence demotion"
    )
    p.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    q = ProviderQuery(
        payer=args.payer,
        plan_hint=args.plan_hint,
        npi=args.npi,
        provider_first_name=args.first_name,
        provider_last_name=args.last_name,
        state=args.state,
        zip_code=args.zip_code,
        tin=args.tin,
    )

    adapter_kwargs = {}
    if args.year:
        adapter_kwargs["year"] = args.year
    if args.base_url:
        adapter_kwargs["base_url"] = args.base_url
    if args.no_cache:
        from network_probe.core._http import CachedClient

        adapter_kwargs["client"] = CachedClient(cache_dir=None)

    try:
        verdict = check_network(q, corroborate=not args.no_corroborate, **adapter_kwargs)
    except Exception as exc:  # network errors, bad payer, etc.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2))
        return 0

    glyph = _STATUS_GLYPH.get(verdict.status, verdict.status.value)
    who = " ".join(x for x in [args.first_name, args.last_name] if x) or args.npi or "(provider)"
    print(f"\n  {glyph}   (confidence: {verdict.confidence})")
    print(f"  provider : {who}" + (f"  NPI {args.npi}" if args.npi else ""))
    print(f"  plan/net : {verdict.plan_or_network_checked}")
    print(f"  why      : {verdict.notes}")
    if verdict.matched_provider:
        mp = verdict.matched_provider
        name = mp.get("display_name") or mp.get("name") or "(provider)"
        print(f"  matched  : {name} (NPI {mp.get('npi')}, {mp.get('specialty')})")
    for sig in verdict.corroboration or []:
        print(f"  x-check  : [{sig['source']}] {sig['result']} — {sig['detail']}")
    print(f"  source   : {verdict.source_url}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
