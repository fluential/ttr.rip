import logging
import sys
import hmac
import hashlib
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    # The master key must be a valid Fernet key.
    Fernet(settings.ENCRYPTION_KEY.encode())
except (ValueError, TypeError) as e:
    logger.critical(f"Invalid ENCRYPTION_KEY: {e}. The key must be 32 url-safe base64-encoded bytes. Application will not start.")
    sys.exit(1)

def get_derived_key(master_key: str, salt: str) -> bytes:
    """
    Derives a user-specific encryption key from the master key and a salt (user's auth_key).
    Uses HMAC-based Key Derivation Function (HKDF) logic, simplified for this use case.
    """
    # Use HMAC-SHA256 as the Pseudo-Random Function (PRF)
    # The derived key for Fernet must be 32 bytes and url-safe base64 encoded.
    derived = hmac.new(master_key.encode(), msg=salt.encode(), digestmod=hashlib.sha256).digest()
    return urlsafe_b64encode(derived)

def encrypt_token(token: str, user_auth_key: str) -> str:
    """Encrypts a token using a key derived from the master key and user's auth key."""
    if not token:
        return token
    
    derived_key = get_derived_key(settings.ENCRYPTION_KEY, user_auth_key)
    fernet = Fernet(derived_key)
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str, user_auth_key: str) -> str:
    """Decrypts a token using a key derived from the master key and user's auth key."""
    if not encrypted_token:
        return encrypted_token
    
    derived_key = get_derived_key(settings.ENCRYPTION_KEY, user_auth_key)
    fernet = Fernet(derived_key)
    try:
        return fernet.decrypt(encrypted_token.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt token: Invalid token or key. This could be due to a master key or user auth_key change.")
        raise
