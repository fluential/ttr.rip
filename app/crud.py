import uuid
from datetime import datetime, timezone
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
    db_user = models.User(username=user.username, hashed_password=hashed_password)
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

async def get_checks_by_owner(db: AsyncSession, owner_id: int):
    result = await db.execute(select(models.Check).filter(models.Check.owner_id == owner_id).order_by(models.Check.id.desc()))
    return result.scalars().all()

async def create_check(db: AsyncSession, check: schemas.CheckCreate, owner_id: int):
    db_check = models.Check(
        **check.model_dump(),
        uuid=str(uuid.uuid4()),
        owner_id=owner_id
    )
    db.add(db_check)
    await db.commit()
    await db.refresh(db_check)
    return db_check

async def delete_check(db: AsyncSession, check_id: int, owner_id: int):
    result = await db.execute(
        select(models.Check).filter(models.Check.id == check_id, models.Check.owner_id == owner_id)
    )
    db_check = result.scalars().first()
    if db_check:
        await db.delete(db_check)
        await db.commit()
        return db_check
    return None
