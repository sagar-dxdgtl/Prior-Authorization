from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from network_probe.auth.deps import require_role
from network_probe.core.context import RequestContext
from network_probe.db.repo import ReviewCaseRepo, ReviewNoteRepo
from network_probe.db.session import tenant_session

router = APIRouter(prefix="/api/review", tags=["review"])


class NoteBody(BaseModel):
    text: str


class ResolveBody(BaseModel):
    resolution: str


def _case_dict(c) -> dict:
    return {
        "id": str(c.id),
        "status": c.status,
        "payer_key": c.payer_key,
        "npi": c.npi,
        "member_id_hash": c.member_id_hash,  # keyed hash only — never plaintext PHI
        "assignee_id": str(c.assignee_id) if c.assignee_id else None,
        "eligibility_check_id": str(c.eligibility_check_id) if c.eligibility_check_id else None,
        "resolution": c.resolution,
        "created_at": c.created_at.isoformat(),
    }


def _note_dict(n) -> dict:
    return {
        "id": str(n.id),
        "author_id": str(n.author_id),
        "text": n.text,
        "created_at": n.created_at.isoformat(),
    }


@router.get("/cases")
def list_cases(status: str | None = None, ctx: RequestContext = Depends(require_role("reviewer", "admin"))):
    with tenant_session(ctx.tenant_id) as s:
        return [_case_dict(c) for c in ReviewCaseRepo(s, ctx.tenant_id).list(status=status)]


@router.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, ctx: RequestContext = Depends(require_role("reviewer", "admin"))):
    with tenant_session(ctx.tenant_id) as s:
        case = ReviewCaseRepo(s, ctx.tenant_id).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail={"message": "case not found"})
        notes = ReviewNoteRepo(s, ctx.tenant_id).list_for(case_id)
        return {**_case_dict(case), "notes": [_note_dict(n) for n in notes]}


@router.post("/cases/{case_id}/claim")
def claim_case(case_id: uuid.UUID, ctx: RequestContext = Depends(require_role("reviewer", "admin"))):
    with tenant_session(ctx.tenant_id) as s:
        case = ReviewCaseRepo(s, ctx.tenant_id).update(
            case_id, assignee_id=ctx.actor_id, status="in_review"
        )
        if case is None:
            raise HTTPException(status_code=404, detail={"message": "case not found"})
        return _case_dict(case)


@router.post("/cases/{case_id}/notes")
def add_note(case_id: uuid.UUID, body: NoteBody, ctx: RequestContext = Depends(require_role("reviewer", "admin"))):
    with tenant_session(ctx.tenant_id) as s:
        case = ReviewCaseRepo(s, ctx.tenant_id).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail={"message": "case not found"})
        note = ReviewNoteRepo(s, ctx.tenant_id).add(case_id=case_id, author_id=ctx.actor_id, text=body.text)
        return _note_dict(note)


@router.post("/cases/{case_id}/resolve")
def resolve_case(
    case_id: uuid.UUID, body: ResolveBody, ctx: RequestContext = Depends(require_role("reviewer", "admin"))
):
    with tenant_session(ctx.tenant_id) as s:
        case = ReviewCaseRepo(s, ctx.tenant_id).update(case_id, status="resolved", resolution=body.resolution)
        if case is None:
            raise HTTPException(status_code=404, detail={"message": "case not found"})
        return _case_dict(case)
