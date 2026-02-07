"""Log viewer routes: full page + HTMX partial for auto-refresh."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.responses import FileResponse

from app.config import Settings, get_settings
from app.logging_config import get_memory_handler
from app.main import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
async def logs_page(
    request: Request,
    level: str = Query("", description="Minimum log level filter"),
    search: str = Query("", description="Search text filter"),
    limit: int = Query(200, ge=1, le=2000, description="Max entries to show"),
):
    """Render the log viewer page."""
    handler = get_memory_handler()
    records = handler.get_records(
        limit=limit,
        level=level or None,
        search=search or None,
    )
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "records": records,
            "current_level": level,
            "current_search": search,
            "current_limit": limit,
        },
    )


@router.get("/entries")
async def logs_entries(
    request: Request,
    level: str = Query("", description="Minimum log level filter"),
    search: str = Query("", description="Search text filter"),
    limit: int = Query(200, ge=1, le=2000, description="Max entries to show"),
):
    """HTMX partial: return only the log entries table body for auto-refresh."""
    handler = get_memory_handler()
    records = handler.get_records(
        limit=limit,
        level=level or None,
        search=search or None,
    )
    return templates.TemplateResponse(
        "logs/_entries.html",
        {
            "request": request,
            "records": records,
        },
    )


@router.post("/clear")
async def clear_logs(request: Request):
    """Clear the in-memory log buffer. Returns empty entries partial."""
    handler = get_memory_handler()
    logger.info("In-memory log buffer cleared by user")
    handler.clear()
    return templates.TemplateResponse(
        "logs/_entries.html",
        {
            "request": request,
            "records": [],
        },
    )


@router.get("/download")
async def download_log_file(
    settings: Settings = Depends(get_settings),
):
    """Download the persistent log file (async-safe via FileResponse)."""
    log_path = Path(settings.data_dir) / "ytdl-stash.log"
    if not log_path.exists():
        return PlainTextResponse("No log file found.", status_code=404)
    return FileResponse(
        path=str(log_path),
        filename="ytdl-stash.log",
        media_type="text/plain",
    )
