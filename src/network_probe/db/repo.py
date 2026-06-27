from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from network_probe.db.models import EligibilityCheck, OverrideRow


class OverrideRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id
    def add(self, **kw) -> OverrideRow:
        row = OverrideRow(tenant_id=self.tid, **kw)   # tenant_id from context, never from kw
        self.s.add(row); self.s.flush(); return row
    def lookup(self, payer: str, npi: str) -> OverrideRow | None:
        return (self.s.query(OverrideRow)
                .filter(OverrideRow.payer == payer, OverrideRow.npi == npi)   # RLS adds tenant filter
                .order_by(OverrideRow.verified_at.desc()).first())

class EligibilityCheckRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id
    def record(self, **kw) -> EligibilityCheck:
        row = EligibilityCheck(tenant_id=self.tid, **kw)
        self.s.add(row); self.s.flush(); return row
