from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import select

from network_probe.db.base import SessionLocal, app_engine
from network_probe.db.models import Payer


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


# existing adapter keys -> canonical roster-label slug
ADAPTER_ALIASES = {
    "oscar": "oscar",
    "devoted": "devoted-health",
    "humana-fhir": "humana",
    "cigna-fhir": "cigna-healthcare",
    "uhc": "unitedhealthcare",
}


class PayerCatalogue(Protocol):
    def resolve(self, payer_key: str) -> Payer | None: ...


class DbPayerCatalogue:
    """Resolves a payer identifier (adapter key, roster key, or label) to a global Payer row.
    Reads global rows (tenant_id IS NULL) as the app role with no tenant context — the payers RLS
    policy permits that. Returns the first match (stedi_payer_id is consistent per payer)."""

    def resolve(self, payer_key: str) -> Payer | None:
        if not payer_key:
            return None
        want = ADAPTER_ALIASES.get(payer_key.lower().strip(), _slug(payer_key))
        with SessionLocal(bind=app_engine()) as s:
            rows = s.execute(select(Payer)).scalars().all()
            for p in rows:
                if _slug(p.key) == want:
                    return p
            for p in rows:
                if _slug(p.label) == want:
                    return p
        return None
