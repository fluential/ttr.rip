from fastapi import APIRouter
from app.api.v1.endpoints import login, checks, admin, metrics, status_pages, geoip

api_router = APIRouter()
api_router.include_router(login.router, tags=["login"])
api_router.include_router(checks.router, prefix="/checks", tags=["checks"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(status_pages.router, prefix="/status-pages", tags=["status-pages"])
api_router.include_router(geoip.router, prefix="/geoip", tags=["geoip"])
