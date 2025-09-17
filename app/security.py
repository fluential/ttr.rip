import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import Depends, HTTPException, status, Header, Cookie, Form, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db import base as db_base
from app.db import models as db_models
from app import crud, schemas
from app.core.redis_pool import get_redis_connection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/token", auto_error=False)
logger = logging.getLogger(__name__)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

# --- CSRF Protection ---

def generate_csrf_token() -> str:
    """Generates a secure, URL-safe token for CSRF protection."""
    return secrets.token_urlsafe(32)

async def verify_form_csrf_token(
    request: Request,
    csrf_token_form: str = Form(..., alias="csrf_token"),
):
    """
    FastAPI dependency to verify the CSRF token from a form submission
    against the token stored in the request state (set by middleware or another dependency).
    """
    csrf_token_cookie = request.cookies.get("csrf_token")
    if not csrf_token_cookie or not secrets.compare_digest(csrf_token_cookie, csrf_token_form):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch")

async def verify_api_csrf_token(
    request: Request,
    x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token"),
):
    """
    FastAPI dependency to verify the CSRF token from an API request header
    against the token stored in the cookie. (Double Submit Cookie Pattern)
    """
    csrf_token_cookie = request.cookies.get("csrf_token")
    logger.debug(f"CSRF check: cookie_token='...{csrf_token_cookie[-4:] if csrf_token_cookie else 'None'}' header_token='...{x_csrf_token[-4:] if x_csrf_token else 'None'}'")
    if not csrf_token_cookie or not x_csrf_token or not secrets.compare_digest(csrf_token_cookie, x_csrf_token):
        logger.warning("CSRF token mismatch.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch")


# --- Authentication Dependencies ---

async def get_public_user_from_key(
    x_auth_key: Optional[str] = Header(None, alias="X-Auth-Key"),
    db: AsyncSession = Depends(db_base.get_db),
) -> db_models.User:
    """
    Dependency for public, key-based authentication via the X-Auth-Key header.
    If a user is found, it is returned.
    If not, a temporary, in-memory-only User object is returned.
    The user is only persisted to the DB upon a meaningful action (e.g., creating a check).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
    )

    auth_key = x_auth_key
    logger.debug(f"get_public_user_from_key called with auth_key: ...{auth_key[-4:] if auth_key else 'None'}")

    if not auth_key:
        logger.warning("Auth key is missing from header.")
        raise credentials_exception

    # --- Blacklist Check ---
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r and r.exists(f"blacklist:auth_key:{auth_key}"):
                logger.warning(f"Authentication attempt with blacklisted key: ...{auth_key[-4:]}")
                raise credentials_exception
        except Exception as e:
            logger.error(f"Redis check failed during auth: {e}")
            # Fail closed: if we can't check the blacklist, deny access.
            raise credentials_exception
    # --- End Blacklist Check ---

    logger.debug(f"Looking up user by auth key: ...{auth_key[-4:]}")
    user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
    if not user:
        logger.warning(f"No user found for auth key ...{auth_key[-4:]}. Returning temporary user object.")
        # User does not exist. Return a temporary, non-persistent User object.
        # This allows read-only operations to proceed for a new key without a DB write.
        # The user will be created in the DB when they perform a write action (e.g., create_check).
        return db_models.User(auth_key=auth_key)
    
    logger.debug(f"User found for auth key ...{auth_key[-4:]}: user_id={user.id}")
    return user


async def get_current_admin_user(
    token_from_header: Optional[str] = Depends(oauth2_scheme),
    token_from_cookie: Optional[str] = Cookie(None, alias="auth_token"),
    db: AsyncSession = Depends(db_base.get_db)
) -> db_models.User:
    """
    Dependency for admin authentication using JWT.
    Tries to get token from Authorization header first, then from a cookie.
    Ensures the user is an admin.
    """
    token = token_from_header or token_from_cookie

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
        
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = schemas.TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    user = await crud.get_user_by_username(db, username=token_data.username)
    if user is None or not user.is_admin:
        raise credentials_exception
    return user


def create_telegram_session_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


async def get_telegram_session_data(
    telegram_session: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    if not telegram_session:
        return {} # Return empty dict if no cookie, so routes can use it for conditional logic

    try:
        payload = jwt.decode(telegram_session, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # Simplified: just check for presence of telegram_user_id
        telegram_user_id: int = payload.get("telegram_user_id")
        if telegram_user_id is None:
            return {} # Invalid payload
        return payload
    except JWTError:
        return {} # Invalid token


def validate_telegram_hash(auth_data: Dict[str, Any]) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("Cannot validate Telegram hash: TELEGRAM_BOT_TOKEN is not set.")
        return False

    hash_from_telegram = auth_data.pop('hash')
    data_check_string_parts = []
    for key, value in sorted(auth_data.items()):
        data_check_string_parts.append(f"{key}={value}")
    
    data_check_string = "\n".join(data_check_string_parts)
    
    secret_key = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.encode()).digest()
    calculated_hash = hmac.new(secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()
    
    is_valid = hmac.compare_digest(calculated_hash, hash_from_telegram)
    if not is_valid:
        logger.warning("Invalid Telegram hash received.")
    return is_valid
