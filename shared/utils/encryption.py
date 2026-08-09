import logging

from cryptography.fernet import Fernet, InvalidToken
from decouple import config

logger = logging.getLogger(__name__)

# Derive a Fernet key from FIELD_ENCRYPTION_KEY env var.
# Fernet requires a 32-byte URL-safe base64-encoded key.
_raw_key = config("FIELD_ENCRYPTION_KEY", default="")


def _get_fernet():
    if not _raw_key:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(_raw_key.encode() if isinstance(_raw_key, str) else _raw_key) # type: ignore


def encrypt_field(value: str) -> str:
    if not value:
        return value
    f = _get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_field(value: str) -> str:
    if not value:
        return value
    f = _get_fernet()
    try:
        return f.decrypt(value.encode()).decode()
    except InvalidToken:
        logger.warning("decrypt_field: value appears to be plaintext, returning as-is")
        return value


def mask_account_number(value: str) -> str:
    """Mask an account number, showing only the last 4 digits. e.g. '****1234'."""
    if not value or len(value) <= 4:
        return value
    return "*" * (len(value) - 4) + value[-4:]
