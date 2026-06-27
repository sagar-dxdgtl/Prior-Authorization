from __future__ import annotations
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from .models import OverrideRow, EligibilityCheck

class OverrideRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id
    def add(self, **kw) -> OverrideRow:
        row = OverrideRow(tenant_id=self.tid, **kw)   # tenant_id from context, never from kw
        self.s.add(row); self.s.flush(); return row
    def lookup(self, payer: str, npi: str) -> Optional[OverrideRow]:
        return (self.s.query(OverrideRow)
                .filter(OverrideRow.payer == payer, OverrideRow.npi == npi)   # RLS adds tenant filter
                .order_by(OverrideRow.verified_at.desc()).first())

class EligibilityCheckRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id
    def record(self, **kw) -> EligibilityCheck:
        row = EligibilityCheck(tenant_id=self.tid, **kw)
        self.s.add(row); self.s.flush(); return row
