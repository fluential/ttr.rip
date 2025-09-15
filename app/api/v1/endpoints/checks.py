from typing import List, Union
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas, security
from app.core.config import settings
from app.db import base as db_base
from app.db import models as db_models

router = APIRouter()

@router.get("/", response_model=List[schemas.Check])
async def read_checks(
    db: AsyncSession = Depends(db_base.get_db),
    principal: Union[db_models.User, str] = Depends(security.get_auth_principal),
):
    return await crud.get_checks_by_owner(db=db, principal=principal)

@router.post("/", response_model=schemas.Check, status_code=status.HTTP_201_CREATED)
async def create_check(
    check: schemas.CheckCreate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: Union[db_models.User, str] = Depends(security.get_auth_principal),
):
    return await crud.create_check(db=db, check=check, principal=principal)

@router.put("/{check_id}", response_model=schemas.Check)
async def update_check(
    check_id: int,
    check: schemas.CheckUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: Union[db_models.User, str] = Depends(security.get_auth_principal),
):
    updated_check = await crud.update_check(db=db, check_id=check_id, check_data=check, principal=principal)
    if not updated_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return updated_check

@router.put("/{check_id}/telegram", response_model=schemas.Check)
async def update_check_telegram_settings(
    check_id: int,
    telegram_settings: schemas.TelegramSettingsUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: Union[db_models.User, str] = Depends(security.get_auth_principal),
    telegram_session: dict = Depends(security.get_telegram_session_data),
):
    if not settings.DEBUG_MODE:
        if not telegram_session or telegram_session.get("check_id") != check_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram authentication required")

    updated_check = await crud.update_check_telegram_settings(db=db, check_id=check_id, settings_data=telegram_settings, principal=principal)
    if not updated_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return updated_check

@router.delete("/{check_id}", response_model=schemas.Check)
async def delete_check(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: Union[db_models.User, str] = Depends(security.get_auth_principal),
):
    deleted_check = await crud.delete_check(db=db, check_id=check_id, principal=principal)
    if not deleted_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return deleted_check
