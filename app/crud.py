import uuid
import json
import base64
import re
from datetime import datetime, timezone, timedelta
from typing import Union, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, case, or_, and_, text
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.exc import IntegrityError
import logging
from app.db import models
from app import schemas, security
from app.services import notifications
from app.core import encryption
from app.worker import celery_app
from app.core.config import settings
from app.core.redis_pool import get_redis_connection

logger = logging.getLogger(__name__)

def _calculate_deadline(check: models.Check) -> Optional[datetime]:
    """Calculates the deadline for a check."""
    if not check.last_ping and not check.created_at:
        return None
    
    reference_time = check.last_ping or check.created_at
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
        
    return reference_time + timedelta(seconds=check.interval_seconds + check.grace_seconds)

def _update_redis_stats_counters(user_id: int, old_status: Optional[str], new_status: Optional[str], is_paused: bool = False):
    """Atomically updates user stats counters in Redis."""
    if settings.DEBUG_MODE or not settings.REDIS_URL or old_status == new_status:
        return

    # If a check is paused, its contribution is to the 'paused' counter, which is handled
    # by the toggle_check_pause function. Changes to the underlying status of a paused
    # check should not affect the visible stats.
    if is_paused and new_status is not None: # The new_status check prevents affecting deletion
        return

    try:
        r = get_redis_connection()
        if not r:
            return
        
        pipe = r.pipeline()
        key = f"user_stats:counters:{user_id}"

        # Decrement old status counter if it exists
        if old_status:
            pipe.hincrby(key, old_status, -1)
        
        # Increment new status counter if it exists
        if new_status:
            pipe.hincrby(key, new_status, 1)
        
        # Update total count
        if old_status and not new_status: # Deletion
            pipe.hincrby(key, "total", -1)
        elif not old_status and new_status: # Creation
            pipe.hincrby(key, "total", 1)
        
        pipe.execute()
        logger.debug(f"Updated Redis stats for user {user_id}: {old_status} -> {new_status}")
    except Exception as e:
        logger.error(f"Could not update Redis stats counters for user {user_id}: {e}")


def _encode_cursor(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.isoformat()
    
    json_str = json.dumps(value)
    return base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')

def _decode_cursor(cursor: Optional[str]) -> Any:
    if cursor is None:
        return None
    try:
        json_str = base64.urlsafe_b64decode(cursor).decode('utf-8')
        value = json.loads(json_str)
        # Attempt to convert back to datetime if it's an ISO format string
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return value
    except Exception:
        return None

# User CRUD
async def get_user_by_username(db: AsyncSession, username: str):
    result = await db.execute(select(models.User).filter(models.User.username == username))
    return result.scalars().first()

async def get_user_by_auth_key(db: AsyncSession, auth_key: str):
    result = await db.execute(select(models.User).filter(models.User.auth_key == auth_key))
    return result.scalars().first()

async def get_user_by_telegram_id(db: AsyncSession, telegram_user_id: int):
    result = await db.execute(select(models.User).filter(models.User.telegram_user_id == telegram_user_id))
    return result.scalars().first()

async def create_user(db: AsyncSession, user: schemas.UserCreate):
    hashed_password = None
    if user.password:
        hashed_password = security.get_password_hash(user.password)
    
    db_user = models.User(
        username=user.username,
        hashed_password=hashed_password,
        is_admin=user.is_admin,
        auth_key=user.auth_key,
        telegram_user_id=user.telegram_user_id,
        telegram_first_name=user.telegram_first_name,
        telegram_username=user.telegram_username,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def update_user_auth_key(db: AsyncSession, user: models.User, new_auth_key: str):
    """
    Update a user's auth key. This requires re-encrypting all of the user's secrets
    (like Telegram tokens) because their encryption is tied to the auth_key.
    """
    old_auth_key = user.auth_key

    # First get all checks owned by this user that have any encrypted tokens
    result = await db.execute(
        select(models.Check)
        .filter(models.Check.owner_id == user.id)
        .filter(or_(
            models.Check.telegram_bot_token.isnot(None),
            models.Check.slack_webhook_url.isnot(None),
            models.Check.discord_webhook_url.isnot(None),
            models.Check.webhook_url.isnot(None)
        ))
    )
    checks_with_tokens = result.scalars().all()
    
    logger.info(f"Re-encrypting tokens for {len(checks_with_tokens)} checks for user {user.id} due to auth_key rotation.")

    # For each check, decrypt the token with the old key and re-encrypt it with the new key
    for check in checks_with_tokens:
        encrypted_fields = [
            "telegram_bot_token", "slack_webhook_url", 
            "discord_webhook_url", "webhook_url"
        ]
        for field in encrypted_fields:
            encrypted_value = getattr(check, field)
            if encrypted_value:
                try:
                    decrypted_value = encryption.decrypt_token(encrypted_value, old_auth_key)
                    setattr(check, field, encryption.encrypt_token(decrypted_value, new_auth_key))
                except Exception as e:
                    logger.error(f"Failed to re-encrypt {field} for check {check.id} during auth_key rotation: {e}. This may cause notification failures for this check.")
                    # Continue with other checks even if one fails
    
    # Update the user's auth key
    user.auth_key = new_auth_key
    
    # Commit all changes (user key and re-encrypted tokens)
    await db.commit()
    await db.refresh(user)
    return user

async def get_all_checks_by_owner(db: AsyncSession, principal: models.User):
    """Gets all checks for a given principal, without pagination."""
    if not principal.id:
        return []

    query = select(models.Check).filter(models.Check.owner_id == principal.id)
    result = await db.execute(query)
    return result.scalars().all()

# Check CRUD
async def get_check_by_uuid(db: AsyncSession, check_uuid: str):
    result = await db.execute(select(models.Check).filter(models.Check.uuid == check_uuid))
    return result.scalars().first()

async def get_user_queued_notification_count(db: AsyncSession, principal: models.User) -> Union[int, str]:
    if settings.DEBUG_MODE or not principal.id:
        return 0

    try:
        r = get_redis_connection()
        if not r:
            return "N/A"
        
        owner_identifier = f"user_id_{principal.id}"
        
        count = r.get(f"user_stats:queued_notifications:{owner_identifier}")
        return int(count) if count else 0
    except Exception as e:
        logger.error(f"Could not get user queued notification count: {e}", exc_info=False)
        return "N/A"


async def get_check_by_id_and_owner(db: AsyncSession, check_id: int, principal: models.User):
    # If the principal has no ID, they can't own any checks yet.
    if not principal.id:
        return None
        
    query = select(models.Check).filter(models.Check.id == check_id).options(joinedload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    # Admin can see any check

    result = await db.execute(query)
    return result.scalars().first()

async def get_check_stats_by_owner(db: AsyncSession, principal: models.User):
    # If the principal has no ID, they have no stats.
    if not principal.id:
        return schemas.CheckStats(total_checks=0, up_count=0, down_count=0, new_count=0, paused_count=0)

    # --- Redis Counter Strategy ---
    if not settings.DEBUG_MODE and settings.REDIS_URL:
        try:
            r = get_redis_connection()
            if r:
                key = f"user_stats:counters:{principal.id}"
                # HGETALL returns strings, so we need to convert them
                cached_counters = r.hgetall(key)
                if cached_counters:
                    logger.debug(f"Redis counter hit for user stats: {principal.id}")
                    # Averages are not stored in counters, so we still need a DB query for them.
                    # This is a compromise to keep counter logic simple.
                    avg_query = select(
                        func.avg(models.Check.interval_seconds).label("avg_interval_seconds"),
                        func.avg(models.Check.last_duration_seconds).label("avg_duration_seconds")
                    ).filter(models.Check.owner_id == principal.id)
                    
                    result = await db.execute(avg_query)
                    averages = result.first()

                    return schemas.CheckStats(
                        total_checks=int(cached_counters.get("total", 0)),
                        up_count=int(cached_counters.get("up", 0)),
                        down_count=int(cached_counters.get("down", 0)),
                        new_count=int(cached_counters.get("new", 0)),
                        paused_count=int(cached_counters.get("paused", 0)),
                        avg_interval_seconds=averages.avg_interval_seconds if averages else None,
                        avg_duration_seconds=averages.avg_duration_seconds if averages else None,
                        user_queued_notifications=await get_user_queued_notification_count(db, principal)
                    )
        except Exception as e:
            logger.error(f"Could not read from Redis counters for stats: {e}")
    # --- End Redis Counter Strategy ---

    # Fallback to DB query if Redis fails, is disabled, or counters are not hydrated.
    logger.info(f"Falling back to DB query for user stats: {principal.id}")
    base_query = select(models.Check).filter(models.Check.owner_id == principal.id)

    subquery = base_query.subquery()
    stats_query = select(
        func.count(subquery.c.id).label("total_checks"),
        func.sum(case((and_(subquery.c.status == 'up', subquery.c.paused == False), 1), else_=0)).label("up_count"),
        func.sum(case((and_(subquery.c.status == 'down', subquery.c.paused == False), 1), else_=0)).label("down_count"),
        func.sum(case((and_(subquery.c.status == 'new', subquery.c.paused == False), 1), else_=0)).label("new_count"),
        func.sum(case((subquery.c.paused == True, 1), else_=0)).label("paused_count"),
        func.avg(subquery.c.interval_seconds).label("avg_interval_seconds"),
        func.avg(subquery.c.last_duration_seconds).label("avg_duration_seconds")
    )

    result = await db.execute(stats_query)
    stats = result.first()
    
    if stats and stats.total_checks > 0:
        stats_obj = schemas.CheckStats(
            total_checks=stats.total_checks,
            up_count=stats.up_count or 0,
            down_count=stats.down_count or 0,
            new_count=stats.new_count or 0,
            paused_count=stats.paused_count or 0,
            avg_interval_seconds=stats.avg_interval_seconds,
            avg_duration_seconds=stats.avg_duration_seconds,
            user_queued_notifications=await get_user_queued_notification_count(db, principal)
        )
    else:
        stats_obj = schemas.CheckStats(total_checks=0, up_count=0, down_count=0, new_count=0, paused_count=0)

    # --- Rehydrate Redis Counters ---
    if not settings.DEBUG_MODE and settings.REDIS_URL:
        try:
            r = get_redis_connection()
            if r:
                key = f"user_stats:counters:{principal.id}"
                pipe = r.pipeline()
                pipe.hset(key, "total", stats_obj.total_checks)
                pipe.hset(key, "up", stats_obj.up_count)
                pipe.hset(key, "down", stats_obj.down_count)
                pipe.hset(key, "new", stats_obj.new_count)
                pipe.hset(key, "paused", stats_obj.paused_count)
                pipe.execute()
                logger.info(f"Rehydrated Redis counters for user {principal.id}")
        except Exception as e:
            logger.error(f"Could not rehydrate Redis counters for user {principal.id}: {e}")
    # --- End Rehydration ---

    return stats_obj


def _validate_content(check: models.Check, content: Optional[str]) -> tuple[bool, str]:
    """
    Validates if the content matches the check's criteria.
    Returns a tuple of (validation_passed, reason).
    """
    if not check.expected_content:
        return True, "" # No content validation required

    if content is None:
        content = "" # Treat no content as an empty string

    rule = check.expected_content_type or 'present'
    pattern = check.expected_content
    use_regex = check.use_regex_for_content

    match_found = False
    try:
        if use_regex:
            # Use re.DOTALL to allow '.' to match newlines
            if re.search(pattern, content, re.DOTALL):
                match_found = True
        else:
            if pattern in content:
                match_found = True
    except re.error as e:
        logger.warning(f"Invalid regex for check {check.id}: {e}")
        return False, f"Invalid regex: {e}" # Fail on invalid regex

    if rule == 'present' and not match_found:
        return False, "Expected content not found"
    
    if rule == 'absent' and match_found:
        return False, "Unexpected content was found"

    return True, ""


async def update_check_ping(db: AsyncSession, check: models.Check, content: Optional[str] = None):
    validation_passed, reason = _validate_content(check, content)
    if not validation_passed:
        logger.info(f"Check '{check.name}' (ID: {check.id}) failed content validation: {reason}.")
        # The content is already stored in Redis by the ping endpoint, so we don't need to pass it here.
        return await update_check_fail(db, check, reason=reason)

    now = datetime.now(timezone.utc)
    if check.last_start:
        last_start = check.last_start
        if last_start.tzinfo is None:
            last_start = last_start.replace(tzinfo=timezone.utc)
        duration = now - last_start
        check.last_duration_seconds = int(duration.total_seconds())
    elif check.last_ping:
        last_ping = check.last_ping
        if last_ping.tzinfo is None:
            last_ping = last_ping.replace(tzinfo=timezone.utc)
        duration = now - last_ping
        check.last_duration_seconds = int(duration.total_seconds())
    else:
        check.last_duration_seconds = None

    previous_status = check.status
    check.last_ping = now
    check.status = "up"
    check.last_start = None
    check.deadline = _calculate_deadline(check)
    await db.commit()
    await db.refresh(check)
    _update_redis_stats_counters(check.owner_id, previous_status, "up", is_paused=check.paused)

    if previous_status == "down":
        logger.info(f"Check '{check.name}' (ID: {check.id}) is back UP.")
        message = f"🟢 Check Up: [{check.name}] is back up."
        if check.last_duration_seconds is not None:
            duration_str = notifications.format_duration(check.last_duration_seconds)
            message += f" Last run took {duration_str}."
        notifications.schedule_all_notifications(check, message)

    return check

async def update_check_start(db: AsyncSession, check: models.Check):
    check.last_start = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(check)
    return check

async def toggle_check_pause(db: AsyncSession, check: models.Check):
    """Toggles the paused state of a check and updates Redis counters."""
    is_pausing = not check.paused
    check.paused = is_pausing
    
    # Update redis counters
    if not settings.DEBUG_MODE and settings.REDIS_URL:
        try:
            r = get_redis_connection()
            if r:
                pipe = r.pipeline()
                key = f"user_stats:counters:{check.owner_id}"
                if is_pausing:
                    # Decrement status, increment paused
                    pipe.hincrby(key, check.status, -1)
                    pipe.hincrby(key, "paused", 1)
                else: # Resuming
                    # Decrement paused, increment status
                    pipe.hincrby(key, "paused", -1)
                    pipe.hincrby(key, check.status, 1)
                pipe.execute()
        except Exception as e:
            logger.error(f"Could not update Redis stats counters for pause toggle: {e}")

    await db.commit()
    await db.refresh(check)
    return check

async def update_check_fail(db: AsyncSession, check: models.Check, reason: Optional[str] = None):
    previous_status = check.status
    check.status = "down"
    check.last_start = None
    await db.commit()
    await db.refresh(check)
    _update_redis_stats_counters(check.owner_id, previous_status, "down", is_paused=check.paused)

    if previous_status != "down":
        message = f"🔴 Check Failed: [{check.name}] reported a failure."
        if reason:
            message += f" Reason: {reason}."
        notifications.schedule_all_notifications(check, message)

    return check

async def get_checks_by_owner(db: AsyncSession, principal: models.User, size: int = 25, sort_by: str = 'id', sort_direction: str = 'desc', cursor: Optional[str] = None):
    # If the principal has no ID, they can't have any checks.
    if not principal.id:
        return [], None, None

    query = select(models.Check)
    if principal.is_admin:
        query = query.options(selectinload(models.Check.owner))
    else:
        query = query.filter(models.Check.owner_id == principal.id)

    sort_column = getattr(models.Check, sort_by, models.Check.id)
    is_expires_sort = False # This logic is no longer needed with the deadline column

    cursor_val = _decode_cursor(cursor)

    # For reverse direction (prev page), we flip the sort and the operator
    is_prev = sort_direction.endswith('_prev')
    if is_prev:
        sort_direction = 'asc' if sort_direction.startswith('desc') else 'desc'
    
    if cursor_val is not None:
        if (sort_direction == 'desc' and not is_prev) or (sort_direction == 'asc' and is_prev):
            query = query.where(sort_column < cursor_val)
        else:
            query = query.where(sort_column > cursor_val)

    if sort_direction == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Fetch one more than the page size to check if there's a next page
    query = query.limit(size + 1)
    
    result = await db.execute(query)

    items = result.scalars().all()
    cursor_values = None

    next_cursor = None
    prev_cursor = None

    if is_prev:
        # Items were fetched in reverse order. We reverse them back for display.
        items.reverse()

        has_prev_page = len(items) > size

        # The 'next' cursor always points to the last item of the page we just fetched.
        if items:
            next_cursor_val = getattr(items[-1], sort_by)
            next_cursor = _encode_cursor(next_cursor_val)

        if has_prev_page:
            # The extra item is now at the beginning. Trim it for the response.
            items = items[1:]
            
            # The 'prev' cursor points to the new first item of the page.
            if items:
                prev_cursor_val = getattr(items[0], sort_by)
                prev_cursor = _encode_cursor(prev_cursor_val)
        else:
            prev_cursor = None
    else: # is_next
        # The 'prev' cursor always points to the first item of the page.
        if items:
            prev_cursor_val = getattr(items[0], sort_by)
            prev_cursor = _encode_cursor(prev_cursor_val)

        if len(items) > size:
            # The 'next' cursor points to the last item of the page content (not the extra one).
            next_cursor_val = getattr(items[size - 1], sort_by)
            next_cursor = _encode_cursor(next_cursor_val)
            # Trim the extra item from the end for the response.
            items = items[:size]
        else:
            next_cursor = None

    return items, next_cursor, prev_cursor


async def create_check(db: AsyncSession, check: schemas.CheckCreate, principal: models.User):
    """
    Creates a check. If the principal (user) is not yet persisted in the database,
    it creates the user first. Handles race conditions for user creation.
    """
    # If the principal doesn't have an ID, it's a new user that needs to be created.
    if not principal.id:
        try:
            logger.info(f"Attempting to create new user for auth_key ...{principal.auth_key[-4:]}")
            user_schema = schemas.UserCreate(auth_key=principal.auth_key)
            principal = await create_user(db, user=user_schema)
            logger.info(f"New user created with ID: {principal.id}")
        except IntegrityError:
            await db.rollback() # Rollback the failed user creation
            logger.warning(f"Race condition detected for user creation with auth_key ...{principal.auth_key[-4:]}. Refetching user.")
            principal = await get_user_by_auth_key(db, auth_key=principal.auth_key)
            if not principal:
                # This should be virtually impossible if an IntegrityError occurred, but as a safeguard:
                logger.error(f"Failed to refetch user after IntegrityError for auth_key ...{principal.auth_key[-4:]}")
                raise Exception("Could not create or find user for check creation.")
        except Exception:
            await db.rollback()
            raise

    try:
        # Now, principal is guaranteed to be a persisted User object.
        db_check_data = {
            **check.model_dump(),
            "uuid": str(uuid.uuid4()),
            "owner_id": principal.id
        }
        
        db_check = models.Check(**db_check_data)
        db_check.deadline = _calculate_deadline(db_check)
        db.add(db_check)
        await db.commit()
        await db.refresh(db_check)
        _update_redis_stats_counters(principal.id, old_status=None, new_status="new")
        
        result = await db.execute(
            select(models.Check)
            .options(selectinload(models.Check.owner))
            .filter(models.Check.id == db_check.id)
        )
        final_check = result.scalars().one()
        
        return final_check
    except Exception as e:
        logger.error(f"Error during check creation phase: {e}", exc_info=True)
        await db.rollback()
        raise

async def update_check(db: AsyncSession, check_id: int, check_data: schemas.CheckUpdate, principal: models.User):
    # If the principal has no ID, they can't own any checks to update.
    if not principal.id:
        return None

    query = select(models.Check).filter(models.Check.id == check_id).options(joinedload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    # For admin, no owner filter is applied, can edit any check.

    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        update_data = check_data.model_dump()
        for key, value in update_data.items():
            setattr(db_check, key, value)

        # Re-evaluate status after update
        now = datetime.now(timezone.utc)
        reference_time = db_check.last_ping if db_check.last_ping else db_check.created_at
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        deadline = reference_time + timedelta(seconds=db_check.interval_seconds + db_check.grace_seconds)

        old_status = db_check.status
        if now > deadline:
            new_status = "down"
        else:
            new_status = "up" if db_check.last_ping else "new"
        db_check.status = new_status
        
        # Recalculate deadline if interval or grace period changed
        if 'interval_seconds' in update_data or 'grace_seconds' in update_data:
            db_check.deadline = _calculate_deadline(db_check)

        await db.commit()
        await db.refresh(db_check)
        _update_redis_stats_counters(principal.id, old_status, new_status, is_paused=db_check.paused)
    return db_check


async def link_telegram_to_user(db: AsyncSession, user: models.User, login_data: schemas.TelegramLoginData):
    user.telegram_user_id = login_data.id
    user.telegram_first_name = login_data.first_name
    user.telegram_username = login_data.username
    await db.commit()
    await db.refresh(user)
    return user


async def update_check_telegram_settings(db: AsyncSession, check_id: int, settings_data: schemas.TelegramSettingsUpdate, principal: models.User):
    # If the principal has no ID, they can't own any checks to update.
    if not principal.id:
        return None

    query = select(models.Check).filter(models.Check.id == check_id).options(joinedload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    # For admin, no owner filter is applied.

    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        update_data = settings_data.model_dump(exclude_unset=True)
        
        # Handle the bot token separately to avoid clearing it unintentionally
        if 'telegram_bot_token' in update_data:
            token = update_data.pop('telegram_bot_token') # Remove from dict
            if token: # Only update if a new token is provided
                db_check.telegram_bot_token = encryption.encrypt_token(token, principal.auth_key)

        for key, value in update_data.items():
            setattr(db_check, key, value)
        await db.commit()
        await db.refresh(db_check)
    return db_check


async def update_check_slack_settings(db: AsyncSession, check_id: int, settings_data: schemas.SlackSettingsUpdate, principal: models.User):
    if not principal.id: return None
    query = select(models.Check).filter(models.Check.id == check_id).options(joinedload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    
    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        update_data = settings_data.model_dump(exclude_unset=True)
        if 'slack_webhook_url' in update_data:
            url = update_data.pop('slack_webhook_url')
            if url:
                db_check.slack_webhook_url = encryption.encrypt_token(url, principal.auth_key)
        for key, value in update_data.items():
            setattr(db_check, key, value)
        await db.commit()
        await db.refresh(db_check)
    return db_check


async def update_check_discord_settings(db: AsyncSession, check_id: int, settings_data: schemas.DiscordSettingsUpdate, principal: models.User):
    if not principal.id: return None
    query = select(models.Check).filter(models.Check.id == check_id).options(joinedload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    
    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        update_data = settings_data.model_dump(exclude_unset=True)
        if 'discord_webhook_url' in update_data:
            url = update_data.pop('discord_webhook_url')
            if url:
                db_check.discord_webhook_url = encryption.encrypt_token(url, principal.auth_key)
        for key, value in update_data.items():
            setattr(db_check, key, value)
        await db.commit()
        await db.refresh(db_check)
    return db_check


async def update_check_webhook_settings(db: AsyncSession, check_id: int, settings_data: schemas.WebhookSettingsUpdate, principal: models.User):
    if not principal.id: return None
    query = select(models.Check).filter(models.Check.id == check_id).options(joinedload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    
    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        update_data = settings_data.model_dump(exclude_unset=True)
        if 'webhook_url' in update_data:
            url = update_data.pop('webhook_url')
            if url:
                db_check.webhook_url = encryption.encrypt_token(url, principal.auth_key)
        for key, value in update_data.items():
            setattr(db_check, key, value)
        await db.commit()
        await db.refresh(db_check)
    return db_check


async def delete_user_and_data(db: AsyncSession, user: models.User):
    """
    Deletes a user and all of their associated checks using bulk operations.
    """
    if not user or not user.id:
        return

    # Get check IDs for Redis cleanup before deleting
    result = await db.execute(select(models.Check.id).filter(models.Check.owner_id == user.id))
    check_ids = result.scalars().all()
    
    logger.info(f"Deleting {len(check_ids)} checks for user {user.id} as part of account deletion.")
    
    # Bulk delete checks
    if check_ids:
        await db.execute(
            text("DELETE FROM checks WHERE owner_id = :owner_id"),
            {"owner_id": user.id}
        )

    # Now delete the user
    logger.info(f"Deleting user {user.id} (auth_key: ...{user.auth_key[-4:]}).")
    await db.delete(user)
    
    await db.commit()

    # Optional: Clean up any related Redis data if necessary
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                pipe = r.pipeline()
                # Clean up user stats counters
                pipe.delete(f"user_stats:counters:{user.id}")
                # Clean up check-related keys (if any)
                if check_ids:
                    for check_id in check_ids:
                        key_pattern = f"check:{check_id}:*"
                        keys = r.keys(key_pattern)
                        if keys:
                            pipe.delete(*keys)
                pipe.execute()
                logger.info(f"Cleaned up Redis entries for deleted user {user.id}")
        except Exception as e:
            logger.error(f"Error cleaning up Redis entries for deleted user {user.id}: {e}")


async def delete_check(db: AsyncSession, check_id: int, principal: models.User):
    # If the principal has no ID, they can't own any checks to delete.
    if not principal.id:
        return None

    query = select(models.Check).filter(models.Check.id == check_id).options(selectinload(models.Check.owner))
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    # For admin, no owner filter is applied.

    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        owner_id = db_check.owner_id
        is_paused = db_check.paused
        old_status = db_check.status
        
        await db.delete(db_check)
        await db.commit()

        # If the check was paused, we decrement the 'paused' counter.
        # Otherwise, we decrement its last known status counter.
        status_to_decrement = "paused" if is_paused else old_status
        _update_redis_stats_counters(owner_id, old_status=status_to_decrement, new_status=None)

        # Clean up associated Redis keys
        if not settings.DEBUG_MODE:
            try:
                r = get_redis_connection()
                if r:
                    pipe = r.pipeline()
                    pipe.delete(f"ping_logs:{check_id}")
                    pipe.delete(f"check_content:{check_id}")
                    pipe.execute()
                    logger.info(f"Cleaned up Redis entries for deleted check {check_id}")
            except Exception as e:
                logger.error(f"Error cleaning up Redis entries for deleted check {check_id}: {e}")

        return db_check
    return None


# Status Page CRUD
async def get_status_page_by_slug(db: AsyncSession, slug: str):
    result = await db.execute(
        select(models.StatusPage)
        .options(selectinload(models.StatusPage.checks))
        .filter(models.StatusPage.slug == slug)
    )
    return result.scalars().first()

async def get_status_pages_by_owner(db: AsyncSession, principal: models.User):
    if not principal.id:
        return []
    result = await db.execute(
        select(models.StatusPage)
        .options(selectinload(models.StatusPage.checks))
        .filter(models.StatusPage.owner_id == principal.id)
        .order_by(models.StatusPage.name)
    )
    return result.scalars().all()

async def create_status_page(db: AsyncSession, status_page: schemas.StatusPageCreate, principal: models.User):
    # Check for slug uniqueness
    existing = await db.execute(select(models.StatusPage).filter(models.StatusPage.slug == status_page.slug))
    if existing.scalars().first():
        raise IntegrityError("Status page with this slug already exists.", params=None, orig=None)

    db_status_page = models.StatusPage(
        name=status_page.name,
        slug=status_page.slug,
        owner_id=principal.id
    )

    if status_page.check_ids:
        checks_result = await db.execute(
            select(models.Check).where(models.Check.id.in_(status_page.check_ids), models.Check.owner_id == principal.id)
        )
        db_status_page.checks = checks_result.scalars().all()

    db.add(db_status_page)
    await db.commit()
    await db.refresh(db_status_page)
    return db_status_page

async def update_status_page(db: AsyncSession, status_page_id: int, status_page_data: schemas.StatusPageUpdate, principal: models.User):
    result = await db.execute(
        select(models.StatusPage)
        .options(selectinload(models.StatusPage.checks))
        .where(models.StatusPage.id == status_page_id, models.StatusPage.owner_id == principal.id)
    )
    db_status_page = result.scalars().first()
    if not db_status_page:
        return None

    # Check for slug uniqueness if it's being changed
    if status_page_data.slug != db_status_page.slug:
        existing_result = await db.execute(select(models.StatusPage).filter(models.StatusPage.slug == status_page_data.slug))
        if existing_result.scalars().first():
            raise IntegrityError("Status page with this slug already exists.", params=None, orig=None)

    db_status_page.name = status_page_data.name
    db_status_page.slug = status_page_data.slug

    if status_page_data.check_ids is not None:
        checks_result = await db.execute(
            select(models.Check).where(models.Check.id.in_(status_page_data.check_ids), models.Check.owner_id == principal.id)
        )
        db_status_page.checks = checks_result.scalars().all()

    await db.commit()
    await db.refresh(db_status_page)
    return db_status_page

async def delete_status_page(db: AsyncSession, status_page_id: int, principal: models.User):
    result = await db.execute(
        select(models.StatusPage).where(models.StatusPage.id == status_page_id, models.StatusPage.owner_id == principal.id)
    )
    db_status_page = result.scalars().first()
    if db_status_page:
        await db.delete(db_status_page)
        await db.commit()
    return db_status_page
