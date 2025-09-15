from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas, security
from app.db import base as db_base
from app.db import models as db_models

router = APIRouter()

@router.get("/", response_model=List[schemas.Check])
async def read_checks(
    db: AsyncSession = Depends(db_base.get_db),
    current_user: db_models.User = Depends(security.get_current_user),
):
    return await crud.get_checks_by_owner(db=db, owner_id=current_user.id)

@router.post("/", response_model=schemas.Check, status_code=status.HTTP_201_CREATED)
async def create_check(
    check: schemas.CheckCreate,
    db: AsyncSession = Depends(db_base.get_db),
    current_user: db_models.User = Depends(security.get_current_user),
):
    return await crud.create_check(db=db, check=check, owner_id=current_user.id)

@router.delete("/{check_id}", response_model=schemas.Check)
async def delete_check(
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    current_user: db_models.User = Depends(security.get_current_user),
):
    deleted_check = await crud.delete_check(db=db, check_id=check_id, owner_id=current_user.id)
    if not deleted_check:
        raise HTTPException(status_code=404, detail="Check not found")
    return deleted_check
