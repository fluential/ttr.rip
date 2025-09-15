import uuid
from datetime import datetime, timezone, timedelta
from typing import Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db import models
from app import schemas, security
from app.services import notifications

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

async def get_check_by_id_and_owner(db: AsyncSession, check_id: int, principal: Union[models.User, str]):
    query = select(models.Check).filter(models.Check.id == check_id)
    if isinstance(principal, models.User) and not principal.is_admin:
        query = query.filter(models.Check.owner_id == principal.id)
    elif not isinstance(principal, models.User):
        query = query.filter(models.Check.owner_key == principal)
    # Admin can see any check

    result = await db.execute(query)
    return result.scalars().first()

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

async def get_checks_by_owner(db: AsyncSession, principal: Union[models.User, str]):
    if isinstance(principal, models.User) and principal.is_admin:
        # Admin sees all checks, joining with User to get username
        result = await db.execute(
            select(models.Check)
            .order_by(models.Check.id.desc())
        )
    elif isinstance(principal, models.User):
        # Non-admin user (not currently possible but for future)
        result = await db.execute(select(models.Check).filter(models.Check.owner_id == principal.id).order_by(models.Check.id.desc()))
    else:
        # Public user identified by auth key
        result = await db.execute(select(models.Check).filter(models.Check.owner_key == principal).order_by(models.Check.id.desc()))
    return result.scalars().all()

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
