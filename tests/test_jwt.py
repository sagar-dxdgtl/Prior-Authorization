import uuid, pytest
from network_probe.auth import jwt_tokens as jt

def _claims():
    return dict(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="user", token_version=3)

def test_access_roundtrip():
    c = _claims()
    tok, exp = jt.issue_access(**c)
    d = jt.decode_token(tok, expected_typ="access")
    assert d["sub"] == str(c["user_id"]) and d["tid"] == str(c["tenant_id"])
    assert d["tv"] == 3 and exp == 1800

def test_refresh_not_accepted_as_access():
    c = _claims()
    rtok = jt.issue_refresh(**c)
    with pytest.raises(jt.TokenError):
        jt.decode_token(rtok, expected_typ="access")
    # but valid as refresh:
    assert jt.decode_token(rtok, expected_typ="refresh")["typ"] == "refresh"

def test_tampered_token_rejected():
    c = _claims()
    tok, _ = jt.issue_access(**c)
    with pytest.raises(jt.TokenError):
        jt.decode_token(tok + "x", expected_typ="access")

def test_alg_none_rejected():
    # an attacker-forged unsigned token must be rejected (alg pinned to HS256)
    import jwt as pyjwt
    c = _claims()
    forged = pyjwt.encode({"sub": str(c["user_id"]), "tid": str(c["tenant_id"]),
                           "typ": "access", "tv": 3, "iat": 0, "exp": 9999999999},
                          key="", algorithm="none")
    with pytest.raises(jt.TokenError):
        jt.decode_token(forged, expected_typ="access")
