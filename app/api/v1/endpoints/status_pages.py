from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app import crud, schemas, security
from app.db import base as db_base
from app.db import models as db_models

router = APIRouter()

@router.get("", response_model=List[schemas.StatusPage])
async def read_status_pages(
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    return await crud.get_status_pages_by_owner(db=db, principal=principal)

@router.post("", response_model=schemas.StatusPage, status_code=status.HTTP_201_CREATED)
async def create_status_page(
    status_page: schemas.StatusPageCreate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    if not principal.id:
        raise HTTPException(status_code=401, detail="Cannot create status page for a non-existent user.")
    try:
        return await crud.create_status_page(db=db, status_page=status_page, principal=principal)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A status page with this slug already exists.")

@router.put("/{status_page_id}", response_model=schemas.StatusPage)
async def update_status_page(
    status_page_id: int,
    status_page: schemas.StatusPageUpdate,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    if not principal.id:
        raise HTTPException(status_code=401, detail="Invalid user.")
    try:
        updated = await crud.update_status_page(db=db, status_page_id=status_page_id, status_page_data=status_page, principal=principal)
        if not updated:
            raise HTTPException(status_code=404, detail="Status page not found.")
        return updated
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A status page with this slug already exists.")

@router.delete("/{status_page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status_page(
    status_page_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    principal: db_models.User = Depends(security.get_public_user_from_key),
):
    if not principal.id:
        raise HTTPException(status_code=401, detail="Invalid user.")
    deleted = await crud.delete_status_page(db=db, status_page_id=status_page_id, principal=principal)
    if not deleted:
        raise HTTPException(status_code=404, detail="Status page not found.")
