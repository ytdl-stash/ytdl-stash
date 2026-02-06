import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database import engine, init_db
from app.scheduler import start_scheduler, stop_scheduler

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async lifespan handler for startup and shutdown events."""
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.download_dir).mkdir(parents=True, exist_ok=True)
    await init_db(settings)
    start_scheduler()
    yield
    # === SHUTDOWN ===
    logger.info("Shutting down...")
    stop_scheduler()
    if engine is not None:
        await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(title="ytdl-stash", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

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
                f'<span class="error">Error: {exc.detail}</span>',
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
                f'<span class="error">Error: {exc!s}</span>',
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
        dashboard,
        channels,
        health as health_routes,
        performers,
        videos,
        settings as settings_routes,
    )

    app.include_router(health_routes.router)
    app.include_router(dashboard.router)
    app.include_router(channels.router)
    app.include_router(performers.router)
    app.include_router(videos.router)
    app.include_router(settings_routes.router)

    return app


app = create_app()
