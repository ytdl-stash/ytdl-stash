import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import get_cookie_name, get_password_hash, verify_session_token
from app.config import get_settings
from app.database import init_db
from app.logging_config import setup_logging
from app.scheduler import start_scheduler, stop_scheduler

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Add global function to check if password is set
templates.env.globals["is_password_set"] = lambda: get_password_hash() is not None

# Add global functions for pause state (visible on every page via base.html banner)
from app.download_control import download_control as _dc

templates.env.globals["is_downloads_paused"] = _dc.is_downloads_paused
templates.env.globals["is_channels_paused"] = _dc.is_channels_paused


def status_badge_class(status: str | None) -> str:
    """Return DaisyUI badge class for a video status (used in templates)."""
    if status is None:
        return "badge badge-ghost"
    mapping = {
        "synced": "badge badge-success",
        "pending": "badge badge-warning",
        "cancelling": "badge badge-warning",
        "downloaded": "badge badge-primary",
        "downloading": "badge badge-primary",
        "importing": "badge badge-primary",
        "failed": "badge badge-error",
        "cancelled": "badge badge-ghost",
        "skipped": "badge badge-ghost",
        "imported": "badge badge-secondary",
    }
    return mapping.get(status, "badge badge-ghost")


templates.env.filters["status_badge_class"] = status_badge_class
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async lifespan handler for startup and shutdown events."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level, data_dir=settings.data_dir)
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.download_dir).mkdir(parents=True, exist_ok=True)
    await init_db(settings)
    from app.download_control import load_pause_state_from_db
    await load_pause_state_from_db()
    start_scheduler()
    yield
    # === SHUTDOWN ===
    logger.info("Shutting down...")
    stop_scheduler()
    from app import database as _db  # late import to get current module-level value

    if _db.engine is not None:
        await _db.engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(title="ytdl-stash", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    class AuthMiddleware(BaseHTTPMiddleware):
        _SKIP_PREFIXES = ("/health", "/login", "/logout")
        _STATIC_PREFIX = "/static"

        async def dispatch(self, request, call_next):
            path = request.url.path
            if path.startswith(self._STATIC_PREFIX) or path in self._SKIP_PREFIXES:
                return await call_next(request)
            stored = get_password_hash()
            if stored is None:
                return await call_next(request)
            token = request.cookies.get(get_cookie_name())
            if token and verify_session_token(token, stored):
                return await call_next(request)
            return RedirectResponse(url="/login", status_code=302)

    app.add_middleware(AuthMiddleware)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    f'<span class="error">Not found</span>',
                    status_code=404,
                )
            return templates.TemplateResponse(
                "error.html",
                {
                    "request": request,
                    "title": "Not Found",
                    "message": exc.detail or "The requested resource was not found.",
                },
                status_code=404,
            )
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<span class="error">Error: {html.escape(str(exc.detail))}</span>',
                status_code=exc.status_code,
            )
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "title": "Error",
                "message": str(exc.detail),
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<span class="error">Error: {html.escape(str(exc))}</span>',
                status_code=500,
            )
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "title": "Something went wrong",
                "message": "An unexpected error occurred. Please try again or check the logs.",
            },
            status_code=500,
        )

    from app.routes import (
        auth as auth_routes,
        channels,
        dashboard,
        health as health_routes,
        jobs as jobs_routes,
        logs as logs_routes,
        videos,
        settings as settings_routes,
    )

    app.include_router(health_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(dashboard.router)
    app.include_router(channels.router)
    app.include_router(videos.router)
    app.include_router(jobs_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(logs_routes.router)

    return app


app = create_app()
