from cryptography.fernet import Fernet

from network_probe.core.crypto import FernetCrypto, hash_member_id


def test_encrypt_roundtrip():
    c = FernetCrypto([Fernet.generate_key().decode()])
    tok = c.encrypt("MEMBER123")
    assert tok != "MEMBER123"
    assert c.decrypt(tok) == "MEMBER123"

def test_key_rotation_decrypts_old():
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old = FernetCrypto([k1])
    tok = old.encrypt("x")
    new = FernetCrypto([k2, k1])   # new primary, old still accepted
    assert new.decrypt(tok) == "x"
    assert new.encrypt("y") != tok

def test_hash_is_stable_and_peppered():
    a = hash_member_id("ABC123", pepper="p1")
    assert a == hash_member_id("abc123", pepper="p1")   # case/space-normalized
    assert a != hash_member_id("ABC123", pepper="p2")   # pepper changes digest
    assert len(a) == 64

def test_empty_keys_rejected():
    import pytest
    with pytest.raises(ValueError):
        FernetCrypto([])
