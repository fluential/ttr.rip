from fastapi import APIRouter
from app.api.v1.endpoints import login, checks, admin

api_router = APIRouter()
api_router.include_router(login.router, tags=["login"])
api_router.include_router(checks.router, prefix="/checks", tags=["checks"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
