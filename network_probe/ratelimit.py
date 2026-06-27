from __future__ import annotations
import threading
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from .auth.jwt_tokens import decode_token, TokenError

# Slice A: in-memory, per-process, advisory. Real (persistent, distributed, enforced) limits = Slice B.
RATE_LIMIT_PER_MIN = 120
DAILY_QUOTA = 1000
MONTHLY_QUOTA = 20000

class _Counters:
    def __init__(self):
        self._lock = threading.Lock()
        self._daily = defaultdict(int)
        self._monthly = defaultdict(int)
    def bump(self, tenant: str) -> tuple[int, int]:
        with self._lock:
            self._daily[tenant] += 1
            self._monthly[tenant] += 1
            return self._daily[tenant], self._monthly[tenant]

_counters = _Counters()

def _tenant_of(request) -> str:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            return decode_token(auth.split(" ", 1)[1].strip(), expected_typ="access").get("tid", "anon")
        except TokenError:
            return "anon"
        except Exception:
            return "anon"
    return "anon"

class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        tenant = _tenant_of(request)
        daily, monthly = _counters.bump(tenant)
        response = await call_next(request)
        h = response.headers
        h["x-ratelimit-limit"] = str(RATE_LIMIT_PER_MIN)
        h["x-ratelimit-remaining"] = str(RATE_LIMIT_PER_MIN)   # advisory; per-minute enforcement is Slice B
        h["x-quota-daily-limit"] = str(DAILY_QUOTA)
        h["x-quota-daily-used"] = str(daily)
        h["x-quota-daily-remaining"] = str(max(0, DAILY_QUOTA - daily))
        h["x-quota-monthly-limit"] = str(MONTHLY_QUOTA)
        h["x-quota-monthly-used"] = str(monthly)
        h["x-quota-monthly-remaining"] = str(max(0, MONTHLY_QUOTA - monthly))
        return response
