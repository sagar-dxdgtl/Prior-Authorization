"""Verified TIN-level network status (payer TIN portal / Availity TIN check).

Payer *directories* almost never expose per-TIN status, but a payer's own TIN-level
network-status portal does — e.g. Cigna's "Select a TIN and provider/group to verify the network
status for this patient" screen returns an explicit in/out answer for a specific (provider, TIN).
When we have such a confirmed `(payer, NPI, billing TIN) -> IN/OON` fact, we record it here so
`TinScopeSource` can report a *real* group-level result instead of "no per-TIN data".

This is the same golden-record idea as `overrides.py`, but scoped to the TIN signal rather than
the whole verdict. Seeded from verified payer-portal screenshots (see
`test-data/P Verify OON examples …pdf`); extend at runtime via a JSON file pointed to by
`TIN_STATUS_PATH`:

    [{"payer","npi","tin","status","group","source","verified_at"}]

There is no free real-time NPI→TIN API, so absent a record here (or a loaded crosswalk / Oscar's
embedded TINs) the honest answer remains "needs integration".
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _norm(t) -> str:
    return re.sub(r"[^0-9]", "", str(t or ""))


@dataclass
class TinStatus:
    payer: str
    npi: str
    tin: str
    status: str                  # IN_NETWORK | OUT_OF_NETWORK
    group: Optional[str] = None  # billing group / org name shown by the portal
    source: Optional[str] = None
    verified_at: Optional[str] = None


# Verified from Cigna's Network Status portal (pVerify OON examples PDF, p.3):
#   TIN 463812940 / WAZNI PLLC · KIANG WILLIAM (NPI 1184610453) -> "You are Out-Of-Network for
#   this patient." (Cigna HMO FL, Plan ID 3346355)
#
# Verified from the UnitedHealthcare Transparency-in-Coverage MRF (TX exchange slice, 2026-06):
#   group NPI 1972941318 / TIN 412049581 (Srinivas Rao MD PA dba Texas Vein & Wellness Institute,
#   TX-Houston) is NOT present in the network file (0 occurrences) -> out-of-network. This is the
#   OON counterpart to the in-network Texas UVC Dallas TIN 933510922 (see tin_crosswalk.py seed).
_SEED = [
    TinStatus(payer="cigna-fhir", npi="1184610453", tin="463812940", status="OUT_OF_NETWORK",
              group="Wazni PLLC", source="Cigna Network Status portal", verified_at="2026-05-28"),
    TinStatus(payer="uhc", npi="1972941318", tin="412049581", status="OUT_OF_NETWORK",
              group="Srinivas Rao MD PA dba Texas Vein & Wellness Institute",
              source="UHC Transparency-in-Coverage MRF (TX exchange) — not present (0 occurrences)",
              verified_at="2026-06"),
]


class TinStatusBook:
    def __init__(self, records: Optional[list] = None, path: Optional[str] = None):
        self._items: list[TinStatus] = list(records) if records is not None else list(_SEED)
        p = path or os.environ.get("TIN_STATUS_PATH")
        if p and Path(p).exists():
            for r in json.loads(Path(p).read_text(encoding="utf-8")):
                self._items.append(TinStatus(**r))

    def lookup(self, payer, npi, tin) -> Optional[TinStatus]:
        for s in self._items:
            if (s.payer.lower() == (payer or "").lower()
                    and s.npi == (npi or "")
                    and _norm(s.tin) == _norm(tin)):
                return s
        return None

    def __bool__(self) -> bool:
        return bool(self._items)


_DEFAULT: Optional[TinStatusBook] = None


def default_tin_status() -> TinStatusBook:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = TinStatusBook()
    return _DEFAULT
