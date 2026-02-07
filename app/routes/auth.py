"""Auth routes: login page and logout. Used when app password is set."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.auth import (
    create_session_token,
    get_cookie_name,
    get_password_hash,
    verify_password,
    verify_session_token,
)
from app.main import templates

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login_get(request: Request):
    """Render login page. Redirect to / if no password is set or already authenticated."""
    stored = get_password_hash()
    if stored is None:
        return RedirectResponse(url="/", status_code=302)
    cookie_name = get_cookie_name()
    token = request.cookies.get(cookie_name)
    if token and verify_session_token(token, stored):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@router.post("/login")
async def login_post(request: Request):
    """Verify password and set session cookie on success; redirect to / or show error."""
    stored = get_password_hash()
    if stored is None:
        return RedirectResponse(url="/", status_code=302)
    form = await request.form()
    password = form.get("password") or ""
    if not verify_password(password, stored):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid password."},
            status_code=401,
        )
    token = create_session_token(stored)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=get_cookie_name(),
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return response


@router.get("/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(get_cookie_name())
    return response
