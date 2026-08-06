"""Rebuild the ZIP → county crosswalk shipped in `network_probe/geo/zip_counties.csv.gz`.

Source: the Census Bureau's 2020 ZCTA-to-County Relationship File. Free, no registration, no API
key, and authoritative — it is the same geography the payer portals are built on. Verified
2026-08-06 against UHC Find Care's own county dropdown, which is the only ground truth that matters
here: 30101 -> Bartow/Cherokee/Cobb/Paulding, 30144 -> Cobb, 30188 -> Cherokee/Cobb. All three
matched exactly, including the counts.

DO NOT swap this for one of the popular ZIP libraries. `pgeocode`, `zipcodes` and `uszipcode` return
ONE county per ZIP — the dominant one — and on those same three ZIPs they answer "Cobb", "Cobb",
"Cherokee". That silently discards three of 30101's four counties, and Cobb vs Paulding is a 12-plan
list against a 14-plan one. A confident wrong county is worse than no county at all.

Run:  python -m scripts.build_zip_counties          (writes the .csv.gz in place)
"""

from __future__ import annotations

import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

SOURCE = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_county20_natl.txt"
)
OUT = Path(__file__).resolve().parents[1] / "src" / "network_probe" / "geo" / "zip_counties.csv.gz"


def build(source: str = SOURCE, out: Path = OUT) -> int:
    print(f"downloading {source} …", file=sys.stderr)
    with urllib.request.urlopen(source, timeout=300) as fh:  # noqa: S310 — fixed census.gov URL
        raw = fh.read().decode("utf-8-sig")

    rows: list[tuple[str, str, str, int]] = []
    for r in csv.DictReader(io.StringIO(raw), delimiter="|"):
        zcta = (r.get("GEOID_ZCTA5_20") or "").strip()
        fips = (r.get("GEOID_COUNTY_20") or "").strip()
        name = (r.get("NAMELSAD_COUNTY_20") or "").strip()
        if not (zcta and fips and name):
            continue
        rows.append((zcta, fips, name, int(r.get("AREALAND_PART") or 0)))

    if len(rows) < 40_000:  # the real file has ~47k pairs; a short read means a truncated download
        raise SystemExit(f"refusing to write a suspiciously small crosswalk ({len(rows)} pairs)")

    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in sorted(rows):
        writer.writerow(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(buf.getvalue().encode(), 9))

    zips = {r[0] for r in rows}
    multi = len({r[0] for r in rows}) - len({r[0] for r in rows if sum(1 for x in rows if x[0] == r[0]) == 1}) \
        if len(rows) < 5_000 else None  # the O(n^2) count is only worth it on a toy input
    print(f"wrote {out} — {len(rows)} pairs over {len(zips)} ZCTAs, "
          f"{out.stat().st_size // 1024} KB" + (f", {multi} multi-county" if multi else ""),
          file=sys.stderr)
    return len(rows)


if __name__ == "__main__":
    build()
