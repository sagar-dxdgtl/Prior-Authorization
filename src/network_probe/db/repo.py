from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from network_probe.db.models import EligibilityCheck, OverrideRow, ReviewCase, ReviewNote


class OverrideRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id

    def add(self, **kw) -> OverrideRow:
        row = OverrideRow(tenant_id=self.tid, **kw)  # tenant_id from context, never from kw
        self.s.add(row)
        self.s.flush()
        return row

    def lookup(self, payer: str, npi: str) -> OverrideRow | None:
        return (
            self.s.query(OverrideRow)
            .filter(OverrideRow.payer == payer, OverrideRow.npi == npi)  # RLS adds tenant filter
            .order_by(OverrideRow.verified_at.desc())
            .first()
        )


class EligibilityCheckRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id

    def record(self, **kw) -> EligibilityCheck:
        row = EligibilityCheck(tenant_id=self.tid, **kw)
        self.s.add(row)
        self.s.flush()
        return row


class ReviewCaseRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id

    def add(self, **kw) -> ReviewCase:
        row = ReviewCase(tenant_id=self.tid, **kw)  # tenant_id from context, never from kw
        self.s.add(row)
        self.s.flush()
        return row

    def get(self, case_id) -> ReviewCase | None:
        return self.s.query(ReviewCase).filter(ReviewCase.id == case_id).first()  # RLS adds tenant filter

    def list(self, status: str | None = None) -> list[ReviewCase]:
        q = self.s.query(ReviewCase)
        if status:
            q = q.filter(ReviewCase.status == status)
        return q.order_by(ReviewCase.created_at.desc()).all()

    def update(self, case_id, **fields) -> ReviewCase | None:
        row = self.get(case_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        self.s.flush()
        return row


class ReviewNoteRepo:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.s, self.tid = session, tenant_id

    def add(self, **kw) -> ReviewNote:
        row = ReviewNote(tenant_id=self.tid, **kw)  # tenant_id from context, never from kw
        self.s.add(row)
        self.s.flush()
        return row

    def list_for(self, case_id) -> list[ReviewNote]:
        return (
            self.s.query(ReviewNote)
            .filter(ReviewNote.case_id == case_id)  # RLS adds tenant filter
            .order_by(ReviewNote.created_at)
            .all()
        )
