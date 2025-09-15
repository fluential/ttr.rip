import logging
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    fernet = Fernet(settings.ENCRYPTION_KEY.encode())
except (ValueError, TypeError) as e:
    logger.critical(f"Invalid ENCRYPTION_KEY: {e}. The key must be 32 url-safe base64-encoded bytes. The application may not function correctly.")
    fernet = None

def encrypt_token(token: str) -> str:
    """Encrypts a token using Fernet."""
    if not fernet:
        raise ValueError("Encryption key is not configured correctly.")
    if not token:
        return token
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypts a token using Fernet."""
    if not fernet:
        raise ValueError("Encryption key is not configured correctly.")
    if not encrypted_token:
        return encrypted_token
    try:
        return fernet.decrypt(encrypted_token.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt token: Invalid token or key.")
        raise
