import pytest

from network_probe.auth.passwords import DUMMY_HASH, check_policy, hash_password, verify_password


def test_hash_verify():
    h = hash_password("Sup3r-secret!-pw")
    assert h != "Sup3r-secret!-pw"
    assert verify_password("Sup3r-secret!-pw", h)
    assert not verify_password("wrong", h)

def test_policy_rejects_weak():
    with pytest.raises(ValueError):
        check_policy("short")
    check_policy("Str0ng-enough-pw")   # >=12, no raise

def test_dummy_hash_is_real_bcrypt_and_never_matches():
    assert DUMMY_HASH.startswith("$2")            # bcrypt format
    assert verify_password("anything", DUMMY_HASH) is False
    assert verify_password("not-a-real-password-constant-time-guard", DUMMY_HASH) is True

def test_long_password_handled():
    pw = "A1!" + "x"*100  # >72 bytes
    h = hash_password(pw)
    assert verify_password(pw, h) is True
    assert verify_password("A1!" + "y"*100, h) is False
