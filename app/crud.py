import uuid
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, case
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

async def create_user(db: AsyncSession, user: schemas.UserCreate):
    hashed_password = security.get_password_hash(user.password)
    db_user = models.User(
        username=user.username,
        hashed_password=hashed_password,
        is_admin=user.is_admin
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

# Check CRUD
async def get_check_by_uuid(db: AsyncSession, check_uuid: str):
    result = await db.execute(select(models.Check).filter(models.Check.uuid == check_uuid))
    return result.scalars().first()

async def get_user_queued_notification_count(db: AsyncSession, principal: Union[models.User, str]) -> Union[int, str]:
    if settings.DEBUG_MODE:
        return 0

    if isinstance(principal, models.User):
        query = select(models.Check.id).filter(models.Check.owner_id == principal.id)
    else:
        query = select(models.Check.id).filter(models.Check.owner_key == principal)
    
    result = await db.execute(query)
    user_check_ids = {row[0] for row in result}

    if not user_check_ids:
        return 0

    try:
        import redis
        r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
        r.ping()
        queue_name = celery_app.conf.get('task_default_queue', 'celery')
        
        user_queued_count = 0
        tasks = r.lrange(queue_name, 0, -1)
        for task_json in tasks:
            try:
                task_data = json.loads(task_json)
                body_b64 = task_data.get('body')
                if body_b64:
                    body_json = base64.b64decode(body_b64).decode('utf-8')
                    body = json.loads(body_json)
                    # Celery task body is a list: [args, kwargs, options]
                    if isinstance(body, list) and len(body) > 0:
                        args = body[0]
                        if args and isinstance(args, list) and len(args) > 0 and isinstance(args[0], int) and args[0] in user_check_ids:
                            user_queued_count += 1
            except Exception:
                # Ignore tasks that can't be parsed, might be other task types
                continue
        
        return user_queued_count

    except Exception as e:
        logger.error(f"Could not get user queue stats: {e}", exc_info=False)
        return "N/A"


async def get_check_by_id_and_owner(db: AsyncSession, check_id: int, principal: Union[models.User, str]):
    query = select(models.Check).filter(models.Check.id == check_id)
    if isinstance(principal, models.User) and not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    elif not isinstance(principal, models.User):
        query = query.filter(models.Check.owner_key == principal)
    # Admin can see any check

    result = await db.execute(query)
    return result.scalars().first()

async def get_check_stats_by_owner(db: AsyncSession, principal: Union[models.User, str]):
    # Base query for the user's checks
    if isinstance(principal, models.User) and principal.is_admin:
        # Admin stats would be for all checks
        query = select(models.Check)
    elif isinstance(principal, models.User):
        query = select(models.Check).filter(models.Check.owner_id == principal.id)
    else:
        query = select(models.Check).filter(models.Check.owner_key == principal)

    stats_query = select(
        func.count(models.Check.id).label("total_checks"),
        func.sum(case((models.Check.status == 'up', 1), else_=0)).label("up_count"),
        func.sum(case((models.Check.status == 'down', 1), else_=0)).label("down_count"),
        func.sum(case((models.Check.status == 'new', 1), else_=0)).label("new_count"),
        func.avg(models.Check.interval_seconds).label("avg_interval_seconds"),
        func.avg(models.Check.last_duration_seconds).label("avg_duration_seconds")
    ).select_from(query.subquery())

    result = await db.execute(stats_query)
    stats = result.first()

    user_queued_notifications = await get_user_queued_notification_count(db, principal)
    
    processed_notifications: Union[int, str] = 0
    if not settings.DEBUG_MODE:
        try:
            import redis
            r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
            if isinstance(principal, models.User):
                owner_identifier = f"user_id_{principal.id}"
            else:
                owner_identifier = principal
            
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
        notifications.schedule_telegram_notification(check.id, message)

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
        notifications.schedule_telegram_notification(check.id, message)

    return check

async def get_checks_by_owner(db: AsyncSession, principal: Union[models.User, str], page: int = 1, size: int = 25, sort_by: str = 'id', sort_direction: str = 'desc'):
    # Base query
    if isinstance(principal, models.User) and principal.is_admin:
        query = select(models.Check)
    elif isinstance(principal, models.User):
        # Non-admin user (not currently possible but for future)
        query = select(models.Check).filter(models.Check.owner_id == principal.id)
    else:
        # Public user identified by auth key
        query = select(models.Check).filter(models.Check.owner_key == principal)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply sorting
    sort_column = getattr(models.Check, sort_by, models.Check.id)
    if sort_direction == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Apply pagination
    query = query.offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    items = result.scalars().all()
    
    return items, total

async def create_check(db: AsyncSession, check: schemas.CheckCreate, principal: Union[models.User, str]):
    db_check_data = {
        **check.model_dump(),
        "uuid": str(uuid.uuid4()),
    }
    if isinstance(principal, models.User):
        db_check_data["owner_id"] = principal.id
    else:
        db_check_data["owner_key"] = principal

    db_check = models.Check(**db_check_data)
    db.add(db_check)
    await db.commit()
    await db.refresh(db_check)
    return db_check

async def update_check(db: AsyncSession, check_id: int, check_data: schemas.CheckUpdate, principal: Union[models.User, str]):
    query = select(models.Check).filter(models.Check.id == check_id)
    if isinstance(principal, models.User) and not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    elif not isinstance(principal, models.User):
        query = query.filter(models.Check.owner_key == principal)
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


async def create_or_update_telegram_auth(db: AsyncSession, check_id: int, login_data: schemas.TelegramLoginData):
    # Find existing auth for this check
    result = await db.execute(select(models.TelegramAuth).filter(models.TelegramAuth.check_id == check_id))
    db_auth = result.scalars().first()

    if db_auth:
        # Update existing auth record
        db_auth.telegram_user_id = login_data.id
        db_auth.first_name = login_data.first_name
        db_auth.username = login_data.username
        db_auth.auth_date = login_data.auth_date
        db_auth.hash = login_data.hash
    else:
        # Create new auth record
        db_auth = models.TelegramAuth(
            check_id=check_id,
            telegram_user_id=login_data.id,
            first_name=login_data.first_name,
            username=login_data.username,
            auth_date=login_data.auth_date,
            hash=login_data.hash,
        )
        db.add(db_auth)
    
    await db.commit()
    await db.refresh(db_auth)
    return db_auth


async def update_check_telegram_settings(db: AsyncSession, check_id: int, settings_data: schemas.TelegramSettingsUpdate, principal: Union[models.User, str]):
    query = select(models.Check).filter(models.Check.id == check_id)
    if isinstance(principal, models.User) and not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    elif not isinstance(principal, models.User):
        query = query.filter(models.Check.owner_key == principal)
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


async def delete_check(db: AsyncSession, check_id: int, principal: Union[models.User, str]):
    query = select(models.Check).filter(models.Check.id == check_id)
    if isinstance(principal, models.User) and not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    elif not isinstance(principal, models.User):
        query = query.filter(models.Check.owner_key == principal)
    # For admin, no owner filter is applied.

    result = await db.execute(query)
    db_check = result.scalars().first()
    if db_check:
        await db.delete(db_check)
        await db.commit()
        return db_check
    return None
