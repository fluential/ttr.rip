import secrets
from datetime import timedelta
from fastapi import APIRouter, Request, Depends, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import base as db_base
from app.db import models as db_models
from app import crud, security, schemas
from app.core.config import settings
from app.core import encryption

router = APIRouter()
admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/web/templates")

def generate_auth_key() -> str:
    """Generates a secure, URL-safe token."""
    return secrets.token_urlsafe(24) # 32 characters

# --- Public Routes ---

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    context = {
        "request": request,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("public_login.html", context)
    response.delete_cookie("auth_key")
    return response

@router.post("/dashboard", response_class=HTMLResponse)
async def login_with_key(request: Request, auth_key: str = Form(...), db: AsyncSession = Depends(db_base.get_db)):
    # We don't need to validate the key here. If it's invalid, the user just won't see any checks.
    # If it's a valid key for an existing user, they'll see their checks.
    # If it's a new key, a user will be created when they create their first check.
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # 1 year
    return response

@router.get("/new", response_class=HTMLResponse)
async def new_anonymous_user(request: Request, db: AsyncSession = Depends(db_base.get_db)):
    auth_key = generate_auth_key()
    # We no longer create the user here. It will be created just-in-time.
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # 1 year
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(db_base.get_db)):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    # No need to check for user existence here. The dashboard will simply show
    # "no checks" if the key is new or invalid. The API calls will handle auth.
    context = {
        "request": request,
        "auth_key": auth_key,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("dashboard.html", context)
    # Refresh cookie on activity
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60)
    return response


@router.get("/telegram/callback", response_class=HTMLResponse, name="telegram_callback")
async def telegram_callback(
    request: Request,
    db: AsyncSession = Depends(db_base.get_db),
):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        raise HTTPException(status_code=403, detail="Not authenticated. Please log in with your key first.")

    # The get_auth_principal dependency will create the user if it doesn't exist.
    # We need to manually call it here to get the user principal.
    user = await security.get_auth_principal(x_auth_key=auth_key, db=db)
    if not user:
        # This should theoretically not happen due to JIT creation
        raise HTTPException(status_code=404, detail="User not found for the provided auth key.")

    query_params = dict(request.query_params)
    if not security.validate_telegram_hash(query_params.copy()):
        raise HTTPException(status_code=400, detail="Invalid hash from Telegram")

    login_data = schemas.TelegramLoginData(**query_params)
    
    # Check if this telegram ID is already linked to another user
    existing_telegram_user = await crud.get_user_by_telegram_id(db, login_data.id)
    if existing_telegram_user and existing_telegram_user.id != user.id:
        # This is a complex state - for now, we prevent linking.
        # A more advanced implementation could offer to merge accounts.
        raise HTTPException(status_code=409, detail="This Telegram account is already linked to a different user.")

    await crud.link_telegram_to_user(db, user=user, login_data=login_data)

    # Redirect back to the dashboard
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def public_integrations(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    # Manually get principal, which will create user if needed.
    user = await security.get_auth_principal(x_auth_key=auth_key, db=db)
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    check = await crud.get_check_by_id_and_owner(db, check_id=check_id, principal=user)
    if not check:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    decrypted_token = ""
    if check.telegram_bot_token:
        try:
            decrypted_token = encryption.decrypt_token(check.telegram_bot_token)
        except Exception:
            # If decryption fails (e.g., key changed, old data), treat as empty.
            decrypted_token = ""

    context = {
        "request": request,
        "check": check,
        "auth_key": auth_key,
        "is_admin": False,
        "telegram_bot_token": decrypted_token,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    return templates.TemplateResponse("integrations.html", context)


# --- Admin Routes ---

@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    context = {
        "request": request,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    return templates.TemplateResponse("login.html", context)

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
    
    context = {
        "request": request,
        "api_token": token,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    return templates.TemplateResponse("admin_dashboard.html", context)


@admin_router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def admin_integrations(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
):
    token = request.cookies.get("auth_token")
    if not token:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    # We fetch without owner check; API calls from the page will be authenticated.
    result = await db.execute(select(db_models.Check).filter(db_models.Check.id == check_id))
    check = result.scalars().first()

    if not check:
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)

    decrypted_token = ""
    if check.telegram_bot_token:
        try:
            decrypted_token = encryption.decrypt_token(check.telegram_bot_token)
        except Exception:
            decrypted_token = ""

    context = {
        "request": request,
        "check": check,
        "api_token": token,
        "is_admin": True,
        "telegram_bot_token": decrypted_token,
        "process_time": getattr(request.state, "process_time", 0),
        "redis_connected": request.app.state.redis_connected,
        "debug_mode": settings.DEBUG_MODE,
    }
    return templates.TemplateResponse("integrations.html", context)
