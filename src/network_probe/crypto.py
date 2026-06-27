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

def hash_member_id(member_id: str, pepper: str) -> str:
    """HMAC-SHA256 (keyed) so low-entropy member IDs can't be brute-forced from the digest."""
    norm = re.sub(r"\s+", "", (member_id or "")).upper()
    return hmac.new(pepper.encode(), norm.encode(), hashlib.sha256).hexdigest()
