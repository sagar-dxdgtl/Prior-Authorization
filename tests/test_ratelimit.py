from fastapi import FastAPI
from fastapi.testclient import TestClient

from network_probe.ratelimit import RateLimitHeadersMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(RateLimitHeadersMiddleware)
    @app.get("/ping")
    def ping(): return {"ok": True}
    return TestClient(app)

def test_quota_headers_present_and_increment():
    c = _client()
    r1 = c.get("/ping")
    assert r1.headers["x-ratelimit-limit"] == "120"
    assert r1.headers["x-quota-daily-limit"] == "1000"
    assert r1.headers["x-quota-monthly-limit"] == "20000"
    u1 = int(r1.headers["x-quota-daily-used"])
    u2 = int(c.get("/ping").headers["x-quota-daily-used"])
    assert u2 == u1 + 1   # per-request increment

def test_bad_token_does_not_crash():
    c = _client()
    r = c.get("/ping", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 200 and "x-quota-daily-used" in r.headers
