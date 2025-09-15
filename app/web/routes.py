from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login", response_class=HTMLResponse)
async def handle_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # This is a simplified web login. It sets a dummy cookie to protect the UI route.
    # The actual API authentication is handled by a JWT token fetched by the frontend JS.
    # In a real app, you'd want a more robust session management system.
    # For this project, we assume a single 'admin' user with a hardcoded password for UI access.
    if username == "admin" and password == "password":
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
