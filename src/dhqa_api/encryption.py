from cryptography.fernet import Fernet, InvalidToken

from dhqa_api.config import settings


def _fernet() -> Fernet:
    key = settings.app_encryption_key
    if not key:
        raise RuntimeError("APP_ENCRYPTION_KEY is required before storing connection credentials")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored credential cannot be decrypted; verify APP_ENCRYPTION_KEY") from exc
