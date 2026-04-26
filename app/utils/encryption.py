"""
Application-level encryption utilities for sensitive personal data.

Uses Fernet (symmetric AES-128-CBC with HMAC-SHA256) for encrypt/decrypt
and peppered SHA-256 for deterministic hash columns used in lookups.

All encrypted values are stored as base64-encoded strings in the database.
"""
import hashlib
import json
import logging
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import String, Text, TypeDecorator

from app.config import settings

logger = logging.getLogger(__name__)

_fernet_instance: Optional[Fernet] = None


def get_fernet() -> Fernet:
    """Lazy-initialised Fernet instance from ENCRYPTION_KEY setting."""
    global _fernet_instance
    if _fernet_instance is None:
        if not settings.encryption_key:
            raise RuntimeError(
                "ENCRYPTION_KEY is not set. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet_instance = Fernet(settings.encryption_key.encode())
    return _fernet_instance


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    if plaintext is None:
        return None
    if plaintext == "":
        return ""
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext string. Returns plaintext."""
    if ciphertext is None:
        return None
    if ciphertext == "":
        return ""
    return get_fernet().decrypt(ciphertext.encode()).decode()


def hash_value(value: str) -> str:
    """
    Deterministic peppered SHA-256 hash for lookup columns.
    Used for email_hash, filename_hash, token_hash, description_hash.
    """
    if not value:
        return ""
    return hashlib.sha256(
        (value + (settings.hash_salt or "")).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Custom SQLAlchemy column types
# ---------------------------------------------------------------------------

class EncryptedString(TypeDecorator):
    """Auto-encrypt/decrypt String column."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


class EncryptedText(TypeDecorator):
    """Auto-encrypt/decrypt Text column."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


class EncryptedJSON(TypeDecorator):
    """
    Auto-encrypt/decrypt JSON column.
    Stored as encrypted Text in DB; exposed as Python dict/list.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return encrypt_value(json.dumps(value, default=str))
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            decrypted = decrypt_value(value)
            if decrypted:
                return json.loads(decrypted)
        return None


class EncryptedNumeric(TypeDecorator):
    """
    Auto-encrypt/decrypt Numeric column.
    Stored as encrypted Text in DB; exposed as Python Decimal.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return encrypt_value(str(value))
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            from decimal import Decimal, InvalidOperation
            decrypted = decrypt_value(value)
            if decrypted:
                try:
                    return Decimal(decrypted)
                except InvalidOperation:
                    logger.warning("Failed to decrypt numeric value, returning 0")
                    return Decimal("0")
        return None
