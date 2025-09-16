from typing import List, Union, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app import crud, schemas, security
from app.services import notifications
from app.core.config import settings
from app.db import base as db_base
from app.db import models as db_models

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/stats", response_model=schemas.CheckStats)
async def read_check_stats(
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    return await crud.get_check_stats_by_owner(db=db, principal=principal)


@router.get("", response_model=schemas.CheckPage)
async def read_checks(
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
    size: int = Query(25, ge=1, le=100),
    sort_by: str = Query('id'),
    sort_direction: str = Query('desc', pattern="^(asc|desc|asc_prev|desc_prev)$"),
    cursor: Optional[str] = None,
):
    allowed_sort_fields = ['id', 'name', 'status', 'created_at', 'last_ping', 'last_duration_seconds', 'uuid']
    if sort_by not in allowed_sort_fields:
        raise HTTPException(status_code=400, detail=f"Invalid sort field: {sort_by}")

    items, next_cursor, prev_cursor = await crud.get_checks_by_owner(
        db=db, 
        principal=principal, 
        size=size, 
        sort_by=sort_by, 
        sort_direction=sort_direction,
        cursor=cursor
    )

    return schemas.CheckPage(
        items=items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        size=size
    )

@router.post("", response_model=schemas.Check, status_code=status.HTTP_201_CREATED)
async def create_check(
    check: schemas.CheckCreate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    return await crud.create_check(db=db, check=check, principal=principal)

@router.put("/{check_id}", response_model=schemas.Check)
async def update_check(
    check_id: int,
    check: schemas.CheckUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
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
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    updated_check = await crud.update_check_telegram_settings(db=db, check_id=check_id, settings_data=telegram_settings, principal=principal)
    if not updated_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return updated_check

@router.post("/{check_id}/telegram/test", response_model=schemas.Check, status_code=status.HTTP_200_OK)
async def test_telegram_notification(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    if not all([check.telegram_enabled, check.telegram_bot_token, check.telegram_chat_id]):
        raise HTTPException(status_code=400, detail="Telegram settings are incomplete. Please save your settings first.")

    logger.info(f"Sending test Telegram notification for check ID {check_id} on behalf of user {principal.id}.")
    message = f"🔔 This is a test notification for your check '[{check.name}]'."
    await notifications.send_telegram_notification(db, check, message)
    await db.commit()
    await db.refresh(check)
    return check

@router.post("/{check_id}/telegram/test-queue", status_code=status.HTTP_202_ACCEPTED)
async def test_telegram_notification_queue(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    if not all([check.telegram_enabled, check.telegram_bot_token, check.telegram_chat_id]):
        raise HTTPException(status_code=400, detail="Telegram settings are incomplete. Please save your settings first.")

    logger.info(f"Queueing test Telegram notification for check ID {check_id} on behalf of user {principal.id}.")
    message = f"🔔 This is a test notification for your check '[{check.name}]' (via queue)."
    notifications.schedule_telegram_notification(check, message)
    return {"message": "Test notification queued."}


@router.delete("/{check_id}", response_model=schemas.Check)
async def delete_check(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    deleted_check = await crud.delete_check(db=db, check_id=check_id, principal=principal)
    if not deleted_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return deleted_check
