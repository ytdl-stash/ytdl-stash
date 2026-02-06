"""Settings routes: settings page, Stash connectivity test, and YTDLM import."""

import json
import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.main import templates
from app.stash_client import StashClient
from app.ytdlm_import import IMPORT_FILE_MAX_BYTES, ImportResult, run_import

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def settings_page(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Settings page with read-only config display."""
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings},
    )


@router.post("/test-stash")
async def test_stash(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Test Stash connectivity. Returns success or error HTML fragment."""
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            ok = await stash.health_check()
        if ok:
            return HTMLResponse(
                '<span class="success">Stash connection OK</span>',
                status_code=200,
            )
        return HTMLResponse(
            '<span class="error">Stash returned non-OK status</span>',
            status_code=502,
        )
    except Exception as e:
        logger.warning("Stash test failed: %s", e)
        return HTMLResponse(
            f'<span class="error">Error: {e!s}</span>',
            status_code=502,
        )


@router.post("/import")
async def import_ytdlm(
    request: Request,
    file: UploadFile = File(...),
    dry_run: str = Form("false"),
    include_playlists: str = Form("false"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Import channels and videos from YoutubeDL-Material local_db.json. Returns HTMX partial with results."""
    result = ImportResult(dry_run=(dry_run == "true"))
    if not file.filename or not file.filename.lower().endswith(".json"):
        result.errors.append("Please upload a JSON file (e.g. local_db.json).")
        return templates.TemplateResponse(
            "settings/_import_results.html",
            {"request": request, "result": result},
        )
    content = b""
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            content += chunk
            if len(content) > IMPORT_FILE_MAX_BYTES:
                result.errors.append(f"File exceeds {IMPORT_FILE_MAX_BYTES // (1024*1024)} MB limit.")
                return templates.TemplateResponse(
                    "settings/_import_results.html",
                    {"request": request, "result": result},
                )
    except Exception as e:
        result.errors.append(f"Failed to read file: {e!s}")
        return templates.TemplateResponse(
            "settings/_import_results.html",
            {"request": request, "result": result},
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        result.errors.append("File is not valid UTF-8. Save local_db.json as UTF-8 and try again.")
        return templates.TemplateResponse(
            "settings/_import_results.html",
            {"request": request, "result": result},
        )
    try:
        json_data = json.loads(text)
    except json.JSONDecodeError as e:
        result.errors.append(f"Invalid JSON: {e!s}")
        return templates.TemplateResponse(
            "settings/_import_results.html",
            {"request": request, "result": result},
        )
    if not isinstance(json_data, dict):
        result.errors.append("JSON root must be an object.")
        return templates.TemplateResponse(
            "settings/_import_results.html",
            {"request": request, "result": result},
        )
    result = await run_import(
        db,
        json_data,
        settings,
        dry_run=(dry_run == "true"),
        include_playlists=(include_playlists == "true"),
    )
    return templates.TemplateResponse(
        "settings/_import_results.html",
        {"request": request, "result": result},
    )
