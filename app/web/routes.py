import random
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
    """Generates a 16-digit key."""
    return str(random.randint(1000_0000_0000_0000, 9999_9999_9999_9999))

# --- Public Routes ---

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    response = templates.TemplateResponse("public_login.html", {"request": request})
    response.delete_cookie("auth_key")
    return response

@router.post("/dashboard", response_class=HTMLResponse)
async def login_with_key(request: Request, auth_key: str = Form(...)):
    if auth_key and len(auth_key) == 16 and auth_key.isdigit():
        response = RedirectResponse(url=f"/dashboard/{auth_key}", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # 1 year
        return response
    return RedirectResponse(url="/?error=1", status_code=status.HTTP_302_FOUND)

@router.get("/new", response_class=HTMLResponse)
async def new_anonymous_user(request: Request):
    auth_key = generate_auth_key()
    response = RedirectResponse(url=f"/dashboard/{auth_key}", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # 1 year
    return response


@router.get("/dashboard/{auth_key}", response_class=HTMLResponse)
async def public_dashboard(request: Request, auth_key: str):
    if not (len(auth_key) == 16 and auth_key.isdigit()):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    context = {"request": request, "auth_key": auth_key}
    response = templates.TemplateResponse("dashboard.html", context)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60) # Refresh cookie
    return response


@router.get("/telegram/callback", response_class=HTMLResponse, name="telegram_callback")
async def telegram_callback(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
):
    query_params = {k: v for k, v in request.query_params.items() if k not in ['check_id']}
    
    if not security.validate_telegram_hash(query_params.copy()):
        raise HTTPException(status_code=400, detail="Invalid hash from Telegram")

    login_data = schemas.TelegramLoginData(**query_params)
    
    check = await db.get(db_models.Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    await crud.create_or_update_telegram_auth(db, check_id=check_id, login_data=login_data)

    session_token = security.create_telegram_session_token({
        "telegram_user_id": login_data.id,
        "check_id": check_id
    })

    # Determine redirect URL based on whether it was an admin or public user
    if check.owner_id: # Admin-owned check
        redirect_url = f"/admin/check/{check_id}/integrations"
    else: # Public key-owned check
        redirect_url = f"/check/{check_id}/integrations"

    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="telegram_session", value=session_token, httponly=True, samesite="lax")
    return response


@router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def public_integrations(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    telegram_session: dict = Depends(security.get_telegram_session_data)
):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    
    check = await crud.get_check_by_id_and_owner(db, check_id=check_id, principal=auth_key)
    if not check:
        return RedirectResponse(url=f"/dashboard/{auth_key}", status_code=status.HTTP_302_FOUND)

    is_telegram_authed = settings.DEBUG_MODE or bool(
        telegram_session
        and telegram_session.get("check_id") == check_id
        and telegram_session.get("telegram_user_id")
    )

    auth_url = request.url_for('telegram_callback').include_query_params(check_id=check.id)

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
        "telegram_bot_name": settings.TELEGRAM_BOT_NAME,
        "telegram_auth_url": str(auth_url),
        "is_telegram_authed": is_telegram_authed,
        "debug_mode": settings.DEBUG_MODE,
        "telegram_bot_token": decrypted_token,
    }
    return templates.TemplateResponse("integrations.html", context)


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


@admin_router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def admin_integrations(
    request: Request,
    check_id: int,
    db: AsyncSession = Depends(db_base.get_db),
    telegram_session: dict = Depends(security.get_telegram_session_data)
):
    token = request.cookies.get("auth_token")
    if not token:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    # We fetch without owner check; API calls from the page will be authenticated.
    result = await db.execute(select(db_models.Check).filter(db_models.Check.id == check_id))
    check = result.scalars().first()

    if not check:
        return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)

    is_telegram_authed = settings.DEBUG_MODE or bool(
        telegram_session
        and telegram_session.get("check_id") == check_id
        and telegram_session.get("telegram_user_id")
    )

    auth_url = request.url_for('telegram_callback').include_query_params(check_id=check.id)

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
        "telegram_bot_name": settings.TELEGRAM_BOT_NAME,
        "telegram_auth_url": str(auth_url),
        "is_telegram_authed": is_telegram_authed,
        "debug_mode": settings.DEBUG_MODE,
        "telegram_bot_token": decrypted_token,
    }
    return templates.TemplateResponse("integrations.html", context)
