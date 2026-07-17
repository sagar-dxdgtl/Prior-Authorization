"""Line-of-business classifier = the Transparency-in-Coverage eligibility gate.

The federal TiC MRF mandate applies to **commercial** group/individual coverage only. Medicare
(Advantage and FFS), Medicaid, Dual (D-SNP), TRICARE and VA are all exempt — no MRF exists for
them — so TiC must never be consulted for those lines (it would return a guaranteed blank on a
live check). This module decides, from the member's real plan name (the 271's plan) and/or the
payer row's benefit_type, whether a line is commercial (TiC-eligible) or not.

Signals, in precedence order:
  1. A non-commercial marker in EITHER the plan text or the benefit_type wins — a stale
     "Commercial" tag can't fire TiC on an "AARP Medicare Advantage" plan.
  2. Otherwise a commercial marker (Commercial/ACA/exchange, or a bare PPO/HMO/POS product name)
     → commercial.
  3. No signal either way → "unknown" (treated as NOT commercial: we never invite TiC on a guess).
"""

from __future__ import annotations

import re

# non-commercial line markers, most specific first (dual is a subset of both Medicare and Medicaid)
_DUAL = re.compile(r"\bdual\b|d-?snp|\bfide\b", re.I)
_MEDICAID = re.compile(r"medicaid|ahcccs|medi-?cal|\bchip\b", re.I)
_MEDICARE = re.compile(
    r"medicare|advantage|\bmapd\b|\bma-?pd\b|\bpdp\b|\bpart [abcd]\b|\bh\d{4}\b|\br\d{4}\b|\bs\d{4}\b",
    re.I,
)
_FEDERAL = re.compile(r"tricare|champva|\bva\b|veteran", re.I)
# commercial markers — checked only when NO non-commercial marker is present anywhere
_COMMERCIAL = re.compile(r"commercial|\baca\b|exchange|marketplace|\bppo\b|\bhmo\b|\bepo\b|\bpos\b|open access", re.I)


def _noncommercial_lob(text: str | None) -> str | None:
    t = text or ""
    if not t.strip():
        return None
    if _DUAL.search(t):
        return "dual"
    if _MEDICAID.search(t):
        return "medicaid"
    if _MEDICARE.search(t):
        return "medicare"
    if _FEDERAL.search(t):
        return "federal"
    return None


def line_of_business(plan: str | None, benefit_type: str | None) -> str:
    """Return one of: commercial | medicare | medicaid | dual | federal | unknown.

    Only "commercial" is TiC-eligible. `plan` is the member's real plan name (271); `benefit_type`
    is the payer row's coarse class. Either may be None.
    """
    found = {x for x in (_noncommercial_lob(plan), _noncommercial_lob(benefit_type)) if x}
    for lob in ("dual", "medicaid", "medicare", "federal"):
        if lob in found:
            return lob
    if _COMMERCIAL.search(plan or "") or _COMMERCIAL.search(benefit_type or ""):
        return "commercial"
    return "unknown"


def is_commercial(plan: str | None, benefit_type: str | None) -> bool:
    """True only for commercial/ACA lines — the ones subject to the TiC MRF mandate."""
    return line_of_business(plan, benefit_type) == "commercial"
