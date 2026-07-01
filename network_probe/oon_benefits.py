"""Out-of-network benefits from Stedi — demo slice (Phase 6, scoped to the members we have).

pVerify's 271 gives cost-share, but its network field is `Unknown`. Here we pull the *full*
271 benefits straight from Stedi for the members in `test-data/` and normalize them, so the UI
can show every benefit (in-network, out-of-network, not-applicable) under an **OON** tab next to
the network verdict.

Demo constraints (see docs/superpowers/specs/2026-07-01-oon-tic-demo-design.md):
- **No live fetch in the app.** Run the prefetch once; the app reads the saved copy.
- **PHI stays in `.cache/`** (gitignored). We never commit member benefits.
- Every 271 flows through `CachedClient`, so a member is fetched from Stedi at most once.

Prefetch:
    python -m network_probe.oon_benefits test-data/*.pdf

Identity note: eligibility is keyed on the **subscriber**, who is not always the patient (a
dependent report names the spouse/parent as subscriber). `subscriber_identity` handles both. The
270 is sent with the maximal identity (subscriber memberId+DOB+name and provider NPI+name) — the
superset that every payer we tested accepts (UHC needs only memberId+DOB; Humana also needs the
provider name; Devoted also needs the subscriber name).
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from ._http import CachedClient
from .corroboration import StediSource  # reuse endpoint + payer-id map (single source of truth)
from .report_ingest import _extract_text, _nppes_name, parse_report

CACHE_DIR = Path(".cache")
OON_INDEX = CACHE_DIR / "oon_benefits.json"

# X12 271 EB12 in-plan-network indicator -> human label.
_NET = {"Y": "In Network", "N": "Out of Network", "W": "Not Applicable"}


def network_label(code: Optional[str]) -> str:
    return _NET.get(code or "", "Unspecified")


def _fmt_value(b: dict) -> Optional[str]:
    """A benefit line carries either a percent (coinsurance) or a dollar amount (copay/ded/OOP)."""
    pct = b.get("benefitPercent")
    amt = b.get("benefitAmount")
    if pct not in (None, ""):
        try:
            return f"{float(pct) * 100:g}%"
        except (TypeError, ValueError):
            return str(pct)
    if amt not in (None, ""):
        try:
            return f"${float(amt):,.2f}"
        except (TypeError, ValueError):
            return f"${amt}"
    return None


def parse_oon(resp: dict) -> list[dict]:
    """Normalize a 271 `benefitsInformation` array into flat rows — ALL benefits, tagged by
    network so the UI can group Out-of-Network / In-Network / Not-Applicable."""
    rows: list[dict] = []
    for b in resp.get("benefitsInformation") or []:
        rows.append({
            "network": network_label(b.get("inPlanNetworkIndicatorCode")),
            "network_code": b.get("inPlanNetworkIndicatorCode"),
            "type": b.get("name") or b.get("code"),
            "code": b.get("code"),
            "value": _fmt_value(b),
            "coverage_level": b.get("coverageLevel"),
            "time": b.get("timeQualifier"),
            "service_types": b.get("serviceTypes") or [],
            "authorization": b.get("authOrCertIndicator"),
            "messages": b.get("additionalInformation") or [],
        })
    return rows


def oon_only(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("network_code") == "N"]


# --------------------------------------------------------------------------- identity

def _to_yyyymmdd(dob: Optional[str]) -> Optional[str]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dob or "")
    return f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}" if m else None


def subscriber_identity(pdf_path, text: Optional[str] = None) -> dict:
    """The subscriber the 270 must be keyed on. For a *Subscriber Verification* report the patient
    IS the subscriber (name from the filename `Last, First`, single DOB). For a *Dependent
    Verification* report the subscriber is a different person (spouse/parent) listed first — take
    the subscriber's first name from the body and the first DOB."""
    text = text if text is not None else _extract_text(pdf_path)
    fn = os.path.basename(str(pdf_path))
    m = re.match(r"Eligibility Report - ([A-Za-z'\-]+),\s*([A-Za-z'\-]+)", fn)
    plast, pfirst = (m.group(1), m.group(2)) if m else (None, None)

    mids = [c for c in re.findall(r"Member ID\s*:\s*([A-Za-z0-9]+)", text)
            if len(c) >= 5 and any(ch.isdigit() for ch in c)]
    dobs = re.findall(r"Date Of Birth\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", text)

    if "Dependent Verification" in text:
        subs = (re.findall(r"First\s*name\s*:\s*([A-Za-z'\-]+)", text)
                + re.findall(r"Firstname\s*:\s*([A-Za-z'\-]+)", text))
        first = subs[0] if subs else pfirst
    else:
        first = pfirst

    return {
        "member_id": mids[0] if mids else None,
        "dob": _to_yyyymmdd(dobs[0]) if dobs else None,
        "first_name": first,
        "last_name": plast,
        "patient": f"{pfirst} {plast}" if pfirst and plast else None,
    }


# --------------------------------------------------------------------------- fetch

def fetch_271(payer_id: str, provider: dict, subscriber: dict,
              client: CachedClient, service_types=("30",), api_key: Optional[str] = None) -> dict:
    """One 270 -> 271 via Stedi, through CachedClient (so a repeat member is a cache hit)."""
    api_key = api_key or os.environ.get("STEDI_API_KEY")
    body = {
        "tradingPartnerServiceId": payer_id,
        "provider": {k: v for k, v in provider.items() if v},
        "subscriber": {k: v for k, v in subscriber.items() if v},
        "encounter": {"serviceTypeCodes": list(service_types)},
    }
    return client.post_json(
        StediSource.BASE, content=json.dumps(body),
        headers={"Authorization": api_key, "content-type": "application/json"})


def prefetch(paths: list[str], client: Optional[CachedClient] = None,
             api_key: Optional[str] = None) -> dict:
    """Fetch + normalize + cache OON benefits for each eligibility PDF. Writes
    `.cache/oon_benefits.json` keyed by rendering NPI (matching the UI's sample keys)."""
    client = client or CachedClient()
    index: dict = {}
    for p in paths:
        name = os.path.basename(p)
        text = _extract_text(p)
        parsed = parse_report(text)
        payer_key = parsed.get("payer_key")
        payer_id = StediSource.PAYER_IDS.get(payer_key or "")
        idn = subscriber_identity(p, text)
        npi = parsed.get("npi")
        if not (payer_id and npi and idn["member_id"] and idn["dob"]):
            print(f"skip {name}: need payer_id+npi+member_id+dob "
                  f"(payer={payer_key}, npi={bool(npi)}, member={bool(idn['member_id'])})")
            continue
        pf, pl = _nppes_name(npi, client)
        provider = {"npi": npi, "firstName": pf, "lastName": pl or idn["last_name"]}
        subscriber = {"memberId": idn["member_id"], "dateOfBirth": idn["dob"],
                      "firstName": idn["first_name"], "lastName": idn["last_name"]}
        try:
            resp = fetch_271(payer_id, provider, subscriber, client, api_key=api_key)
        except Exception as exc:
            print(f"error {name}: {exc}")
            continue
        rows = parse_oon(resp)
        index[npi] = {
            "npi": npi,
            "patient": idn["patient"],
            "payer": (resp.get("payer") or {}).get("name"),
            "payer_key": payer_key,
            "plan": parsed.get("plan_name") or parsed.get("policy_type"),
            "benefits": rows,
            "oon_count": len(oon_only(rows)),
            "errors": resp.get("errors"),
        }
        print(f"  {idn['patient']:22} {payer_key:12} {len(rows):3} benefits · {len(oon_only(rows)):3} OON")
    CACHE_DIR.mkdir(exist_ok=True)
    OON_INDEX.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"wrote {OON_INDEX} ({len(index)} members)")
    return index


def load_oon(npi: Optional[str] = None):
    """Read the cached OON index (all members, or one by NPI). Empty when not prefetched."""
    if not OON_INDEX.exists():
        return {} if npi is None else None
    data = json.loads(OON_INDEX.read_text(encoding="utf-8"))
    return data if npi is None else data.get(npi)


def main(argv: Optional[list[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    paths: list[str] = []
    for a in argv:
        paths += glob.glob(a)
    paths = [p for p in sorted(set(paths)) if p.lower().endswith(".pdf") and "oon examples" not in p.lower()]
    if not paths:
        print("usage: python -m network_probe.oon_benefits test-data/*.pdf")
        return
    if not os.environ.get("STEDI_API_KEY"):
        print("STEDI_API_KEY not set — refusing to run (this is a live prefetch).")
        return
    print(f"prefetching OON benefits for {len(paths)} report(s)…")
    prefetch(paths)


if __name__ == "__main__":
    main()
