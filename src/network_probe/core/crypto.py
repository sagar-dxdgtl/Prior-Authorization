from __future__ import annotations

import hashlib
import hmac
import re
from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet


class CryptoProvider(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, token: str) -> str: ...


class FernetCrypto:
    """First key encrypts; all keys can decrypt (rotation). KMS-wrapped variant lands in Slice B."""

    def __init__(self, keys: list[str]):
        if not keys or not keys[0]:
            raise ValueError("FernetCrypto requires at least one key")
        self._mf = MultiFernet([Fernet(k.encode()) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._mf.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._mf.decrypt(token.encode()).decode()


def resolve_fernet_keys(
    raw_keys: list[str],
    *,
    kms_enabled: bool | None = None,
    kms_client=None,
    region: str | None = None,
) -> list[str]:
    """When KMS is enabled, each raw key is a base64-encoded KMS ciphertext blob whose
    plaintext is the real urlsafe-b64 Fernet key; decrypt each via KMS.
    Otherwise return raw_keys unchanged (local/dev default — off by default).
    """
    from network_probe.core.config import get_settings

    s = get_settings()
    if kms_enabled is None:
        kms_enabled = s.fernet_keys_kms
    if not kms_enabled or not raw_keys:
        return list(raw_keys)
    import base64

    client = kms_client
    if client is None:
        import boto3

        client = boto3.client("kms", region_name=region or s.aws_default_region)
    out = []
    for k in raw_keys:
        resp = client.decrypt(CiphertextBlob=base64.b64decode(k))
        pt = resp["Plaintext"]
        out.append(pt.decode() if isinstance(pt, (bytes, bytearray)) else pt)
    return out


def hash_member_id(member_id: str, pepper: str) -> str:
    """HMAC-SHA256 (keyed) so low-entropy member IDs can't be brute-forced from the digest."""
    norm = re.sub(r"\s+", "", (member_id or "")).upper()
    return hmac.new(pepper.encode(), norm.encode(), hashlib.sha256).hexdigest()
