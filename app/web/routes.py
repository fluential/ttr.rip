import random
from datetime import timedelta
from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import base as db_base
from app import crud, security
from app.core.config import settings

router = APIRouter()
admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/web/templates")

def generate_auth_key() -> str:
    """Generates a 16-digit key."""
    return str(random.randint(1000_0000_0000_0000, 9999_9999_9999_9999))

# --- Public Routes ---

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    auth_key = request.cookies.get("auth_key")
    if not auth_key or not (len(auth_key) == 16 and auth_key.isdigit()):
        auth_key = generate_auth_key()
        response = RedirectResponse(url=f"/dashboard/{auth_key}", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # 1 year
        return response
    return RedirectResponse(url=f"/dashboard/{auth_key}", status_code=status.HTTP_302_FOUND)

@router.get("/dashboard/{auth_key}", response_class=HTMLResponse)
async def public_dashboard(request: Request, auth_key: str):
    if not (len(auth_key) == 16 and auth_key.isdigit()):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    context = {"request": request, "auth_key": auth_key}
    response = templates.TemplateResponse("dashboard.html", context)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # Refresh cookie
    return response

# --- Admin Routes ---

@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@admin_router.post("/login", response_class=HTMLResponse)
async def handle_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(db_base.get_db),
):
    user = await crud.get_user_by_username(db, username=username)
    if user and user.is_admin and security.verify_password(password, user.hashed_password):
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = security.create_access_token(
            data={"sub": user.username}, expires_delta=access_token_expires
        )
        response = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="auth_token", value=access_token, httponly=True)
        return response
    return RedirectResponse(url="/admin/login?error=1", status_code=status.HTTP_302_FOUND)

@admin_router.get("/logout", response_class=HTMLResponse)
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("auth_token")
    return response

@admin_router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    token = request.cookies.get("auth_token")
    if not token:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "api_token": token})
