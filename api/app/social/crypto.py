"""Social tokens are encrypted at rest (Fernet / AES-128-CBC + HMAC)."""
import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    if s.token_encryption_key:
        return Fernet(s.token_encryption_key.encode())
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"clipeo-social-tokens", info=b"fernet").derive(
        s.secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None
