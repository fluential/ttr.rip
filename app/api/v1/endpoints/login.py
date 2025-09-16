from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Header, Response
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

@router.delete("/user", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_account(
    response: Response,
    user: db_models.User = Depends(security.get_public_user_from_key),
    db: AsyncSession = Depends(db_base.get_db)
):
    """Deletes a user and all their associated data."""
    if not user or not user.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication key"
        )
    
    auth_key_to_blacklist = user.auth_key
    
    await crud.delete_user_and_data(db, user=user)
    
    # Add the key to the Redis blacklist and invalidate auth cache
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                pipe = r.pipeline()
                # Blacklist for 24 hours (86400 seconds)
                pipe.set(f"blacklist:auth_key:{auth_key_to_blacklist}", "1", ex=86400)
                # Invalidate the auth cache for this key
                pipe.delete(f"auth_cache:{auth_key_to_blacklist}")
                pipe.execute()
                logger.info(f"Blacklisted and cleared auth cache for key of deleted user {user.id}: ...{auth_key_to_blacklist[-4:]}")
        except Exception as e:
            logger.error(f"Failed to update Redis for deleted user {user.id}: {e}")

    # Clear the auth cookie
    response.delete_cookie("auth_key")
    
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post("/user/rotate-key", response_model=schemas.UserKeyResponse)
async def rotate_api_key(
    response: Response,
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
    
    # Add the old key to the Redis blacklist and invalidate its auth cache
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                pipe = r.pipeline()
                # Blacklist for 24 hours (86400 seconds)
                pipe.set(f"blacklist:auth_key:{old_key}", "1", ex=86400)
                # Invalidate the auth cache for the old key
                pipe.delete(f"auth_cache:{old_key}")
                pipe.execute()
                logger.info(f"Blacklisted and cleared auth cache for old key of user {user.id}: ...{old_key[-4:]}")
        except Exception as e:
            logger.error(f"Failed to update Redis for key rotation for user {user.id}: {e}")
            # This is not a fatal error for the rotation itself, but should be logged.

    # Set the new key in an HttpOnly cookie on the response
    response.set_cookie(
        key="auth_key",
        value=user.auth_key,
        httponly=True,
        max_age=365 * 24 * 60 * 60,  # 1 year
        samesite="Lax"
    )

    return {"auth_key": user.auth_key}
