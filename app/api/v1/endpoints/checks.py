from typing import List, Union, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Response, Request
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import json
import time
import hashlib

from app import crud, schemas, security
from app.services import notifications, rate_control
from app.core.config import settings
from app.core import encryption
from app.core.redis_pool import get_redis_connection
from app.api.v1.endpoints.metrics import parse_prometheus_metric, get_latency_health
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
    tag: Optional[str] = None,
    request: Request = None,
):
    allowed_sort_fields = ['id', 'name', 'created_at', 'uuid', 'deadline', 'last_ping']
    if sort_by not in allowed_sort_fields:
        raise HTTPException(status_code=400, detail=f"Invalid sort field: {sort_by}")

    principal = await crud.ensure_user_has_slug(db, principal)

    # Lightweight ETag pre-check: use DB-backed checks_version + time bucket for runtime freshness
    etag = None
    if request and principal.id:
        ver = str(getattr(principal, "checks_version", 0) or 0)
        t_bucket = int(time.time() // 2)
        etag = f'W/"{ver}:{sort_by}:{sort_direction}:{tag or ""}:{size}:{t_bucket}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "public, max-age=2"})

    items, next_cursor, prev_cursor = await crud.get_checks_by_owner(
        db=db, 
        principal=principal, 
        size=size, 
        sort_by=sort_by, 
        sort_direction=sort_direction,
        cursor=cursor,
        tag=tag
    )

    # Enrich checks with runtime data from Redis
    await crud.enrich_checks_with_runtime_data(items)

    # Build lightweight ETag from per-user version (recompute if needed)
    if not etag and principal.id:
        ver = str(getattr(principal, "checks_version", 0) or 0)
        t_bucket = int(time.time() // 2)
        etag = f'W/"{ver}:{sort_by}:{sort_direction}:{tag or ""}:{size}:{t_bucket}"'

    page = schemas.CheckPage(
        items=items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        size=size
    )
    return ORJSONResponse(content=page.model_dump(), headers={"ETag": etag, "Cache-Control": "public, max-age=5"})

@router.get("/aggregate", response_class=ORJSONResponse)
async def read_dashboard_aggregate(
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
    size: int = Query(25, ge=1, le=100),
    sort_by: str = Query('id'),
    sort_direction: str = Query('desc', pattern="^(asc|desc|asc_prev|desc_prev)$"),
    cursor: Optional[str] = None,
    tag: Optional[str] = None,
    request: Request = None,
):
    allowed_sort_fields = ['id', 'name', 'created_at', 'uuid', 'deadline', 'last_ping']
    if sort_by not in allowed_sort_fields:
        raise HTTPException(status_code=400, detail=f"Invalid sort field: {sort_by}")

    principal = await crud.ensure_user_has_slug(db, principal)

    # Lightweight ETag pre-check for aggregate (DB-backed checks_version + 2s bucket for runtime)
    etag = None
    if request and principal.id:
        ver = str(getattr(principal, "checks_version", 0) or 0)
        t_bucket = int(time.time() // 2)
        etag = f'W/"{ver}:{sort_by}:{sort_direction}:{tag or ""}:{size}:{t_bucket}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "public, max-age=2"})

    items, next_cursor, prev_cursor = await crud.get_checks_by_owner(
        db=db, 
        principal=principal, 
        size=size, 
        sort_by=sort_by, 
        sort_direction=sort_direction,
        cursor=cursor,
        tag=tag
    )
    await crud.enrich_checks_with_runtime_data(items)

    # User stats
    user_stats = await crud.get_check_stats_by_owner(db=db, principal=principal)
    user_stats_payload = user_stats.model_dump() if hasattr(user_stats, "model_dump") else user_stats

    # Tags
    tags_list = await crud.get_all_tags_by_owner(db=db, principal=principal)
    tags_payload = [t.model_dump() if hasattr(t, "model_dump") else {"id": t.id, "name": t.name} for t in tags_list]

    # Metrics summary (inline, mirrored from metrics endpoint)
    avg_api_latency = parse_prometheus_metric("ttl_api_request_duration_seconds")
    avg_db_latency = parse_prometheus_metric("ttl_db_query_duration_seconds")
    avg_redis_latency = parse_prometheus_metric("ttl_redis_command_duration_seconds")
    metrics_summary = {
        "total_checks": int(parse_prometheus_metric("ttl_checks_total") or 0),
        "total_notifications_sent": int(parse_prometheus_metric("ttl_notifications_sent_total") or 0),
        "average_api_latency_seconds": avg_api_latency,
        "average_db_latency_seconds": avg_db_latency,
        "average_redis_latency_seconds": avg_redis_latency,
        "workers_online": int(parse_prometheus_metric("ttl_workers_online") or 0),
        "queue_depth": int(parse_prometheus_metric("ttl_queue_size") or 0),
        "health": {
            "api_latency": get_latency_health(avg_api_latency, yellow_threshold=0.5, red_threshold=1.0),
            "db_latency": get_latency_health(avg_db_latency, yellow_threshold=0.1, red_threshold=0.5),
            "redis_latency": get_latency_health(avg_redis_latency, yellow_threshold=0.01, red_threshold=0.1),
        }
    }

    checks_page = schemas.CheckPage(
        items=items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        size=size
    ).model_dump()

    payload = {
        "checks": checks_page,
        "user_stats": user_stats_payload,
        "metrics_summary": metrics_summary,
        "tags": tags_payload,
        "user_slug": principal.slug or "",
        "ping_base": f"/p/{principal.slug}" if principal.slug else None,
    }

    # Lightweight ETag from per-user version + 2s time bucket
    ver = str(getattr(principal, "checks_version", 0) or 0)
    t_bucket = int(time.time() // 2)
    etag = f'W/"{ver}:{sort_by}:{sort_direction}:{tag or ""}:{size}:{t_bucket}"'

    if request and request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    return ORJSONResponse(content=payload, headers={"ETag": etag, "Cache-Control": "public, max-age=5"})

@router.get("/slug-check", response_class=ORJSONResponse)
async def check_slug_availability(
    slug: str,
    check_id: Optional[int] = None,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    if not principal.id:
        raise HTTPException(status_code=403, detail="User not found")
    
    is_taken = await crud.is_slug_taken(db, slug=slug, owner_id=principal.id, check_id=check_id)
    return ORJSONResponse(content={"is_taken": is_taken})

@router.get("/tags", response_model=List[schemas.Tag])
async def read_tags(
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    return await crud.get_all_tags_by_owner(db=db, principal=principal)


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


@router.put("/{check_id}/slack", response_model=schemas.Check)
async def update_check_slack_settings(
    check_id: int,
    slack_settings: schemas.SlackSettingsUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    updated_check = await crud.update_check_slack_settings(db=db, check_id=check_id, settings_data=slack_settings, principal=principal)
    if not updated_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return updated_check


@router.put("/{check_id}/discord", response_model=schemas.Check)
async def update_check_discord_settings(
    check_id: int,
    discord_settings: schemas.DiscordSettingsUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    updated_check = await crud.update_check_discord_settings(db=db, check_id=check_id, settings_data=discord_settings, principal=principal)
    if not updated_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return updated_check


@router.put("/{check_id}/webhook", response_model=schemas.Check)
async def update_check_webhook_settings(
    check_id: int,
    webhook_settings: schemas.WebhookSettingsUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    updated_check = await crud.update_check_webhook_settings(db=db, check_id=check_id, settings_data=webhook_settings, principal=principal)
    if not updated_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return updated_check


@router.get("/{check_id}/content", response_class=ORJSONResponse)
async def get_check_last_content(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    # First, verify the user has access to this check
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    content = "No content captured yet."
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                redis_content = await r.hget(crud.get_check_runtime_redis_key(check_id), "last_content")
                if redis_content:
                    content = redis_content.decode() if isinstance(redis_content, bytes) else redis_content
        except Exception as e:
            logger.error(f"Failed to retrieve content from Redis for check {check_id}: {e}")
            content = "Error retrieving content from storage."
    
    return ORJSONResponse(content={"content": content})

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


@router.post("/{check_id}/slack/test", response_model=schemas.Check, status_code=status.HTTP_200_OK)
async def test_slack_notification(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check: raise HTTPException(status_code=404, detail="Check not found")
    if not all([check.slack_enabled, check.slack_webhook_url]):
        raise HTTPException(status_code=400, detail="Slack settings are incomplete.")
    message = f"🔔 This is a test notification for your check '[{check.name}]'."
    await notifications.send_slack_notification(db, check, message)
    await db.commit()
    await db.refresh(check)
    return check


@router.post("/{check_id}/discord/test", response_model=schemas.Check, status_code=status.HTTP_200_OK)
async def test_discord_notification(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check: raise HTTPException(status_code=404, detail="Check not found")
    if not all([check.discord_enabled, check.discord_webhook_url]):
        raise HTTPException(status_code=400, detail="Discord settings are incomplete.")
    message = f"🔔 This is a test notification for your check '[{check.name}]'."
    await notifications.send_discord_notification(db, check, message)
    await db.commit()
    await db.refresh(check)
    return check


@router.post("/{check_id}/webhook/test", response_model=schemas.Check, status_code=status.HTTP_200_OK)
async def test_webhook_notification(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check: raise HTTPException(status_code=404, detail="Check not found")
    if not all([check.webhook_enabled, check.webhook_url]):
        raise HTTPException(status_code=400, detail="Webhook settings are incomplete.")
    message = f"🔔 This is a test notification for your check '[{check.name}]'."
    await notifications.send_webhook_notification(db, check, message)
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
    await notifications.schedule_telegram_notification(check, message)
    return {"message": "Test notification queued."}


@router.post("/{check_id}/slack/test-queue", status_code=status.HTTP_202_ACCEPTED)
async def test_slack_notification_queue(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check: raise HTTPException(status_code=404, detail="Check not found")
    if not all([check.slack_enabled, check.slack_webhook_url]):
        raise HTTPException(status_code=400, detail="Slack settings are incomplete.")
    message = f"🔔 This is a test notification for your check '[{check.name}]' (via queue)."
    await notifications.schedule_slack_notification(check, message)
    return {"message": "Test notification queued."}


@router.post("/{check_id}/discord/test-queue", status_code=status.HTTP_202_ACCEPTED)
async def test_discord_notification_queue(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check: raise HTTPException(status_code=404, detail="Check not found")
    if not all([check.discord_enabled, check.discord_webhook_url]):
        raise HTTPException(status_code=400, detail="Discord settings are incomplete.")
    message = f"🔔 This is a test notification for your check '[{check.name}]' (via queue)."
    await notifications.schedule_discord_notification(check, message)
    return {"message": "Test notification queued."}


@router.post("/{check_id}/webhook/test-queue", status_code=status.HTTP_202_ACCEPTED)
async def test_webhook_notification_queue(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check: raise HTTPException(status_code=404, detail="Check not found")
    if not all([check.webhook_enabled, check.webhook_url]):
        raise HTTPException(status_code=400, detail="Webhook settings are incomplete.")
    message = f"🔔 This is a test notification for your check '[{check.name}]' (via queue)."
    await notifications.schedule_webhook_notification(check, message)
    return {"message": "Test notification queued."}

@router.get("/{check_id}/{integration}/rate", response_class=ORJSONResponse)
async def get_integration_rate_snapshot(
    check_id: int,
    integration: str,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_either_admin_or_public_user),
):
    """
    Returns current adaptive rate state for the given integration on this check.
    Shows effective RPS, minimum drip, burst, and whether it's limited (backoff or drained).
    """
    integration = integration.lower()
    if integration not in ("telegram", "slack", "discord", "webhook"):
        raise HTTPException(status_code=400, detail="Unsupported integration")

    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    # Determine channel and fast-path resolve cached identity to avoid decrypt on each call
    if integration == "telegram":
        if not (check.telegram_enabled and check.telegram_bot_token):
            return ORJSONResponse(content={"enabled": False})
        channel = "telegram"
    elif integration == "slack":
        if not (check.slack_enabled and check.slack_webhook_url):
            return ORJSONResponse(content={"enabled": False})
        channel = "slack"
    elif integration == "discord":
        if not (check.discord_enabled and check.discord_webhook_url):
            return ORJSONResponse(content={"enabled": False})
        channel = "discord"
    else:  # webhook
        if not (check.webhook_enabled and check.webhook_url):
            return ORJSONResponse(content={"enabled": False})
        channel = "webhook"

    identity = await rate_control.get_cached_identity(channel, check.id)  # type: ignore[arg-type]
    if not identity:
        # Cache miss: decrypt once, derive identity, then cache for future lookups
        try:
            if channel == "telegram":
                decrypted = encryption.decrypt_token(check.telegram_bot_token, check.owner.auth_key)  # type: ignore[arg-type]
            elif channel == "slack":
                decrypted = encryption.decrypt_token(check.slack_webhook_url, check.owner.auth_key)  # type: ignore[arg-type]
            elif channel == "discord":
                decrypted = encryption.decrypt_token(check.discord_webhook_url, check.owner.auth_key)  # type: ignore[arg-type]
            else:
                decrypted = encryption.decrypt_token(check.webhook_url, check.owner.auth_key)  # type: ignore[arg-type]
            identity = hashlib.sha256(decrypted.encode("utf-8")).hexdigest()[:10]
            await rate_control.cache_identity(channel, check.id, identity)  # type: ignore[arg-type]
        except Exception:
            # If decryption fails, consider it disabled for safety
            return ORJSONResponse(content={"enabled": False})

    snap = await rate_control.snapshot(channel, identity)
    return ORJSONResponse(content=snap)


@router.post("/{check_id}/toggle-pause", response_model=schemas.Check)
async def toggle_pause_check(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    check = await crud.get_check_by_id_and_owner(db=db, check_id=check_id, principal=principal)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    
    return await crud.toggle_check_pause(db=db, check=check)


@router.delete("/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_check(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    deleted_check = await crud.delete_check(db=db, check_id=check_id, principal=principal)
    if not deleted_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/export", response_class=ORJSONResponse)
async def export_checks(
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    if not principal.id:
        raise HTTPException(status_code=404, detail="User not found")

    checks = await crud.get_all_checks_by_owner(db=db, principal=principal)
    
    export_data = [schemas.CheckExport.model_validate(c).model_dump() for c in checks]

    headers = {
        'Content-Disposition': 'attachment; filename="ttr_rip_checks_export.json"'
    }
    return ORJSONResponse(content=export_data, headers=headers)


@router.post("/import", response_model=schemas.CheckImportResponse)
async def import_checks(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    if not file.content_type == "application/json":
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a JSON file.")

    contents = await file.read()
    try:
        data = json.loads(contents)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file.")

    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="JSON file should contain a list of checks.")

    imported_count = 0
    failed_count = 0
    errors = []

    for i, check_data in enumerate(data):
        try:
            # Validate with the export schema
            check_to_import = schemas.CheckExport.model_validate(check_data)
            
            # Create the basic check
            check_create = schemas.CheckCreate(
                name=check_to_import.name,
                interval_seconds=check_to_import.interval_seconds,
                grace_seconds=check_to_import.grace_seconds,
            )
            new_check = await crud.create_check(db=db, check=check_create, principal=principal)
            
            # Manually set properties and commit
            # Re-encrypt imported secrets with the user's current auth_key
            new_check.telegram_bot_token = encryption.encrypt_token(check_to_import.telegram_bot_token, principal.auth_key) if check_to_import.telegram_bot_token else None
            new_check.telegram_chat_id = check_to_import.telegram_chat_id
            new_check.telegram_enabled = check_to_import.telegram_enabled

            new_check.slack_webhook_url = encryption.encrypt_token(check_to_import.slack_webhook_url, principal.auth_key) if check_to_import.slack_webhook_url else None
            new_check.slack_enabled = check_to_import.slack_enabled

            new_check.discord_webhook_url = encryption.encrypt_token(check_to_import.discord_webhook_url, principal.auth_key) if check_to_import.discord_webhook_url else None
            new_check.discord_enabled = check_to_import.discord_enabled

            new_check.webhook_url = encryption.encrypt_token(check_to_import.webhook_url, principal.auth_key) if check_to_import.webhook_url else None
            new_check.webhook_enabled = check_to_import.webhook_enabled
            
            await db.commit()
            
            imported_count += 1
        except Exception as e:
            await db.rollback() # Rollback on error for this check
            failed_count += 1
            errors.append(f"Check #{i+1} ('{check_data.get('name', 'N/A')}'): {str(e)}")

    return schemas.CheckImportResponse(
        imported_count=imported_count,
        failed_count=failed_count,
        errors=errors,
    )
