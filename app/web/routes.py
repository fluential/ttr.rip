import secrets
from datetime import timedelta
from fastapi import APIRouter, Request, Depends, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import logging

from app.db import base as db_base
from app.db import models as db_models
from app import crud, security, schemas
from app.core.config import settings
from app.core import encryption

router = APIRouter()
admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)

def generate_auth_key() -> str:
    """Generates a secure, URL-safe token."""
    return secrets.token_urlsafe(24) # 32 characters

# --- Public Routes ---

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    csrf_token = security.generate_csrf_token()
    context = {
        "request": request,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
        "telegram_auth_enabled": settings.TELEGRAM_AUTH_ENABLED,
        "telegram_bot_name": settings.TELEGRAM_BOT_NAME,
        "csrf_token": csrf_token,
    }
    response = templates.TemplateResponse("public_login.html", context)
    response.delete_cookie("auth_key")
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax")
    return response

@router.post("/dashboard", response_class=HTMLResponse, dependencies=[Depends(security.verify_form_csrf_token)])
async def login_with_key(request: Request, auth_key: str = Form(...), db: AsyncSession = Depends(db_base.get_db)):
    # We don't need to validate the key here. If it's invalid, the user just won't see any checks.
    # If it's a valid key for an existing user, they'll see their checks.
    # If it's a new key, a user will be created when they create their first check.
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax")
    return response

@router.get("/new", response_class=HTMLResponse)
async def new_anonymous_user(request: Request, db: AsyncSession = Depends(db_base.get_db)):
    auth_key = generate_auth_key()
    # We no longer create the user here. It will be created just-in-time.
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(db_base.get_db)):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
    status_pages = []
    if user and user.id:
        status_pages = await crud.get_status_pages_by_owner(db, principal=user)

    logger.info(f"Dashboard loading. TELEGRAM_AUTH_ENABLED: {settings.TELEGRAM_AUTH_ENABLED}, TELEGRAM_BOT_NAME: '{settings.TELEGRAM_BOT_NAME}'")

    csrf_token = security.generate_csrf_token()
    # No need to check for user existence here. The dashboard will simply show
    # "no checks" if the key is new or invalid. The API calls will handle auth.
    context = {
        "request": request,
        "auth_key": auth_key,
        "csrf_token": csrf_token,
        "status_pages": status_pages,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
        "telegram_auth_enabled": settings.TELEGRAM_AUTH_ENABLED,
        "telegram_bot_name": settings.TELEGRAM_BOT_NAME,
    }
    response = templates.TemplateResponse("dashboard.html", context)
    # Refresh cookie on activity
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax")
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax")
    return response


@router.get("/telegram/callback", response_class=HTMLResponse, name="telegram_callback")
async def telegram_callback(
    request: Request,
    db: AsyncSession = Depends(db_base.get_db),
):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        raise HTTPException(status_code=401, detail="Authentication key cookie not found.")

    user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
    if not user:
        # This should theoretically not happen due to JIT creation and exception in dependency
        raise HTTPException(status_code=404, detail="User not found for the provided auth key.")

    query_params = dict(request.query_params)
    logger.info(f"Handling Telegram auth callback for user ID {user.id} with auth_key ...{user.auth_key[-4:]}")
    if not security.validate_telegram_hash(query_params.copy()):
        raise HTTPException(status_code=400, detail="Invalid hash from Telegram")

    login_data = schemas.TelegramLoginData(**query_params)
    
    # Check if this telegram ID is already linked to another user
    existing_telegram_user = await crud.get_user_by_telegram_id(db, login_data.id)
    if existing_telegram_user and existing_telegram_user.id != user.id:
        # This is a complex state - for now, we prevent linking.
        # A more advanced implementation could offer to merge accounts.
        logger.warning(f"Telegram ID {login_data.id} is already linked to user {existing_telegram_user.id}. Preventing link for user {user.id}.")
        raise HTTPException(status_code=409, detail="This Telegram account is already linked to a different user.")

    await crud.link_telegram_to_user(db, user=user, login_data=login_data)
    logger.info(f"Successfully linked Telegram account {login_data.username} (ID: {login_data.id}) to user {user.id}.")

    # Redirect back to the dashboard
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@router.get("/telegram/login_callback", response_class=HTMLResponse, name="telegram_login_callback")
async def telegram_login_callback(
    request: Request,
    db: AsyncSession = Depends(db_base.get_db),
):
    query_params = dict(request.query_params)
    logger.info(f"Handling Telegram login callback with params: {query_params}")
    if not security.validate_telegram_hash(query_params.copy()):
        raise HTTPException(status_code=400, detail="Invalid hash from Telegram")

    login_data = schemas.TelegramLoginData(**query_params)
    
    user = await crud.get_user_by_telegram_id(db, telegram_user_id=login_data.id)
    
    if not user:
        logger.info(f"Telegram login from new user: {login_data.username} (ID: {login_data.id}). Creating new user.")
        auth_key = generate_auth_key()
        user_schema = schemas.UserCreate(
            auth_key=auth_key,
            telegram_user_id=login_data.id,
            telegram_first_name=login_data.first_name,
            telegram_username=login_data.username,
        )
        user = await crud.create_user(db, user=user_schema)
        logger.info(f"Successfully created new user {user.id} for Telegram user {login_data.username}.")
    else:
        logger.info(f"Telegram login successful for existing user {user.id} (Telegram ID: {login_data.id})")

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=user.auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax")
    return response


@router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def public_integrations(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
    if not user:
        # This could happen if the cookie is for a user that was deleted.
        # Redirect to home to get a new key.
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    check = await crud.get_check_by_id_and_owner(db, check_id=check_id, principal=user)
    if not check:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    # For security, don't populate the bot token in the form
    # We only need to know if a token exists, not what it is
    has_token = bool(check.telegram_bot_token)
    csrf_token = security.generate_csrf_token()
    status_pages = await crud.get_status_pages_by_owner(db, principal=user)

    context = {
        "request": request,
        "check": check,
        "auth_key": user.auth_key,
        "csrf_token": csrf_token,
        "status_pages": status_pages,
        "is_admin": False,
        "telegram_bot_token": "",  # Always empty for security
        "has_telegram_bot_token": has_token,  # Just indicate if one exists
        "telegram_bot_token_placeholder": "[Existing token hidden for security]" if has_token else "Enter your Telegram bot token",
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("integrations.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax")
    return response


@router.get("/s/{slug}", response_class=HTMLResponse)
async def public_status_page(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(db_base.get_db),
):
    status_page = await crud.get_status_page_by_slug(db, slug=slug)
    if not status_page or not status_page.is_public:
        raise HTTPException(status_code=404, detail="Status page not found")

    # Calculate overall status
    overall_status = "up"
    if any(c.status == "down" and not c.paused for c in status_page.checks):
        overall_status = "down"
    elif not status_page.checks:
        overall_status = "empty"

    context = {
        "request": request,
        "status_page": status_page,
        "overall_status": overall_status,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    return templates.TemplateResponse("public_status_page.html", context)


# --- Admin Routes ---

@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    csrf_token = security.generate_csrf_token()
    context = {
        "request": request,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
        "csrf_token": csrf_token,
    }
    response = templates.TemplateResponse("login.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax")
    return response

@admin_router.post("/login", response_class=HTMLResponse, dependencies=[Depends(security.verify_form_csrf_token)])
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
        response.set_cookie(key="auth_token", value=access_token, httponly=True, samesite="Lax")
        return response
    return RedirectResponse(url="/admin/login?error=1", status_code=status.HTTP_302_FOUND)

@admin_router.get("/logout", response_class=HTMLResponse)
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("auth_token")
    return response

@admin_router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(db_base.get_db), admin_user: db_models.User = Depends(security.get_current_admin_user)):
    token = request.cookies.get("auth_token")
    if not token:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    status_pages = await crud.get_status_pages_by_owner(db=db, principal=admin_user)
    csrf_token = security.generate_csrf_token()
    context = {
        "request": request,
        "api_token": token,
        "csrf_token": csrf_token,
        "status_pages": status_pages,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("admin_dashboard.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax")
    return response


@admin_router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def admin_integrations(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    admin_user: db_models.User = Depends(security.get_current_admin_user),
):
    token = request.cookies.get("auth_token")
    if not token:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    # Use the authorization-aware CRUD function for defense in depth.
    check = await crud.get_check_by_id_and_owner(db, check_id=check_id, principal=admin_user)

    if not check:
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)

    # For security, don't populate the bot token in the form
    # We only need to know if a token exists, not what it is
    has_token = bool(check.telegram_bot_token)
    csrf_token = security.generate_csrf_token()
    status_pages = await crud.get_status_pages_by_owner(db=db, principal=admin_user)

    context = {
        "request": request,
        "check": check,
        "api_token": token,
        "csrf_token": csrf_token,
        "status_pages": status_pages,
        "is_admin": True,
        "telegram_bot_token": "",  # Always empty for security
        "has_telegram_bot_token": has_token,  # Just indicate if one exists
        "telegram_bot_token_placeholder": "[Existing token hidden for security]" if has_token else "Enter your Telegram bot token",
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("integrations.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax")
    return response
