from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
import logging

from app import crud, schemas, security
from app.core.config import settings
from app.db import base as db_base
from app.db import models as db_models
from app.web.routes import generate_auth_key
from app.core.redis_pool import get_redis_connection

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/token", response_model=schemas.Token)
async def login_for_access_token(
    db: AsyncSession = Depends(db_base.get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
):
    user = await crud.get_user_by_username(db, username=form_data.username)
    if not user or not user.is_admin or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/user/rotate-key", response_model=schemas.UserKeyResponse)
async def rotate_api_key(
    user: db_models.User = Depends(security.get_public_user_from_key),
    db: AsyncSession = Depends(db_base.get_db)
):
    """Rotate the user's API key"""
    if not user or not user.id: # Ensure user exists in DB
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication key"
        )
    
    old_key = user.auth_key
    new_key = generate_auth_key()
    
    # Update the user in the database
    user = await crud.update_user_auth_key(db, user=user, new_auth_key=new_key)
    
    # Add the old key to the Redis blacklist with a TTL
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                # Blacklist for 24 hours (86400 seconds)
                r.set(f"blacklist:auth_key:{old_key}", "1", ex=86400)
                logger.info(f"Blacklisted old auth key for user {user.id}: ...{old_key[-4:]}")
        except Exception as e:
            logger.error(f"Failed to blacklist old auth key for user {user.id}: {e}")
            # This is not a fatal error for the rotation itself, but should be logged.

    return {"auth_key": user.auth_key}
