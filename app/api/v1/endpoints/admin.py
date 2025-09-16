from fastapi import APIRouter, Depends, HTTPException, status
from app import security
from app.db import models as db_models
from app.services import queue_stats

router = APIRouter()

@router.get("/stats", status_code=status.HTTP_200_OK)
async def get_system_stats(
    current_user: db_models.User = Depends(security.get_current_admin_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")
    
    stats = queue_stats.get_queue_stats()
    return stats
