import uuid
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Union, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, case, or_, and_, text
from sqlalchemy.orm import selectinload, joinedload
import logging
from app.db import models
from app import schemas, security
from app.services import notifications
from app.core import encryption
from app.worker import celery_app
from app.core.config import settings
from app.core.redis_pool import get_redis_connection

logger = logging.getLogger(__name__)

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
    """Update a user's auth key and re-encrypt any tokens"""
    # First get all checks owned by this user that have telegram bot tokens
    result = await db.execute(
        select(models.Check)
        .filter(models.Check.owner_id == user.id)
        .filter(models.Check.telegram_bot_token.isnot(None))
    )
    checks_with_tokens = result.scalars().all()
    
    # For each check, decrypt the token and re-encrypt it with the new key
    for check in checks_with_tokens:
        try:
            # Decrypt the token using old key
            decrypted_token = encryption.decrypt_token(check.telegram_bot_token)
            # Re-encrypt with new key
            check.telegram_bot_token = encryption.encrypt_token(decrypted_token)
        except Exception as e:
            logger.error(f"Failed to re-encrypt token for check {check.id}: {e}")
            # Continue with other checks even if one fails
    
    # Update the user's auth key
    user.auth_key = new_auth_key
    
    # Commit all changes
    await db.commit()
    await db.refresh(user)
    return user

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
        return schemas.CheckStats(total_checks=0, up_count=0, down_count=0, new_count=0)

    # --- Caching Layer ---
    if not settings.DEBUG_MODE and settings.REDIS_URL:
        try:
            r = get_redis_connection()
            if r:
                cache_key = f"user_stats:dashboard:{principal.id}"
                cached_stats = r.get(cache_key)
                if cached_stats:
                    logger.debug(f"Cache hit for user stats: {principal.id}")
                    return schemas.CheckStats.model_validate_json(cached_stats)
        except Exception as e:
            logger.error(f"Could not read from Redis cache for stats: {e}")
    # --- End Caching Layer ---

    # Base query for the user's checks
    if principal.is_admin:
        # Admin stats would be for all checks
        base_query = select(models.Check)
    else:
        base_query = select(models.Check).filter(models.Check.owner_id == principal.id)

    # Create a subquery from the base query to apply aggregations
    subquery = base_query.subquery()
    stats_query = select(
        func.count(subquery.c.id).label("total_checks"),
        func.sum(case((subquery.c.status == 'up', 1), else_=0)).label("up_count"),
        func.sum(case((subquery.c.status == 'down', 1), else_=0)).label("down_count"),
        func.sum(case((subquery.c.status == 'new', 1), else_=0)).label("new_count"),
        func.avg(subquery.c.interval_seconds).label("avg_interval_seconds"),
        func.avg(subquery.c.last_duration_seconds).label("avg_duration_seconds")
    )

    result = await db.execute(stats_query)
    stats = result.first()

    user_queued_notifications = await get_user_queued_notification_count(db, principal)
    
    processed_notifications: Union[int, str] = 0
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                owner_identifier = f"user_id_{principal.id}"
                
                count = r.get(f"user_stats:processed_notifications:{owner_identifier}")
                processed_notifications = int(count) if count else 0
        except Exception as e:
            logger.error(f"Could not get processed notification count for principal: {e}")
            processed_notifications = "N/A"

    if stats and stats.total_checks > 0:
        stats_obj = schemas.CheckStats(
            total_checks=stats.total_checks,
            up_count=stats.up_count or 0,
            down_count=stats.down_count or 0,
            new_count=stats.new_count or 0,
            avg_interval_seconds=stats.avg_interval_seconds,
            avg_duration_seconds=stats.avg_duration_seconds,
            user_queued_notifications=user_queued_notifications,
            processed_notifications=processed_notifications
        )
    else:
        stats_obj = schemas.CheckStats(
            total_checks=0,
            up_count=0,
            down_count=0,
            new_count=0,
            user_queued_notifications=user_queued_notifications,
            processed_notifications=processed_notifications
        )

    # --- Caching Layer ---
    if not settings.DEBUG_MODE and settings.REDIS_URL:
        try:
            r = get_redis_connection()
            if r:
                cache_key = f"user_stats:dashboard:{principal.id}"
                r.set(cache_key, stats_obj.model_dump_json(), ex=settings.STATS_CACHE_TTL_SECONDS)
                logger.debug(f"Cache set for user stats: {principal.id}")
        except Exception as e:
            logger.error(f"Could not write to Redis cache for stats: {e}")
    # --- End Caching Layer ---

    return stats_obj


async def update_check_ping(db: AsyncSession, check: models.Check):
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
    await db.commit()
    await db.refresh(check)

    if previous_status == "down":
        logger.info(f"Check '{check.name}' (ID: {check.id}) is back UP.")
        message = f"🟢 Check Up: [{check.name}] is back up."
        if check.last_duration_seconds is not None:
            duration_str = notifications.format_duration(check.last_duration_seconds)
            message += f" Last run took {duration_str}."
        notifications.schedule_telegram_notification(check, message)

    return check

async def update_check_start(db: AsyncSession, check: models.Check):
    check.last_start = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(check)
    return check

async def update_check_fail(db: AsyncSession, check: models.Check):
    previous_status = check.status
    check.status = "down"
    check.last_start = None
    await db.commit()
    await db.refresh(check)

    if previous_status != "down":
        message = f"🔴 Check Failed: [{check.name}] reported a failure."
        notifications.schedule_telegram_notification(check, message)

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

    next_cursor = None
    prev_cursor = None

    if is_prev:
        # If we're fetching a previous page, the items are in reverse order
        items.reverse()
        # The "next" cursor is the first item we fetched (before reversing)
        next_cursor = _encode_cursor(getattr(items[0], sort_by)) if items else None
        # The "previous" cursor is the last item if we fetched a full page
        prev_cursor = _encode_cursor(getattr(items[-1], sort_by)) if len(items) > size else None
    else:
        # The "previous" cursor is the first item we fetched
        prev_cursor = _encode_cursor(getattr(items[0], sort_by)) if items else None
        # The "next" cursor is the last item if we fetched more than page size
        if len(items) > size:
            next_cursor = _encode_cursor(getattr(items[size-1], sort_by))
            items = items[:size] # Trim the extra item

    return items, next_cursor, prev_cursor


async def create_check(db: AsyncSession, check: schemas.CheckCreate, principal: models.User):
    """
    Creates a check. If the principal (user) is not yet persisted in the database,
    it creates the user first.
    """
    try:
        # If the principal doesn't have an ID, it's a new user that needs to be created.
        if not principal.id:
            logger.info(f"Creating new user for auth_key ...{principal.auth_key[-4:]}")
            user_schema = schemas.UserCreate(auth_key=principal.auth_key)
            # The `create_user` function will add, commit, and refresh.
            principal = await create_user(db, user=user_schema)
            logger.info(f"New user created with ID: {principal.id}")

        # Now, principal is guaranteed to be a persisted User object.
        db_check_data = {
            **check.model_dump(),
            "uuid": str(uuid.uuid4()),
            "owner_id": principal.id
        }
        
        db_check = models.Check(**db_check_data)
        db.add(db_check)
        await db.commit()
        await db.refresh(db_check)
        
        # Attach the owner for the response model.
        db_check.owner = principal
        
        return db_check
    except Exception as e:
        logger.error(f"Error in create_check: {e}", exc_info=True)
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

        if now > deadline:
            db_check.status = "down"
        else:
            db_check.status = "up" if db_check.last_ping else "new"

        await db.commit()
        await db.refresh(db_check)
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
        if 'telegram_bot_token' in update_data:
            token = update_data['telegram_bot_token']
            if token:
                update_data['telegram_bot_token'] = encryption.encrypt_token(token)
            else:
                # Store None if the token is cleared
                update_data['telegram_bot_token'] = None

        for key, value in update_data.items():
            setattr(db_check, key, value)
        await db.commit()
        await db.refresh(db_check)
    return db_check


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
        await db.delete(db_check)
        await db.commit()
        return db_check
    return None
