"""NPI → in-network TIN crosswalk (Phase 3 loader).

Most payer directories don't publish TINs (only Oscar embeds them). This loader lets us feed
TIN data from an external source — a Transparency-in-Coverage in-network file (NPI + TIN per
network), an 835/claims export, or a hand-built file — so `TinScopeSource` can do group-level
checks on every payer, not just Oscar.

It's a **skeleton, staged like StediSource**: point it at a file (arg or `TIN_CROSSWALK_PATH`
env) and it activates; with no file it's empty and TIN-scope just falls back to directory data
(or `inconclusive`). No rework needed when a real file arrives.

Accepted formats:
  • JSON dict:   {"<payer>": {"<npi>": ["<tin>", ...]}}
  • JSON list:   [{"payer": "...", "npi": "...", "tin": "..."}]  (or "tins": [...])
  • CSV:         header with columns npi, tin, and optional payer
A missing/blank payer is stored under "*" (applies to any payer for that NPI).
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Optional


def _digits(t) -> str:
    return re.sub(r"[^0-9]", "", str(t or ""))


class TinCrosswalk:
    def __init__(self, path: Optional[str] = None, records: Optional[list] = None):
        self.path = str(path) if path else os.environ.get("TIN_CROSSWALK_PATH")
        self._index: dict[tuple[str, str], set[str]] = {}
        if records is not None:
            for r in records:
                self._add(r.get("payer"), r.get("npi"), r.get("tin"), r.get("tins"))
        elif self.path and Path(self.path).exists():
            self._load(Path(self.path))

    def _add(self, payer, npi, tin=None, tins=None) -> None:
        if not npi:
            return
        key = ((payer or "*").strip().lower(), str(npi).strip())
        bucket = self._index.setdefault(key, set())
        for t in ([tin] if tin else []) + list(tins or []):
            if t:
                bucket.add(str(t).strip())

    def _load(self, p: Path) -> None:
        if p.suffix.lower() == ".csv":
            with p.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    row = {(k or "").strip().lower(): v for k, v in row.items()}
                    self._add(row.get("payer"), row.get("npi"), row.get("tin"))
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for payer, mapping in data.items():
                for npi, tins in (mapping or {}).items():
                    self._add(payer, npi, tins=tins if isinstance(tins, list) else [tins])
        elif isinstance(data, list):
            for r in data:
                self._add(r.get("payer"), r.get("npi"), r.get("tin"), r.get("tins"))

    def tins_for(self, payer: Optional[str], npi: Optional[str]) -> list[str]:
        if not npi:
            return []
        npi = str(npi).strip()
        hit = self._index.get(((payer or "").strip().lower(), npi), set())
        anyp = self._index.get(("*", npi), set())
        return sorted(hit | anyp)

    def __bool__(self) -> bool:
        return bool(self._index)


# United Vein & Vascular Centers billing entities (operator-provided roster). Public provider
# reference (group NPI + EIN + state), NOT PHI. This is the practice's own set of billing TINs —
# the thing a claim's `q.tin` is checked against. Group NPI 1053977801 is shared by two entities
# (NJ UVC Medical + Vascular Health LLC); both are kept.
UVC_ENTITIES = [
    {"entity": "Arizona UVC Medical, PLLC dba United Vein & Vascular Centers",
     "tin": "843447602", "group_npi": "1548800980", "state": "AZ"},
    {"entity": "Colorado Medical Group, PLLC dba United Vein & Vascular Centers",
     "tin": "475181686", "group_npi": "1356714638", "state": "CO"},
    {"entity": "Georgia UVC Medical, LLC dba United Vein & Vascular Centers",
     "tin": "921600050", "group_npi": "1619681244", "state": "GA"},
    {"entity": "Illinois UVC Medical, PLLC dba United Vein & Vascular Centers",
     "tin": "843012976", "group_npi": "1689224719", "state": "IL"},
    {"entity": "New York Medical United, PLLC dba United Vein & Vascular Centers",
     "tin": "880715104", "group_npi": "1265121693", "state": "NY"},
    {"entity": "NJ UVC Medical, PLLC dba United Vein & Vascular Centers",
     "tin": "931867629", "group_npi": "1053977801", "state": "NJ"},
    {"entity": "Wazni, PLLC dba United Vein & Vascular Centers",
     "tin": "463812940", "group_npi": "1114353026", "state": "FL"},
    {"entity": "Srinivas Rao MD PA dba Texas Vein & Wellness Institute",
     "tin": "412049581", "group_npi": "1972941318", "state": "TX-Houston"},
    {"entity": "Texas UVC Medical, PLLC dba United Vein & Vascular Centers",
     "tin": "933510922", "group_npi": "1447023528", "state": "TX-Dallas"},
    {"entity": "Vascular Health LLC",
     "tin": "834407175", "group_npi": "1053977801", "state": "NJ"},
]

# TiC-verified rendering-NPI → in-network TIN records — the small "result" extracted from the
# huge payer Transparency-in-Coverage MRFs (we never store the MRFs themselves). Seeded with the
# UnitedHealthcare TX-exchange slice (TXNETWORKEXGN, 2026-06): rendering NPIs 1972603934 (Kevin
# Fradkin) and 1710305735 bill under Texas UVC Medical's EIN 93-3510922. Extend by dropping more
# records into .cache/tic_crosswalk.json (what the operator extracts from the laptop TiC check)
# or by pointing TIN_CROSSWALK_PATH at a bulk crosswalk file.
_TIC_SEED_RECORDS = [
    {"payer": "uhc", "npi": "1972603934", "tin": "933510922"},
    {"payer": "uhc", "npi": "1710305735", "tin": "933510922"},
]

# Demo "result" cache (gitignored). Holds {roster, records}; layered on top of the in-code seed.
TIC_CACHE_PATH = Path(".cache/tic_crosswalk.json")


def _ingest_tic_cache(cw: "TinCrosswalk", data: dict) -> None:
    """Ingest a {roster, records} blob. Roster group-NPIs bill under their own EIN for any payer;
    records are explicit rendering-NPI → TIN facts (per payer). EINs are normalized to digits."""
    for e in data.get("roster") or []:
        cw._add(e.get("payer") or "*", e.get("group_npi") or e.get("npi"), tin=_digits(e.get("tin")))
    for r in data.get("records") or []:
        tins = [_digits(t) for t in (r.get("tins") or [])] or None
        cw._add(r.get("payer"), r.get("npi"), tin=_digits(r.get("tin")) or None, tins=tins)


def roster() -> list[dict]:
    """The UVC billing-entity roster (entity, EIN, group NPI, state)."""
    return [dict(e) for e in UVC_ENTITIES]


def build_tic_cache(path: Path = TIC_CACHE_PATH) -> Path:
    """(Re)write the gitignored .cache/tic_crosswalk.json with the UVC roster + seed TiC records,
    preserving any records already dropped in. This is the demo "result" file — extend its
    "records" list with what the laptop TiC check yields; the huge MRFs are never stored."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {}
    if path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            blob = {}
    records = blob.get("records") or []
    have = {(r.get("payer"), r.get("npi"), _digits(r.get("tin"))) for r in records}
    for r in _TIC_SEED_RECORDS:
        key = (r["payer"], r["npi"], _digits(r["tin"]))
        if key not in have:
            records.append(dict(r))
    blob["roster"] = UVC_ENTITIES
    blob["records"] = records
    path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    return path


# module-level singleton so the file is read once, not per check
_DEFAULT: Optional[TinCrosswalk] = None
_LOADED = False


def default_crosswalk() -> TinCrosswalk:
    """Layered crosswalk: in-code TiC seed + the UVC roster (recognize the practice's own billing
    TINs out of the box) + .cache/tic_crosswalk.json (demo result slice) + any TIN_CROSSWALK_PATH
    bulk file."""
    global _DEFAULT, _LOADED
    if not _LOADED:
        cw = TinCrosswalk(records=_TIC_SEED_RECORDS)
        _ingest_tic_cache(cw, {"roster": UVC_ENTITIES})
        if TIC_CACHE_PATH.exists():
            try:
                _ingest_tic_cache(cw, json.loads(TIC_CACHE_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        envp = os.environ.get("TIN_CROSSWALK_PATH")
        if envp and Path(envp).exists():
            cw._load(Path(envp))
        _DEFAULT = cw
        _LOADED = True
    return _DEFAULT


def main() -> None:
    p = build_tic_cache()
    blob = json.loads(p.read_text(encoding="utf-8"))
    print(f"wrote {p} — roster {len(blob.get('roster', []))} entities, "
          f"records {len(blob.get('records', []))}")


if __name__ == "__main__":
    main()
