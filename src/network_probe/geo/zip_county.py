"""Which counties a US ZIP touches — the question the payer portals actually key their plans on.

A ZIP is not a place. US ZIP codes are USPS *delivery routes*, so they do not nest inside counties:
**30% of them (10,186 of 33,791) span more than one**, and one spans six. Two different ZIPs also
routinely share a county — 30144 (Kennesaw) and 30188 (Woodstock) are both partly Cobb. Comparing
ZIP strings is therefore neither necessary nor sufficient for "same county", which is why this module
exists and why it answers in county SETS rather than a single county.

That matters because Medicare Advantage plans are sold by county of residence, and the county picks
the plan list: measured live on 2026-08-06, ZIP 30101's four counties offer 11, 12, 14 and 14 plans,
with GA-D001 only in Cobb and GA-2 (PPO) only in Bartow/Paulding.

Data: the Census Bureau's 2020 ZCTA-to-County Relationship File, distilled by
`scripts/build_zip_counties.py`. Free, no key, offline, and verified against UHC Find Care's own
county dropdown on all three ZIPs above — exact match, including the counts. See that script for why
the popular one-county-per-ZIP libraries must not be substituted.

`land_share` is carried for ordering and for showing a human which county dominates. It is
deliberately NEVER used to decide anything: ZIP 30188 is 99.1% Cherokee by land, and picking Cherokee
on that basis would still be a guess about where one member lives.
"""

from __future__ import annotations

import csv
import gzip
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).with_name("zip_counties.csv.gz")


@dataclass(frozen=True)
class County:
    fips: str  # 5-digit state+county FIPS, e.g. "13067"
    name: str  # "Cobb County"
    land_share: float  # 0..1 of the ZIP's land area — display/ordering ONLY, never a decision


@lru_cache(maxsize=1)
def _table() -> dict[str, tuple[County, ...]]:
    """ZIP -> counties, most land first. Missing data file = empty table, never an exception:
    this is a refinement on top of a working walk, not something the walk depends on."""
    if not _DATA.exists():
        return {}
    acc: dict[str, list[tuple[str, str, int]]] = {}
    with gzip.open(_DATA, "rt", encoding="utf-8") as fh:
        for row in csv.reader(io.StringIO(fh.read())):
            if len(row) != 4:
                continue
            zcta, fips, name, area = row
            acc.setdefault(zcta, []).append((fips, name, int(area or 0)))
    out: dict[str, tuple[County, ...]] = {}
    for zcta, parts in acc.items():
        total = sum(p[2] for p in parts) or 1
        out[zcta] = tuple(
            County(fips=f, name=n, land_share=a / total)
            for f, n, a in sorted(parts, key=lambda p: -p[2])
        )
    return out


def _norm(zip_code: str | None) -> str:
    """First five digits. Accepts ZIP+4 ("30144-1234") and stray whitespace."""
    digits = "".join(ch for ch in (zip_code or "") if ch.isdigit())
    return digits[:5] if len(digits) >= 5 else ""


def counties_for_zip(zip_code: str | None) -> tuple[County, ...]:
    """Every county this ZIP touches, most land area first. Empty tuple when unknown."""
    return _table().get(_norm(zip_code), ())


def spans_multiple_counties(zip_code: str | None) -> bool:
    """Whether this ZIP is county-ambiguous. False for an unknown ZIP — absence of data is not
    evidence of ambiguity, and the caller must not read it as a reason to decline."""
    return len(counties_for_zip(zip_code)) > 1


def describe(zip_code: str | None) -> str:
    """Human phrasing for a note: 'entirely Cobb County' / 'spanning Cobb, Paulding and 2 more'."""
    got = counties_for_zip(zip_code)
    if not got:
        return ""
    if len(got) == 1:
        return f"entirely {got[0].name}"
    names = [c.name for c in got]
    head = ", ".join(names[:2])
    return f"spanning {head}" + (f" and {len(names) - 2} more" if len(names) > 2 else f" and {names[-1]}")


def same_county(zip_a: str | None, zip_b: str | None) -> bool | None:
    """Are these two ZIPs certainly in the same county?

    Three-valued on purpose, because two of the answers are actionable and the third is not:

      * True  — both resolve to the SAME single county, so anything keyed on county is safe.
      * False — their county sets are DISJOINT, so they are certainly different counties.
      * None  — unknown ZIP, or the sets overlap without pinning one (e.g. 30144 is Cobb while 30188
                is Cherokee-or-Cobb: the member may or may not share the clinic's county). Callers
                must treat None as "not established", never as either answer.
    """
    a, b = counties_for_zip(zip_a), counties_for_zip(zip_b)
    if not a or not b:
        return None
    fa, fb = {c.fips for c in a}, {c.fips for c in b}
    if len(fa) == 1 and fa == fb:
        return True
    if not (fa & fb):
        return False
    return None
