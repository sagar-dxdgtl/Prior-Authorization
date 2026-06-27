from __future__ import annotations
import uuid
from contextlib import contextmanager
from sqlalchemy import text
from .base import SessionLocal, app_engine

@contextmanager
def tenant_session(tenant_id):
    """Open ONE transaction as the RLS-bound app role, set app.tenant_id LOCAL to it, yield the session.

    The tenant id is coerced to a valid UUID (never '' — `''::uuid` would raise in every RLS
    policy) and passed as a BOUND PARAM (anti-SQLi). `set_config(..., true)` scopes the GUC to this
    transaction, so a pooled connection can't carry tenant A's id into tenant B's request (TOCTOU).
    """
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    session = SessionLocal(bind=app_engine())
    try:
        # first execute autobegins the transaction; the GUC is local to it.
        session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tid)})
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
