"""Provider credentialing matrix — the clinic's own (payer, plan, NPI, TIN) → in/out-of-network
contract record. The authoritative, PHI-free provider-INN signal.

Why this exists (see docs/roadmap/TODO-network-accuracy.md):
  - A public provider *directory* answers "is this NPI in the payer's network?" (payer-scoped,
    TIN-blind) — it can't answer "in THIS member's plan network, billing under THIS TIN."
  - A TiC MRF answers NPI+TIN→plan, but only for COMMERCIAL/ACA lines (Medicare Advantage and
    Medicaid are federally TiC-exempt) — so it misses most MA/Medicaid providers.
  - The clinic's own credentialing/enrollment records cover EVERY line (MA/Medicaid/commercial),
    need no PHI, and are the truest source (they are the contracts). This module holds them.

This is NOT a per-member override: a record is keyed by (payer, plan, NPI, billing TIN) — a
contract fact that generalizes to ANY member on that plan seeing that provider, so it survives a
live check on a new patient. Contrast with overrides.py, which is per-(member, provider) verdict.

Seed (`_SEED`): the client's Availity-verified determinations for the example roster, reduced to
PHI-free (payer, npi, tin, in_network) rows — no patient identity. Extend at runtime with the
clinic's full credentialing export via ``CREDENTIALING_PATH`` (CSV or JSON):

    CSV:  payer,npi,tin,in_network[,plan,source,effective_date]     (in_network: true/false/1/0/y/n)
    JSON: [{"payer","npi","tin","in_network","plan","source","effective_date"}]
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


def _norm_tin(t) -> str:
    return re.sub(r"[^0-9]", "", str(t or ""))


def _as_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "y", "yes", "in", "in_network", "inn"):
        return True
    if s in ("false", "0", "n", "no", "out", "oon", "out_of_network"):
        return False
    return None


@dataclass
class CredentialRecord:
    payer: str  # catalogue/roster key the verdict flow uses as q.payer (e.g. "unitedhealthcare-az")
    npi: str
    tin: str
    in_network: bool
    plan: str | None = None  # plan/line label; disambiguates when a provider's status differs by plan
    source: str | None = None
    effective_date: str | None = None


# Client Availity-verified determinations for the example roster (Insurance Examples 7-2-26), reduced
# to PHI-free (payer, npi, tin, in_network, plan). Same provider+TIN legitimately differs by payer
# because a contract is payer-specific (NPI 1992078745 / TIN 843447602 is IN for BCBS-AZ, OON for
# Mercy Care). Covers the FL (463812940) and IL (843012976) groups that TiC cannot reach (MA/Medicaid).
_SRC = "client-availity-2026-07-02"
# (payer_key, npi, tin, in_network, plan_label)
_SEED_ROWS = [
    ("aetna-az", "1346866332", "843447602", False, "Aetna Medicare AZ"),
    ("ambetter-centene-tx-houston", "1710305735", "933510922", True, "Ambetter ACA"),
    ("bcbs-empire-anthem-elevance-az", "1992078745", "843447602", True, "BCBS AZ"),
    ("bcbs-empire-anthem-elevance-ga-atlanta", "1902811656", "921600050", True, "BCBS Anthem Georgia"),
    ("cigna-healthcare-co-denver", "1629339312", "475181686", False, "Cigna Commercial CO"),
    ("community-health-choice-chc-tx-houston", "1972603934", "933510922", False, "CHC Marketplace"),
    ("first-health-fl-south-florida", "1336160274", "463812940", False, "Medicare FL First Coast"),
    ("humana-co-denver", "1801837109", "475181686", True, "Humana Medicare CO"),
    ("kaiser-permanente-co-denver", "1598895435", "475181686", False, "Kaiser Medicare"),
    ("mercy-care-az", "1992078745", "843447602", False, "Mercy Care AHCCCS"),
    ("meridian-health-il", "1043330285", "843012976", False, "Meridian Health"),
    ("meridian-health-il", "1588744650", "843012976", False, "Meridian Health"),
    ("national-government-services-inc-ngs-il", "1770578221", "843012976", True, "Medicare IL NGS"),
    ("noridian-healthcare-solutions-llc-az", "1346866332", "843447602", True, "Medicare AZ Noridian"),
    ("oscar-fl-south-florida", "1568423168", "463812940", False, "Oscar Health"),
    ("unitedhealthcare-az", "1245461292", "843447602", False, "UHC Dual Complete"),
    ("unitedhealthcare-fl-south-florida", "1760457477", "463812940", False, "UHC AARP Medicare Advantage"),
    ("unitedhealthcare-ga-atlanta", "1902811656", "921600050", True, "UHC Medicare Advantage GA"),
]
_SEED = [CredentialRecord(p, n, t, i, plan=pl, source=_SRC) for (p, n, t, i, pl) in _SEED_ROWS]


def _plan_overlap(a: str | None, b: str | None) -> int:
    """Cheap token overlap so a query plan hint ("AARP Medicare Advantage") matches a record's plan
    label ("Medicare Advantage"). 0 when either side is unknown."""
    if not a or not b:
        return 0
    ta = {t for t in re.sub(r"[^a-z0-9 ]", " ", a.lower()).split() if len(t) > 2}
    tb = {t for t in re.sub(r"[^a-z0-9 ]", " ", b.lower()).split() if len(t) > 2}
    return len(ta & tb)


class CredentialingMatrix:
    def __init__(self, records: list | None = None, path: str | None = None):
        self._items: list[CredentialRecord] = list(records) if records is not None else list(_SEED)
        p = path or os.environ.get("CREDENTIALING_PATH")
        if p and Path(p).exists():
            self._load(Path(p))

    def _load(self, p: Path) -> None:
        if p.suffix.lower() == ".csv":
            with p.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    row = {(k or "").strip().lower(): v for k, v in row.items()}
                    inn = _as_bool(row.get("in_network"))
                    if row.get("npi") and row.get("tin") and inn is not None:
                        self._items.append(CredentialRecord(
                            payer=row.get("payer", ""), npi=str(row["npi"]).strip(),
                            tin=str(row["tin"]).strip(), in_network=inn, plan=row.get("plan") or None,
                            source=row.get("source") or "credentialing-file",
                            effective_date=row.get("effective_date") or None,
                        ))
            return
        for r in json.loads(p.read_text(encoding="utf-8")):
            inn = _as_bool(r.get("in_network"))
            if r.get("npi") and r.get("tin") and inn is not None:
                self._items.append(CredentialRecord(
                    payer=r.get("payer", ""), npi=str(r["npi"]).strip(), tin=str(r["tin"]).strip(),
                    in_network=inn, plan=r.get("plan"), source=r.get("source") or "credentialing-file",
                    effective_date=r.get("effective_date"),
                ))

    def lookup(self, payer, npi, tin, plan: str | None = None) -> CredentialRecord | None:
        pl = (payer or "").lower()
        n, t = str(npi or "").strip(), _norm_tin(tin)
        hits = [r for r in self._items if r.payer.lower() == pl and r.npi == n and _norm_tin(r.tin) == t]
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        # multiple records for the same (payer, npi, tin) → pick the best plan match
        return max(hits, key=lambda r: _plan_overlap(plan, r.plan))

    def group_contracted(self, payer, tin) -> bool | None:
        """Is the billing TIN in-network with this payer under ANY NPI (group-level)? True on positive
        evidence (some NPI at this (payer, TIN) is in-network); None otherwise — an all-OON set doesn't
        prove the group is out (we may only hold the OON physicians)."""
        pl, t = (payer or "").lower(), _norm_tin(tin)
        if any(r.payer.lower() == pl and _norm_tin(r.tin) == t and r.in_network for r in self._items):
            return True
        return None

    def __bool__(self) -> bool:
        return bool(self._items)


_DEFAULT: CredentialingMatrix | None = None


def default_credentialing() -> CredentialingMatrix:
    """In-code verified seed, with any CREDENTIALING_PATH clinic export layered on top."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CredentialingMatrix()
    return _DEFAULT
