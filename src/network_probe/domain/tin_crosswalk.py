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
from pathlib import Path


class TinCrosswalk:
    def __init__(self, path: str | None = None, records: list | None = None):
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

    def tins_for(self, payer: str | None, npi: str | None) -> list[str]:
        if not npi:
            return []
        npi = str(npi).strip()
        hit = self._index.get(((payer or "").strip().lower(), npi), set())
        anyp = self._index.get(("*", npi), set())
        return sorted(hit | anyp)

    def __bool__(self) -> bool:
        return bool(self._index)


# Verified NPI→in-network-TIN records extracted from payer Transparency-in-Coverage MRFs.
# United Vein & Vascular Centers — Texas UVC Medical, PLLC (EIN 93-3510922), confirmed in-network in
# UnitedHealthcare's Texas individual/exchange network (TXNETWORKEXGN, 2026-06 MRF). That MRF lists
# rendering NPIs 1972603934 (Kevin Fradkin) and 1710305735 under this contracted TIN. Extend this
# with a full bulk ingest by pointing TIN_CROSSWALK_PATH at the parsed crosswalk file.
#
# 2026-07-08 UVC demo-cases TiC sweep (real production MRFs, each independently re-verified by a
# second agent re-fetching the same file): payer key matches the catalogue key used in
# uvc_demo.py's ProviderQuery so TinScopeSource's crosswalk fallback actually fires for these cases.
#   - Cigna (Colorado): NPI 1629339312 (Jing Li) -> TIN 475181686, "Colorado Medical Group PLLC",
#     present in 4 of 9 CO in-network files (national-oap, national-ppo, pathwell-oap, pathwell-ppo).
#     https://www.cigna.com/static/mrf/co/latest.json (2026-07 CO table of contents).
#   - Kaiser Foundation Health Plan of Colorado: NPI 1598895435 (Wende Moore's provider) ->
#     TIN 475181686, "United Vein Centers", in Kaiser's COMMERCIAL/Medicaid in-network file
#     (Kaiser publishes no Medicare Advantage MRF -- MA is federally TiC-exempt -- so this is the
#     closest available real evidence for the same legal entity/provider group; not MA-specific).
#     https://healthy.kaiserpermanente.org/pricing/innetwork/co/2026-07-01_KFHP-CO_index.json
#   - UnitedHealthcare of Arizona: NPI 1992078745 (Arthur Maydell) -> TIN 843447602, "John W Darr"
#     group, in UHC-AZ's commercial/exchange network file AZNETWORKEXGN (UHC publishes no Dual
#     Complete/MA MRF -- also TiC-exempt -- same caveat as Kaiser above).
#     https://transparency-in-coverage.uhc.com/api/v1/uhc/blobs/ (2026-07-01 AZNETWORKEXGN).
#   - Ambetter/Centene (Texas): NPI 1710305735 (Umang Patel) -> TIN 933510922, "Texas UVC Medical
#     PLLC", in the TX Ambetter in-network file (also lists a second TIN, 412049581, "Texas Vein
#     and Wellness Institute", for the same NPI -- both real, this provider bills under either).
#     https://www.centene.com/content/dam/centene/.../2026-06-29_centene-management-company-llc_ambetter-tx_in-network.json
#
# Checked and found NOT applicable/NOT present (documented here so this isn't re-researched):
#   - Meridian Health (IL) / Mercy Care (AZ): Medicaid managed-care organizations, outside the
#     federal TiC MRF mandate's scope (individual/group commercial issuers only) -- no file exists.
#   - Community Health Choice (TX Marketplace): real TiC files exist and were searched in full, but
#     NPI 1972603934 does not appear in any of them -- the files are themselves very sparse
#     (~16-19 total NPIs), so this reflects incomplete payer-published data, not a confirmed absence.
#   - Humana (FL): no real production MRF was reachable (their public transparency pages 404/error
#     as a JS shell with no exposed blob URL); the only URL found was Humana's own developer
#     *synthetic* sample dataset, correctly not used as a real finding.
#   - BCBS Anthem/Elevance (GA): NPI 1902811656 IS present in Anthem's real GA in-network files, but
#     Anthem masks billing TINs behind a representative NPI (tin.type="npi", not "ein") -- so this
#     payer's own MRF cannot confirm or refute the EIN-based billing TIN at all.
_SEED = [
    {"payer": "uhc", "npi": "1972603934", "tin": "933510922"},
    {"payer": "uhc", "npi": "1710305735", "tin": "933510922"},
    {"payer": "cigna-healthcare-co-denver", "npi": "1629339312", "tin": "475181686"},
    {"payer": "kaiser-permanente-co-denver", "npi": "1598895435", "tin": "475181686"},
    {"payer": "unitedhealthcare-az", "npi": "1992078745", "tin": "843447602"},
    {"payer": "ambetter-centene-tx-dallas", "npi": "1710305735", "tins": ["933510922", "412049581"]},
]


# module-level singleton so the file is read once, not per check
_DEFAULT: TinCrosswalk | None = None
_LOADED = False


def default_crosswalk() -> TinCrosswalk:
    """In-code verified seed (TiC-derived), with any TIN_CROSSWALK_PATH bulk file layered on top."""
    global _DEFAULT, _LOADED
    if not _LOADED:
        cw = TinCrosswalk(records=_SEED)
        envp = os.environ.get("TIN_CROSSWALK_PATH")
        if envp and Path(envp).exists():
            cw._load(Path(envp))
        _DEFAULT = cw
        _LOADED = True
    return _DEFAULT
