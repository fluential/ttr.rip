import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import Depends, HTTPException, status, Header, Cookie
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

async def get_auth_principal(
    token: Optional[str] = Depends(oauth2_scheme),
    x_auth_key: Optional[str] = Header(None, alias="X-Auth-Key"),
    db: AsyncSession = Depends(db_base.get_db),
) -> db_models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user: Optional[db_models.User] = None

    # Fix: Check if token is a string before attempting to decode it
    if token and isinstance(token, str):
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise credentials_exception
            token_data = schemas.TokenData(username=username)
        except JWTError:
            raise credentials_exception
        user = await crud.get_user_by_username(db, username=token_data.username)
        if user is None: # Admin user must exist
            raise credentials_exception
        return user

    if x_auth_key:
        user = await crud.get_user_by_auth_key(db, auth_key=x_auth_key)
        if not user:
            try:
                # Just-in-time user creation for auth_key users
                user_schema = schemas.UserCreate(auth_key=x_auth_key)
                user = await crud.create_user(db, user=user_schema)
            except IntegrityError:
                await db.rollback()
                # The user was likely created by a concurrent request. Fetch it.
                user = await crud.get_user_by_auth_key(db, auth_key=x_auth_key)
                if not user:
                    # This would be a very strange state, but handle it.
                    logger.error(f"Failed to create or find user for auth_key ...{x_auth_key[-4:]} after IntegrityError.")
                    raise credentials_exception
        return user
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(db_base.get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = schemas.TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = await crud.get_user_by_username(db, username=token_data.username)
    if user is None:
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
