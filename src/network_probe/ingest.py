"""Batch ingest: feed pVerify eligibility reports, get the network verdict for each.

    python -m network_probe.ingest test-data/*.pdf
    python -m network_probe.ingest --json report1.pdf report2.pdf

Fills the reports' own "Provider Network: Unknown" field.
"""

from __future__ import annotations

import glob
import json
import sys

from network_probe._http import CachedClient
from network_probe.report_ingest import parse_report, report_to_query
from network_probe.service import check_network


def verify_report(path: str, client: CachedClient) -> dict:
    parsed = parse_report(path)
    out = {"report": path.split("/")[-1], **{k: parsed[k] for k in ("payer_key", "npi", "state", "zip")},
           "plan": parsed.get("plan_name")}
    if not parsed.get("payer_key"):
        return {**out, "status": "ERROR", "detail": f"unmapped payer {parsed.get('payer_name')!r}"}
    if not parsed.get("npi"):
        return {**out, "status": "ERROR", "detail": "no provider NPI found in report"}
    q = report_to_query(parsed, client=client)
    try:
        v = check_network(q)
    except Exception as exc:
        return {**out, "status": "ERROR", "detail": str(exc)}
    return {**out, "provider": (v.matched_provider or {}).get("name") or f"{q.first_name or ''} {q.last_name or ''}".strip(),
            "status": v.status.value, "confidence": v.confidence, "why": v.notes}


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    as_json = "--json" in args
    paths = [p for a in args if a != "--json" for p in sorted(glob.glob(a))]
    if not paths:
        print("usage: python -m network_probe.ingest [--json] <report.pdf ...>", file=sys.stderr)
        return 2
    client = CachedClient()
    results = [verify_report(p, client) for p in paths]
    if as_json:
        print(json.dumps(results, indent=2))
        return 0
    print(f"\n{'REPORT':22} {'PAYER':11} {'NPI':11} {'NETWORK STATUS':16} CONF")
    print("-" * 78)
    for r in results:
        name = r["report"].replace("Eligibility Report - ", "")[:21]
        st = r.get("status", "?")
        print(f"{name:22} {str(r.get('payer_key')):11} {str(r.get('npi')):11} {st:16} {r.get('confidence','') or r.get('detail','')[:30]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
