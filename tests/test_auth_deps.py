import uuid, pytest
from fastapi import HTTPException
from network_probe.auth import jwt_tokens as jt
from network_probe.auth.deps import context_from_token

class FakeUser:
    def __init__(self, tv=0, mcp=False):
        self.id = uuid.uuid4(); self.tenant_id = uuid.uuid4(); self.role = "user"
        self.token_version = tv; self.must_change_password = mcp

def test_stale_tv_rejected(monkeypatch):
    u = FakeUser(tv=5)
    tok, _ = jt.issue_access(u.id, u.tenant_id, "user", 4)   # stale tv (4 != 5)
    monkeypatch.setattr("network_probe.auth.deps._load_user", lambda i, t: FakeUser(tv=5))
    with pytest.raises(HTTPException) as e:
        context_from_token(tok)
    assert e.value.status_code == 401

def test_must_change_password_blocks_data_routes(monkeypatch):
    u = FakeUser(tv=0, mcp=True)
    tok, _ = jt.issue_access(u.id, u.tenant_id, "user", 0)
    monkeypatch.setattr("network_probe.auth.deps._load_user", lambda i, t: u)
    with pytest.raises(HTTPException) as e:
        context_from_token(tok, allow_password_change=False)
    assert e.value.status_code == 403
    # but the change-password path is allowed:
    assert context_from_token(tok, allow_password_change=True).role == "user"

def test_unknown_user_rejected(monkeypatch):
    u = FakeUser()
    tok, _ = jt.issue_access(u.id, u.tenant_id, "user", 0)
    monkeypatch.setattr("network_probe.auth.deps._load_user", lambda i, t: None)
    with pytest.raises(HTTPException) as e:
        context_from_token(tok)
    assert e.value.status_code == 401
