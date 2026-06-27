"""Tests for KMS-unwrap of Fernet keys and injectable AwsSecrets client.

All tests are PURE — no real AWS, no database, no network.
Marker: default (no db/live markers needed — these run in "not live and not db").
"""
from __future__ import annotations


def test_resolve_fernet_keys_passthrough_when_off():
    from cryptography.fernet import Fernet

    from network_probe.core.crypto import resolve_fernet_keys

    k = Fernet.generate_key().decode()
    assert resolve_fernet_keys([k], kms_enabled=False) == [k]


def test_resolve_fernet_keys_passthrough_empty():
    """Empty key list is a no-op regardless of kms_enabled."""
    from network_probe.core.crypto import resolve_fernet_keys

    assert resolve_fernet_keys([], kms_enabled=True, kms_client=object()) == []


def test_resolve_fernet_keys_kms_unwraps_and_roundtrips():
    import base64

    from cryptography.fernet import Fernet

    from network_probe.core.crypto import FernetCrypto, resolve_fernet_keys

    real = Fernet.generate_key().decode()
    wrapped = base64.b64encode(b"CIPHERTEXT").decode()

    class FakeKms:
        def decrypt(self, CiphertextBlob):
            assert CiphertextBlob == b"CIPHERTEXT"
            return {"Plaintext": real.encode()}

    keys = resolve_fernet_keys([wrapped], kms_enabled=True, kms_client=FakeKms())
    assert keys == [real]
    c = FernetCrypto(keys)
    assert c.decrypt(c.encrypt("phi")) == "phi"  # unwrapped key actually works


def test_resolve_fernet_keys_kms_plaintext_bytes():
    """KMS may return Plaintext as bytes or bytearray — both must work."""
    import base64

    from cryptography.fernet import Fernet

    from network_probe.core.crypto import resolve_fernet_keys

    real = Fernet.generate_key().decode()
    wrapped = base64.b64encode(b"BLOB").decode()

    class FakeKmsBytearray:
        def decrypt(self, CiphertextBlob):
            return {"Plaintext": bytearray(real.encode())}

    keys = resolve_fernet_keys([wrapped], kms_enabled=True, kms_client=FakeKmsBytearray())
    assert keys == [real]


def test_aws_secrets_with_fake_client():
    from network_probe.core.secrets_provider import AwsSecrets

    class FakeSM:
        def get_secret_value(self, SecretId):
            assert SecretId == "preauth/STEDI_API_KEY"
            return {"SecretString": "live-key"}

    assert AwsSecrets(client=FakeSM()).get_secret("STEDI_API_KEY") == "live-key"


def test_aws_secrets_returns_none_on_error():
    from network_probe.core.secrets_provider import AwsSecrets

    class Boom:
        def get_secret_value(self, SecretId):
            raise RuntimeError("nope")

    assert AwsSecrets(client=Boom()).get_secret("X") is None


def test_aws_secrets_custom_prefix():
    from network_probe.core.secrets_provider import AwsSecrets

    class FakeSM:
        def get_secret_value(self, SecretId):
            assert SecretId == "myapp/MY_KEY"
            return {"SecretString": "val"}

    assert AwsSecrets(prefix="myapp/", client=FakeSM()).get_secret("MY_KEY") == "val"
