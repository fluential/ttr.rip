import uuid
from datetime import datetime, timezone
from typing import Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db import models
from app import schemas, security

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

async def update_check_ping(db: AsyncSession, check: models.Check):
    check.last_ping = datetime.now(timezone.utc)
    check.status = "up"
    await db.commit()
    await db.refresh(check)
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
