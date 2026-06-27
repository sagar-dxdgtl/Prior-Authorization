import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from network_probe.auth.deps import get_context
from network_probe.auth.routes import router


def _app():
    app = FastAPI()

    @app.exception_handler(HTTPException)
    def _h(request, exc):
        d = exc.detail
        return JSONResponse(status_code=exc.status_code, content=d if isinstance(d, dict) else {"message": str(d)})

    app.include_router(router)

    @app.get("/_protected")
    def protected(ctx=Depends(get_context)):
        return {"tenant": str(ctx.tenant_id)}

    return app


@pytest.mark.db
def test_first_login_then_change_password(seed_admin):
    c = TestClient(_app())
    r = c.post("/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Initial-pw-1234"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    acc = r.json()["tokens"]["access"]
    assert c.get("/_protected", headers={"Authorization": f"Bearer {acc}"}).status_code == 403
    r2 = c.post(
        "/api/auth/change-password/",
        headers={"Authorization": f"Bearer {acc}"},
        json={
            "current_password": "Initial-pw-1234",
            "new_password": "Brand-new-pw-456",
            "confirm_password": "Brand-new-pw-456",
        },
    )
    assert r2.status_code == 200 and r2.json()["success"] is True
    body = c.post(
        "/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Brand-new-pw-456"}
    ).json()
    assert body["access_token"]
    assert c.get("/_protected", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200


@pytest.mark.db
def test_bad_password_generic_no_user_leak(seed_admin):
    c = TestClient(_app())
    r = c.post("/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "nope"})
    assert r.status_code == 401 and "username" not in r.json()["message"].lower()
    assert (
        c.post("/api/auth/login", data={"grant_type": "password", "username": "ghost", "password": "nope"}).status_code
        == 401
    )


@pytest.mark.db
def test_lockout_after_threshold(seed_admin):
    c = TestClient(_app())
    for _ in range(5):
        assert (
            c.post(
                "/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "wrong"}
            ).status_code
            == 401
        )
    r = c.post("/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Initial-pw-1234"})
    assert r.status_code == 429


@pytest.mark.db
def test_refresh_issues_new_access(seed_admin):
    c = TestClient(_app())
    acc = c.post(
        "/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Initial-pw-1234"}
    ).json()["tokens"]["access"]
    c.post(
        "/api/auth/change-password/",
        headers={"Authorization": f"Bearer {acc}"},
        json={
            "current_password": "Initial-pw-1234",
            "new_password": "Brand-new-pw-456",
            "confirm_password": "Brand-new-pw-456",
        },
    )
    body = c.post(
        "/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Brand-new-pw-456"}
    ).json()
    r = c.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200 and r.json()["access_token"] and r.json()["expires_in"] == 1800


@pytest.mark.db
def test_refresh_rejects_access_token(seed_admin):
    c = TestClient(_app())
    acc = c.post(
        "/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Initial-pw-1234"}
    ).json()["tokens"]["access"]
    # passing an ACCESS token where a refresh token is required must be rejected
    assert c.post("/api/auth/refresh", json={"refresh_token": acc}).status_code == 401


@pytest.mark.db
def test_pre_change_token_rejected_after_change(seed_admin):
    c = TestClient(_app())
    acc = c.post(
        "/api/auth/login", data={"grant_type": "password", "username": "admin", "password": "Initial-pw-1234"}
    ).json()["tokens"]["access"]
    c.post(
        "/api/auth/change-password/",
        headers={"Authorization": f"Bearer {acc}"},
        json={
            "current_password": "Initial-pw-1234",
            "new_password": "Brand-new-pw-456",
            "confirm_password": "Brand-new-pw-456",
        },
    )
    # the OLD access token is now invalid (token_version bumped) → 401 even on the change-password route
    r = c.post(
        "/api/auth/change-password/",
        headers={"Authorization": f"Bearer {acc}"},
        json={"current_password": "whatever", "new_password": "y" * 12, "confirm_password": "y" * 12},
    )
    assert r.status_code == 401
