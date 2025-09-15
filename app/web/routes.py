from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import base as db_base
from app import crud, security

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login", response_class=HTMLResponse)
async def handle_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(db_base.get_db),
):
    user = await crud.get_user_by_username(db, username=username)
    if user and security.verify_password(password, user.hashed_password):
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="auth_token", value="dummy_token_for_admin", httponly=True)
        return response
    return RedirectResponse(url="/login?error=1", status_code=status.HTTP_302_FOUND)

@router.get("/logout", response_class=HTMLResponse)
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("auth_token")
    return response

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    token = request.cookies.get("auth_token")
    if not token:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    return templates.TemplateResponse("dashboard.html", {"request": request})
