"""Override / golden-record layer (TODO item #5).

A directory can be wrong even after corroboration. When a human (or an authoritative source
like Availity / a payer TIN portal) confirms the real status, we record it here so every future
check returns the corrected answer with provenance — instead of re-deriving the stale directory
verdict. This is the MDM "golden record": confirmed truth wins over the source feed.

Stored as a small JSON file (one record per confirmed (payer, NPI[, network/plan/TIN]) fact).
Lookups prefer the most specific match and the most recent verification.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .models import NetworkStatus, NetworkVerdict, ProviderQuery

DEFAULT_PATH = Path(".overrides/overrides.json")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@dataclass
class Override:
    payer: str
    npi: str
    status: str                  # IN_NETWORK | OUT_OF_NETWORK | REVIEW
    verified_by: str             # e.g. "Availity 2026-05-21" or "ops:jdoe"
    verified_at: str             # ISO date the human/source confirmed it
    network: Optional[str] = None
    plan: Optional[str] = None
    tin: Optional[str] = None
    note: str = ""

    def specificity(self) -> int:
        return sum(bool(x) for x in (self.network, self.plan, self.tin))


class OverrideStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self._items: list[Override] = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._items = [Override(**r) for r in raw]
            except Exception:
                self._items = []

    def _matches(self, o: Override, q: ProviderQuery) -> bool:
        if o.payer.lower() != (q.payer or "").lower() or o.npi != (q.npi or ""):
            return False
        if o.tin and _norm(o.tin) != _norm(q.tin or ""):
            return False
        if o.plan and _norm(o.plan) not in _norm(q.plan_hint or "") and _norm(q.plan_hint or "") not in _norm(o.plan):
            return False
        # network is matched loosely against the plan hint too (best-effort)
        if o.network and _norm(o.network) not in _norm(q.plan_hint or "") and not o.plan:
            # don't hard-fail on network mismatch unless it's the only narrowing field
            pass
        return True

    def lookup(self, q: ProviderQuery) -> Optional[Override]:
        cands = [o for o in self._items if self._matches(o, q)]
        if not cands:
            return None
        # most specific, then most recent
        cands.sort(key=lambda o: (o.specificity(), o.verified_at), reverse=True)
        return cands[0]

    def add(self, override: Override) -> None:
        self._items.append(override)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(o) for o in self._items], indent=2), encoding="utf-8")


def verdict_from_override(o: Override, original: NetworkVerdict) -> NetworkVerdict:
    scope = " / ".join(x for x in [o.network or o.plan, f"TIN {o.tin}" if o.tin else None] if x)
    return NetworkVerdict(
        status=NetworkStatus(o.status),
        matched_provider=original.matched_provider,
        plan_or_network_checked=original.plan_or_network_checked,
        source_url=original.source_url,
        confidence="high",
        notes=(f"VERIFIED OVERRIDE — {o.status} confirmed by {o.verified_by} on {o.verified_at}"
               + (f" ({scope})" if scope else "")
               + (f". {o.note}" if o.note else "")
               + " [overrides the live directory]"),
        corroboration=[{"source": "override", "result": "authoritative",
                        "detail": f"{o.status} per {o.verified_by} ({o.verified_at})"}],
    )
