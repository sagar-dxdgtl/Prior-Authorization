"""Durable provider-network fact store (the `provider_network_facts` table).

Persists credentialing / TiC / directory facts (payer, NPI, billing TIN → in/out) so a TiC pull
(e.g. the Oscar FL ingest) is reusable instead of thrown away, and so the group-contracted
(physician-OON vs payer-OON) check has a source beyond the in-code seeds. Global rows are written
with tenant_id NULL (visible to every tenant, like `payers`). No PHI — contract facts only.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from network_probe.db.base import SessionLocal, app_engine
from network_probe.db.models import ProviderNetworkFact


def _norm(t) -> str:
    return re.sub(r"[^0-9]", "", str(t or ""))


class ProviderNetworkStore:
    def __init__(self, engine=None):
        self.engine = engine or app_engine()

    def group_contracted(self, payer_key, tin) -> bool | None:
        """True if any persisted in-network fact exists for (payer, billing TIN); else None."""
        t = _norm(tin)
        if not (payer_key and t):
            return None
        with SessionLocal(bind=self.engine) as s:
            hit = s.execute(
                select(ProviderNetworkFact.id).where(
                    ProviderNetworkFact.payer_key == payer_key,
                    ProviderNetworkFact.tin == t,
                    ProviderNetworkFact.in_network.is_(True),
                ).limit(1)
            ).first()
        return True if hit else None

    def facts_for(self, payer_key, npi=None, tin=None) -> list[ProviderNetworkFact]:
        conds = [ProviderNetworkFact.payer_key == payer_key]
        if npi:
            conds.append(ProviderNetworkFact.npi == str(npi).strip())
        if tin:
            conds.append(ProviderNetworkFact.tin == _norm(tin))
        with SessionLocal(bind=self.engine) as s:
            return list(s.execute(select(ProviderNetworkFact).where(*conds)).scalars().all())

    def upsert(self, rows: list[dict], tenant_id=None) -> int:
        """Insert facts idempotently. Each row: {payer_key, tin, in_network, npi?, source?, plan?,
        network_name?, effective_date?}. Skips rows already present by (payer_key, npi, tin, source)
        — so re-running an ingest is a no-op. Returns the number of NEW facts written."""
        wanted = [
            r for r in rows
            if r.get("payer_key") and r.get("tin") and r.get("in_network") is not None
        ]
        if not wanted:
            return 0
        combos = {(r["payer_key"], r.get("source", "tic")) for r in wanted}
        n = 0
        with SessionLocal(bind=self.engine) as s:
            existing: set = set()
            for pk, src in combos:
                for f in s.execute(
                    select(ProviderNetworkFact.payer_key, ProviderNetworkFact.npi, ProviderNetworkFact.tin,
                           ProviderNetworkFact.source).where(
                        ProviderNetworkFact.payer_key == pk, ProviderNetworkFact.source == src)
                ).all():
                    existing.add((f.payer_key, f.npi, f.tin, f.source))
            for r in wanted:
                npi = str(r["npi"]).strip() if r.get("npi") else None
                tin = _norm(r["tin"])
                src = r.get("source", "tic")
                key = (r["payer_key"], npi, tin, src)
                if key in existing:
                    continue
                s.add(ProviderNetworkFact(
                    tenant_id=tenant_id, payer_key=r["payer_key"], npi=npi, tin=tin,
                    in_network=bool(r["in_network"]), source=src, plan=r.get("plan"),
                    network_name=r.get("network_name"), effective_date=r.get("effective_date"),
                ))
                existing.add(key)
                n += 1
            s.commit()
        return n


_DEFAULT: ProviderNetworkStore | None = None


def default_provider_network_store() -> ProviderNetworkStore:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ProviderNetworkStore()
    return _DEFAULT
