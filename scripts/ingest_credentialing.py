"""Ingest a verification/credentialing sheet → PHI-free credentialing CSV.

Turns the clinic's Availity-verified determinations (or a credentialing/enrollment export) into the
CSV that `domain/credentialing.py` loads via ``CREDENTIALING_PATH``. Output rows are provider-contract
facts only — payer, NPI, billing TIN, in/out, plan — with NO patient identity (MRN/name/DOB dropped).

Input: an .xlsx/.csv with (case-insensitive) columns: Insurance, INN/OON, Market, NPI, TIN.
Output CSV columns: payer,npi,tin,in_network,plan,source

    python scripts/ingest_credentialing.py "Insurance Examples.xlsx" credentialing.csv --sheet Sheet1
    CREDENTIALING_PATH=credentialing.csv  # point the matrix at it
"""

from __future__ import annotations

import argparse
import csv
import re
import sys

from network_probe.payers.search import load_roster_rows, search_roster


def _in_network(status: str) -> bool | None:
    x = (status or "").lower()
    if x.startswith("inn") or x.strip() == "in":
        return True
    if "oon" in x or "out" in x or "physican oon" in x or "physician oon" in x:
        return False
    return None


def _first_tin(raw: str) -> str:
    parts = [p.strip() for p in re.split(r"\band\b|,|/", str(raw or "")) if p.strip().isdigit()]
    return parts[0] if parts else ""


def _rows_from(path: str, sheet: str | None):
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                yield {(k or "").strip().lower(): v for k, v in r.items()}
        return
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c or "").strip().lower() for c in rows[0]]
    for r in rows[1:]:
        yield {header[i]: r[i] for i in range(min(len(header), len(r)))}


def _cell(row: dict, *names) -> str:
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(int(v)) if isinstance(v, float) and v == int(v) else str(v).strip()
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ingest_credentialing", description=__doc__)
    ap.add_argument("in_path", help="Input .xlsx or .csv")
    ap.add_argument("out_csv", help="Output PHI-free credentialing CSV")
    ap.add_argument("--sheet", default=None, help="Worksheet name (xlsx; default: first)")
    ap.add_argument("--source", default="credentialing-ingest", help='Value for the "source" column')
    args = ap.parse_args(argv)

    roster = load_roster_rows()
    seen: set = set()
    out: list[dict] = []
    for row in _rows_from(args.in_path, args.sheet):
        ins = _cell(row, "insurance")
        npi = _cell(row, "npi")
        tin = _first_tin(_cell(row, "tin"))
        mkt = _cell(row, "market")
        inn = _in_network(_cell(row, "inn/oon", "inn/sub/oon status", "status"))
        if not (ins and npi and tin) or inn is None:
            continue
        opt = search_roster(roster, ins, 1, state=mkt) or search_roster(roster, ins, 1)
        payer = opt[0]["value"] if opt else ins
        key = (payer, npi, tin)
        if key in seen:
            continue
        seen.add(key)
        out.append({"payer": payer, "npi": npi, "tin": tin, "in_network": str(inn).lower(),
                    "plan": ins, "source": args.source})

    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["payer", "npi", "tin", "in_network", "plan", "source"])
        w.writeheader()
        w.writerows(out)
    print(f"Wrote {len(out)} PHI-free credentialing rows to {args.out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
