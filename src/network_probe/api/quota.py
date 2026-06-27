"""DB-backed per-tenant daily/monthly quota enforcement.

`enforce_quota` is a FastAPI dependency that ALSO authenticates (via get_context, so the
must_change_password 403 gate and 401s still apply). Each metered request atomically bumps a
per-tenant daily AND monthly counter in Postgres (RLS-isolated); over the limit -> 429. The real
counts are stashed on request.state.quota so the rate-limit middleware can emit them as headers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text

from network_probe.auth.deps import get_context
from network_probe.core.config import get_settings
from network_probe.core.context import RequestContext
from network_probe.db.session import tenant_session


def _quota_limits() -> tuple[int, int]:
    """Indirection so tests can monkeypatch the active (daily, monthly) limits."""
    s = get_settings()
    return s.quota_daily, s.quota_monthly


def _bump(tenant_id, period_type: str, period_key: str) -> int:
    """Atomic per-tenant upsert; returns the new count. Runs as the RLS-bound app role."""
    with tenant_session(tenant_id) as s:
        return s.execute(
            text(
                "INSERT INTO usage_counters (id, tenant_id, period_type, period_key, count) "
                "VALUES (gen_random_uuid(), :tid, :pt, :pk, 1) "
                "ON CONFLICT (tenant_id, period_type, period_key) "
                "DO UPDATE SET count = usage_counters.count + 1 "
                "RETURNING count"
            ),
            {"tid": str(tenant_id), "pt": period_type, "pk": period_key},
        ).scalar()


def enforce_quota(request: Request, ctx: RequestContext = Depends(get_context)) -> RequestContext:
    now = datetime.now(UTC)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    d = _bump(ctx.tenant_id, "day", day)
    m = _bump(ctx.tenant_id, "month", month)
    daily, monthly = _quota_limits()
    request.state.quota = {
        "daily_limit": daily,
        "daily_used": d,
        "daily_remaining": max(0, daily - d),
        "monthly_limit": monthly,
        "monthly_used": m,
        "monthly_remaining": max(0, monthly - m),
    }
    if d > daily or m > monthly:
        raise HTTPException(status_code=429, detail={"message": "quota exceeded"})
    return ctx
