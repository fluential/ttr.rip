import uuid
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, case, or_
from sqlalchemy.orm import selectinload
import logging
from app.db import models
from app import schemas, security
from app.services import notifications
from app.core import encryption
from app.worker import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

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
        auth_key=user.auth_key
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

# Check CRUD
async def get_check_by_uuid(db: AsyncSession, check_uuid: str):
    result = await db.execute(select(models.Check).filter(models.Check.uuid == check_uuid))
    return result.scalars().first()

async def get_user_queued_notification_count(db: AsyncSession, principal: models.User) -> Union[int, str]:
    if settings.DEBUG_MODE:
        return 0

    try:
        import redis
        r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
        
        owner_identifier = f"user_id_{principal.id}"
        
        count = r.get(f"user_stats:queued_notifications:{owner_identifier}")
        return int(count) if count else 0
    except Exception as e:
        logger.error(f"Could not get user queued notification count: {e}", exc_info=False)
        return "N/A"


async def get_check_by_id_and_owner(db: AsyncSession, check_id: int, principal: models.User):
    query = select(models.Check).filter(models.Check.id == check_id)
    if not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    # Admin can see any check

    result = await db.execute(query)
    return result.scalars().first()

async def get_check_stats_by_owner(db: AsyncSession, principal: models.User):
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
            import redis
            r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
            owner_identifier = f"user_id_{principal.id}"
            
            count = r.get(f"user_stats:processed_notifications:{owner_identifier}")
            processed_notifications = int(count) if count else 0
        except Exception as e:
            logger.error(f"Could not get processed notification count for principal: {e}")
            processed_notifications = "N/A"

    if stats and stats.total_checks > 0:
        return schemas.CheckStats(
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
        return schemas.CheckStats(
            total_checks=0,
            up_count=0,
            down_count=0,
            new_count=0,
            user_queued_notifications=user_queued_notifications,
            processed_notifications=processed_notifications
        )


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

async def get_checks_by_owner(db: AsyncSession, principal: models.User, page: int = 1, size: int = 25, sort_by: str = 'id', sort_direction: str = 'desc'):
    # Base query
    if principal.is_admin:
        query = select(models.Check).options(selectinload(models.Check.owner))
    else:
        query = select(models.Check).filter(models.Check.owner_id == principal.id)

    # Add total count to the main query using a window function
    query = query.add_columns(func.count(models.Check.id).over().label("total_count"))

    # Apply sorting
    sort_column = getattr(models.Check, sort_by, models.Check.id)
    if sort_direction == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Apply pagination
    query = query.offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return [], 0

    items = [row.Check for row in rows]
    total = rows[0].total_count

    return items, total

async def create_check(db: AsyncSession, check: schemas.CheckCreate, principal: models.User):
    db_check_data = {
        **check.model_dump(),
        "uuid": str(uuid.uuid4()),
        "owner_id": principal.id
    }

    db_check = models.Check(**db_check_data)
    db.add(db_check)
    await db.commit()
    await db.refresh(db_check)
    return db_check

async def update_check(db: AsyncSession, check_id: int, check_data: schemas.CheckUpdate, principal: models.User):
    query = select(models.Check).filter(models.Check.id == check_id)
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
    query = select(models.Check).filter(models.Check.id == check_id)
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
    query = select(models.Check).filter(models.Check.id == check_id)
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
