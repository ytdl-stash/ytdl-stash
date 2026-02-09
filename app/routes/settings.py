"""Settings routes: settings page, Stash connectivity test, and YTDLM import."""

import html as html_mod
import json
import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import get_version
from app.config import Settings, get_settings
from app.database import get_db
from app.main import templates
from app.stash_client import StashClient
from app.ytdlp_updates import check_for_update, get_status, update_ytdlp
from app.ytdlm_import import IMPORT_FILE_MAX_BYTES, ImportResult, run_import

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


def _ytdlp_env_snippet(settings: Settings) -> str:
    """Return a docker-compose `environment:` YAML snippet for yt-dlp-related config.

    This is a helper for the settings page so users can copy/paste config into
    docker-compose.yml without hunting for variable names.
    """

    def _yaml_line(key: str, value: str | None, default_note: str | None = None) -> str:
        if value is None or value == "":
            note = f"  # {default_note}" if default_note else ""
            return f"# {key}: {json.dumps('')}{note}"
        return f"{key}: {json.dumps(value)}"

    def _yaml_int(key: str, value: int | None, default_note: str | None = None) -> str:
        if value is None:
            note = f"  # {default_note}" if default_note else ""
            return f"# {key}: 0{note}"
        return f"{key}: {value}"

    lines = [
        "# ---- yt-dlp options (ytdl-stash) ----",
        _yaml_line(
            "YTDL_YTDLP_FORMAT",
            settings.ytdlp_format,
            default_note="(optional) yt-dlp format selector, e.g. bestvideo+bestaudio/best",
        ),
        _yaml_line(
            "YTDL_YTDLP_IMPERSONATE",
            settings.ytdlp_impersonate,
            default_note="(optional) yt-dlp impersonate target (varies by yt-dlp version)",
        ),
        _yaml_line(
            "YTDL_YTDLP_USER_AGENT",
            settings.ytdlp_user_agent,
            default_note="(optional) override User-Agent header",
        ),
        _yaml_line(
            "YTDL_YTDLP_REFERER",
            settings.ytdlp_referer,
            default_note="(optional) override Referer header",
        ),
        _yaml_line(
            "YTDL_YTDLP_PROXY",
            settings.ytdlp_proxy,
            default_note="(optional) proxy URL, e.g. socks5://127.0.0.1:9050",
        ),
        _yaml_int(
            "YTDL_YTDLP_SOCKET_TIMEOUT_SECONDS",
            settings.ytdlp_socket_timeout_seconds,
            default_note="(optional) request timeout seconds",
        ),
        _yaml_int(
            "YTDL_YTDLP_RETRIES",
            settings.ytdlp_retries,
            default_note="default: 3",
        ),
        _yaml_int(
            "YTDL_YTDLP_FRAGMENT_RETRIES",
            settings.ytdlp_fragment_retries,
            default_note="default: 3",
        ),
        _yaml_line(
            "YTDL_YTDLP_HTTP_HEADERS_JSON",
            settings.ytdlp_http_headers_json,
            default_note='default: "{}" (JSON object)',
        ),
        _yaml_line(
            "YTDL_YTDLP_SCAN_OPTS_JSON",
            settings.ytdlp_scan_opts_json,
            default_note='default: "{}" (JSON object)',
        ),
        _yaml_line(
            "YTDL_YTDLP_DOWNLOAD_OPTS_JSON",
            settings.ytdlp_download_opts_json,
            default_note='default: "{}" (JSON object)',
        ),
        _yaml_int(
            "YTDL_YTDLP_UPDATE_CHECK_INTERVAL_HOURS",
            settings.ytdlp_update_check_interval_hours,
            default_note="default: 24",
        ),
    ]
    return "\n".join(lines)


@router.get("")
async def settings_page(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Settings page with read-only config display."""
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "ytdlp_env_snippet": _ytdlp_env_snippet(settings),
            "app_version": get_version(),
        },
    )


@router.post("/test-stash")
async def test_stash(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Test Stash connectivity. Returns success or error HTML fragment."""
    try:
        async with StashClient.from_settings(settings) as stash:
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
            f'<span class="error">Error: {html_mod.escape(str(e))}</span>',
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


@router.post("/ytdlp/env")
async def ytdlp_env(
    request: Request,
    settings: Settings = Depends(get_settings),
    # These accept strings so the form can submit empty values. We normalize to
    # Settings types for snippet generation.
    ytdlp_format: str = Form(""),
    ytdlp_impersonate: str = Form(""),
    ytdlp_user_agent: str = Form(""),
    ytdlp_referer: str = Form(""),
    ytdlp_proxy: str = Form(""),
    ytdlp_socket_timeout_seconds: str = Form(""),
    ytdlp_retries: str = Form(""),
    ytdlp_fragment_retries: str = Form(""),
    ytdlp_http_headers_json: str = Form("{}"),
    ytdlp_scan_opts_json: str = Form("{}"),
    ytdlp_download_opts_json: str = Form("{}"),
    ytdlp_update_check_interval_hours: str = Form(""),
):
    """HTMX helper: generate a docker-compose environment snippet for yt-dlp settings."""

    def _none_if_empty(s: str) -> str | None:
        s = (s or "").strip()
        return s if s else None

    def _int_or_none(s: str) -> int | None:
        s = (s or "").strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    tmp = Settings(
        # keep non-yt-dlp fields from current settings so env prefix/other
        # defaults remain consistent
        stash_url=settings.stash_url,
        stash_api_key=settings.stash_api_key,
        download_dir=settings.download_dir,
        stash_download_dir=settings.stash_download_dir,
        data_dir=settings.data_dir,
        default_check_interval_hours=settings.default_check_interval_hours,
        download_delay_seconds=settings.download_delay_seconds,
        cookies_file=settings.cookies_file,
        ytdlp_output_template=settings.ytdlp_output_template,
        log_level=settings.log_level,
        # yt-dlp knobs from the form
        ytdlp_format=_none_if_empty(ytdlp_format),
        ytdlp_impersonate=_none_if_empty(ytdlp_impersonate),
        ytdlp_user_agent=_none_if_empty(ytdlp_user_agent),
        ytdlp_referer=_none_if_empty(ytdlp_referer),
        ytdlp_proxy=_none_if_empty(ytdlp_proxy),
        ytdlp_socket_timeout_seconds=_int_or_none(ytdlp_socket_timeout_seconds),
        ytdlp_retries=_int_or_none(ytdlp_retries) or settings.ytdlp_retries,
        ytdlp_fragment_retries=_int_or_none(ytdlp_fragment_retries)
        or settings.ytdlp_fragment_retries,
        ytdlp_http_headers_json=(ytdlp_http_headers_json or "{}").strip() or "{}",
        ytdlp_scan_opts_json=(ytdlp_scan_opts_json or "{}").strip() or "{}",
        ytdlp_download_opts_json=(ytdlp_download_opts_json or "{}").strip() or "{}",
        ytdlp_update_check_interval_hours=_int_or_none(ytdlp_update_check_interval_hours)
        or settings.ytdlp_update_check_interval_hours,
    )

    return templates.TemplateResponse(
        "settings/_ytdlp_env_snippet.html",
        {"request": request, "snippet": _ytdlp_env_snippet(tmp)},
    )


@router.get("/ytdlp/status")
async def ytdlp_status(request: Request):
    """HTMX partial: show current yt-dlp version + update availability."""
    status = await get_status()
    return templates.TemplateResponse(
        "settings/_ytdlp_update_status.html",
        {"request": request, "status": status},
    )


@router.post("/ytdlp/check")
async def ytdlp_check(request: Request):
    """Check PyPI for latest yt-dlp version and return updated status partial."""
    status = await check_for_update()
    return templates.TemplateResponse(
        "settings/_ytdlp_update_status.html",
        {"request": request, "status": status},
    )


@router.post("/ytdlp/update")
async def ytdlp_update(request: Request):
    """Attempt to self-update yt-dlp in the running container via pip."""
    status = await update_ytdlp()
    return templates.TemplateResponse(
        "settings/_ytdlp_update_status.html",
        {"request": request, "status": status},
    )
