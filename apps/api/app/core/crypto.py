"""
Credential encryption for integrations.

Production uses AWS KMS envelope encryption; dev uses a Fernet key derived
from settings.encryption_key. Interface is identical so swapping is trivial.
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from .config import get_settings


def _fernet() -> Fernet:
    raw = get_settings().encryption_key.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("utf-8")
