import secrets
from datetime import timedelta, datetime, timezone
from fastapi import APIRouter, Request, Depends, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, ORJSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import logging
import json
import time

from app.db import base as db_base
from app.db import models as db_models
from app import crud, security, schemas
from app.core.config import settings
from app.core import encryption
from app.core.redis_pool import get_redis_connection, ephemeral_redis

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
        "debug_mode": settings.DEBUG_MODE,
        "telegram_auth_enabled": settings.TELEGRAM_AUTH_ENABLED,
        "telegram_bot_name": settings.TELEGRAM_BOT_NAME,
        "csrf_token": csrf_token,
    }
    response = templates.TemplateResponse("public_login.html", context)
    response.delete_cookie("auth_key")
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
    return response

@router.post("/dashboard", response_class=HTMLResponse, dependencies=[Depends(security.verify_form_csrf_token)])
async def login_with_key(request: Request, auth_key: str = Form(...), db: AsyncSession = Depends(db_base.get_db)):
    # Validate the provided key format before accepting it
    if not security.is_valid_auth_key(auth_key):
        return RedirectResponse(url="/?error=invalid_key", status_code=status.HTTP_302_FOUND)
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax", secure=not settings.DEBUG_MODE)
    return response

@router.get("/new", response_class=HTMLResponse)
async def new_anonymous_user(request: Request, db: AsyncSession = Depends(db_base.get_db)):
    auth_key = generate_auth_key()
    # We no longer create the user here. It will be created just-in-time.
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax", secure=not settings.DEBUG_MODE)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(db_base.get_db)):
    auth_key = request.cookies.get("auth_key")
    if not auth_key:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    if not security.is_valid_auth_key(auth_key):
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.delete_cookie("auth_key")
        return response
    
    user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
    if not user:
        try:
            logger.info(f"Dashboard visit from a new user with auth_key ...{auth_key[-4:]}. Creating user now.")
            user_schema = schemas.UserCreate(auth_key=auth_key)
            user = await crud.create_user(db, user=user_schema)
        except IntegrityError:
            await db.rollback()
            logger.warning(f"Race condition on user creation during dashboard load for auth_key ...{auth_key[-4:]}. Refetching.")
            user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
            if not user:
                raise HTTPException(status_code=500, detail="Failed to create or find user for dashboard.")
        except Exception:
            await db.rollback()
            raise

    status_pages = []
    if user and user.id:
        user = await crud.ensure_user_has_slug(db, user)
        status_pages = await crud.get_status_pages_by_owner(db, principal=user)

    logger.info(f"Dashboard loading. TELEGRAM_AUTH_ENABLED: {settings.TELEGRAM_AUTH_ENABLED}, TELEGRAM_BOT_NAME: '{settings.TELEGRAM_BOT_NAME}'")

    csrf_token = security.generate_csrf_token()
    # No need to check for user existence here. The dashboard will simply show
    # "no checks" if the key is new or invalid. The API calls will handle auth.
    context = {
        "request": request,
        "auth_key": auth_key,
        "user": user,
        "csrf_token": csrf_token,
        "status_pages": status_pages,
        "process_time": getattr(request.state, "process_time", 0),
        "debug_mode": settings.DEBUG_MODE,
        "telegram_auth_enabled": settings.TELEGRAM_AUTH_ENABLED,
        "telegram_bot_name": settings.TELEGRAM_BOT_NAME,
        "user_slug_enabled": settings.USER_SLUG_ENABLED,
    }
    response = templates.TemplateResponse("dashboard.html", context)
    # Refresh cookie on activity
    response.set_cookie(key="auth_key", value=auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax", secure=not settings.DEBUG_MODE)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
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
        # User is new/transient. Create them now before linking.
        try:
            logger.info(f"Telegram callback for a new user with auth_key ...{auth_key[-4:]}. Creating user now.")
            user_schema = schemas.UserCreate(auth_key=auth_key)
            user = await crud.create_user(db, user=user_schema)
        except IntegrityError:
            await db.rollback()
            logger.warning(f"Race condition on user creation during Telegram callback for auth_key ...{auth_key[-4:]}. Refetching.")
            user = await crud.get_user_by_auth_key(db, auth_key=auth_key)
            if not user:
                raise HTTPException(status_code=500, detail="Failed to create or find user during Telegram link.")
        except Exception:
            await db.rollback()
            raise

    user = await crud.ensure_user_has_slug(db, user)
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

    # Ensure the user has a slug for ping URLs
    user = await crud.ensure_user_has_slug(db, user)

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="auth_key", value=user.auth_key, httponly=True, max_age=365*24*60*60, samesite="Lax", secure=not settings.DEBUG_MODE)
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
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("integrations.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
    return response


@router.get("/s/{user_slug}/{page_slug}", response_class=HTMLResponse)
async def public_status_page(
    user_slug: str,
    page_slug: str,
    request: Request,
    db: AsyncSession = Depends(db_base.get_db),
):
    user = await crud.get_user_by_slug(db, slug=user_slug)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    status_page = await crud.get_status_page_by_slug(db, user=user, page_slug=page_slug)
    if not status_page or not status_page.is_public:
        raise HTTPException(status_code=404, detail="Status page not found")

    # Enrich checks with runtime data and ensure status defaults to 'new'
    if status_page.checks:
        await crud.enrich_checks_with_runtime_data(status_page.checks)
        for c in status_page.checks:
            if getattr(c, "status", None) is None:
                c.status = "new"
            # Attach public-safe last pings for initial render
            try:
                lps = getattr(c, "last_pings", None)
                if isinstance(lps, str):
                    lps = json.loads(lps)
                sanitized = []
                if isinstance(lps, list):
                    for lp in lps[:3]:
                        if isinstance(lp, dict):
                            sanitized.append({
                                "timestamp": lp.get("timestamp"),
                                "country_code": lp.get("country_code"),
                                "country_name": lp.get("country_name"),
                                "connection_type": lp.get("connection_type"),
                            })
                setattr(c, "last_pings_public", sanitized)
            except Exception:
                setattr(c, "last_pings_public", [])

    # Calculate overall status
    if not status_page.checks:
        overall_status = "empty"
    elif any((c.status == "down") and not getattr(c, "paused", False) for c in status_page.checks):
        overall_status = "down"
    else:
        overall_status = "up"

    # Selectable public layout for checks: cards | grid | timeline
    layout = request.query_params.get("layout", "cards")
    if layout not in ("cards", "grid", "timeline"):
        layout = "cards"

    context = {
        "request": request,
        "status_page": status_page,
        "overall_status": overall_status,
        "layout": layout,
        "user_slug": user_slug,
        "last_updated_iso": datetime.now(timezone.utc).isoformat(),
        "is_public_status_page": True,
        "process_time": getattr(request.state, "process_time", 0),
        "debug_mode": settings.DEBUG_MODE,
    }
    return templates.TemplateResponse("public_status_page.html", context)

@router.get("/s/{user_slug}/{page_slug}/data")
async def public_status_page_data(
    user_slug: str,
    page_slug: str,
    request: Request,
    db: AsyncSession = Depends(db_base.get_db),
):
    # Single query: join user->status_page and eager load checks + tags to avoid extra DB roundtrips
    stmt = (
        select(db_models.StatusPage)
        .join(db_models.User, db_models.StatusPage.owner_id == db_models.User.id)
        .where(
            db_models.User.slug == user_slug,
            db_models.StatusPage.slug == page_slug,
            db_models.StatusPage.is_public.is_(True),
        )
        .options(
            selectinload(db_models.StatusPage.checks).selectinload(db_models.Check.tags),
            selectinload(db_models.StatusPage.owner),
        )
    )
    result = await db.execute(stmt)
    status_page = result.scalars().first()
    if not status_page:
        raise HTTPException(status_code=404, detail="Status page not found")

    # Weak ETag pre-check based on per-user checks version + page id
    etag = None
    try:
        ver = str(getattr(status_page.owner, "checks_version", 0) or 0)
        # Time-bucket every 2s to reflect runtime changes in ETag
        t_bucket = int(time.time() // 2)
        etag = f'W/"{ver}:{status_page.id}:{t_bucket}"'
        inm = request.headers.get("if-none-match")
        if inm and inm == etag:
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED,
                headers={"ETag": etag, "Cache-Control": "public, max-age=2"}
            )
    except Exception:
        etag = None

    # Enrich runtime data (Redis) and default missing statuses to 'new'
    if status_page.checks:
        await crud.enrich_checks_with_runtime_data(status_page.checks)
        for c in status_page.checks:
            if getattr(c, "status", None) is None:
                c.status = "new"

    # Compute overall
    if not status_page.checks:
        overall_status = "empty"
    elif any((c.status == "down") and not getattr(c, "paused", False) for c in status_page.checks):
        overall_status = "down"
    else:
        overall_status = "up"

    checks_payload = []
    for c in status_page.checks or []:
        checks_payload.append({
            "id": c.id,
            "name": c.name,
            "paused": bool(getattr(c, "paused", False)),
            "status": getattr(c, "status", "new"),
            "last_ping": c.last_ping.isoformat() if getattr(c, "last_ping", None) else None,
            "last_duration_seconds": getattr(c, "last_duration_seconds", None),
            "interval_seconds": getattr(c, "interval_seconds", None),
            "grace_seconds": getattr(c, "grace_seconds", None),
            "schedule_type": getattr(c, "schedule_type", None),
            "schedule": getattr(c, "schedule", None),
            "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
            "tags": [t.name for t in getattr(c, "tags", [])] if getattr(c, "tags", None) else [],
            # Public-safe recent activity (last 3), no IP or User-Agent
            "last_pings": [
                {
                    "timestamp": lp.get("timestamp"),
                    "country_code": lp.get("country_code"),
                    "country_name": lp.get("country_name"),
                    "connection_type": lp.get("connection_type"),
                }
                for lp in (getattr(c, "last_pings", []) or [])[:3]
                if isinstance(lp, dict)
            ],
        })

    payload = {
        "overall_status": overall_status,
        "checks": checks_payload,
    }
    headers = {}
    if etag:
        headers = {"ETag": etag, "Cache-Control": "public, max-age=2"}
    return ORJSONResponse(content=payload, headers=headers)


# --- Admin Routes ---

@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    csrf_token = security.generate_csrf_token()
    context = {
        "request": request,
        "process_time": getattr(request.state, "process_time", 0),
        "debug_mode": settings.DEBUG_MODE,
        "csrf_token": csrf_token,
    }
    response = templates.TemplateResponse("login.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
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
        # Issue short-lived access token (header-only) and long-lived refresh cookie
        access_token = security.create_admin_access_token(user.username, expires_minutes=10)
        refresh_token = security.create_admin_refresh_token(user.username)

        # Set refresh cookie (HttpOnly, Secure, Strict)
        response = HTMLResponse(
            content=f"""
<!doctype html><html><head><meta charset="utf-8"><title>Redirecting…</title></head>
<body>
<script>
// Store access token in sessionStorage and redirect to admin dashboard
try {{
    sessionStorage.setItem('admin_access_token', {json.dumps(access_token)});
}} catch (e) {{}}
window.location.replace('/admin/dashboard');
</script>
<noscript>Login successful. Please enable JavaScript and <a href="/admin/dashboard">continue</a>.</noscript>
</body></html>
""",
            status_code=status.HTTP_200_OK
        )
        response.set_cookie(
            key="admin_refresh",
            value=refresh_token,
            httponly=True,
            secure=not settings.DEBUG_MODE,
            samesite="Strict",
            max_age=settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        )
        # Also ensure a CSRF token cookie exists for refresh endpoint
        csrf_token = security.generate_csrf_token()
        response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
        return response
    return RedirectResponse(url="/admin/login?error=1", status_code=status.HTTP_302_FOUND)

@admin_router.get("/logout", response_class=HTMLResponse)
async def logout(request: Request):
    # Revoke refresh if present
    refresh_token = request.cookies.get("admin_refresh")
    if refresh_token:
        try:
            security.revoke_admin_refresh_token(refresh_token)
        except Exception as e:
            logger.error(f"Failed to revoke admin refresh token during logout: {e}")
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("admin_refresh")
    return response

@admin_router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    # Serve SPA shell; JS will use header-only API with access token from sessionStorage
    csrf_token = security.generate_csrf_token()
    context = {
        "request": request,
        "csrf_token": csrf_token,
        "status_pages": [],  # Loaded via API
        "process_time": getattr(request.state, "process_time", 0),
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("admin_dashboard.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
    return response


@admin_router.get("/check/{check_id}/integrations", response_class=HTMLResponse)
async def admin_integrations(
    request: Request,
    check_id: int,
):
    # Serve SPA shell; JS will load check details via API
    csrf_token = security.generate_csrf_token()
    # Minimal context with check placeholder to keep templates stable
    check_placeholder = {"id": check_id, "name": ""}
    context = {
        "request": request,
        "check": check_placeholder,
        "csrf_token": csrf_token,
        "status_pages": [],  # Loaded via API
        "is_admin": True,
        "telegram_bot_token": "",
        "has_telegram_bot_token": False,
        "telegram_bot_token_placeholder": "Enter your Telegram bot token",
        "process_time": getattr(request.state, "process_time", 0),
        "debug_mode": settings.DEBUG_MODE,
    }
    response = templates.TemplateResponse("integrations.html", context)
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="Lax", secure=not settings.DEBUG_MODE)
    return response
