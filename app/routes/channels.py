"""Channel routes: list, add, update, delete, check-now."""

import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import database as db_module
from app.config import Settings, get_settings
from app.database import get_db
from app.downloader import async_extract_channel_metadata
from app.main import templates
from app.models import Channel
from app.performer_sync import sync_channel_performer
from app.studio_sync import sync_channel_studio
from app.pipeline import process_channel_scan
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])


def _parse_optional_int(value: str | None) -> int | None:
    """Parse a form value to an optional positive int. Returns None for blank/zero/negative."""
    if value is None or value == "":
        return None
    try:
        n = int(value)
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None

# Hold strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()


def _derive_site(url: str) -> str:
    """Extract domain from URL and strip www."""
    parsed = urlparse(url)
    netloc = parsed.netloc or ""
    if netloc.lower().startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0] if netloc else "unknown"


@router.get("")
async def list_channels(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all channels. HTMX can request partial; currently returns full list page."""
    result = await db.execute(
        select(Channel)
        .options(selectinload(Channel.videos))
        .order_by(Channel.name)
    )
    channels = list(result.scalars().all())

    return templates.TemplateResponse(
        "channels/list.html",
        {"request": request, "channels": channels},
    )


async def _load_channels_for_table(db: AsyncSession) -> list[Channel]:
    """Load all channels with relationships needed by table partials."""
    result = await db.execute(
        select(Channel)
        .options(selectinload(Channel.videos))
        .order_by(Channel.name)
    )
    return list(result.scalars().all())


@router.get("/table")
async def channel_table(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: render the read-only channel table."""
    channels = await _load_channels_for_table(db)
    return templates.TemplateResponse(
        "channels/_table.html",
        {"request": request, "channels": channels},
    )


@router.get("/bulk-edit")
async def bulk_edit_channels(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: render the bulk edit form."""
    channels = await _load_channels_for_table(db)
    return templates.TemplateResponse(
        "channels/_bulk_edit.html",
        {"request": request, "channels": channels},
    )


@router.put("/bulk")
async def bulk_update_channels(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple channels from bulk edit form. Returns _table.html partial."""
    form = await request.form()

    # Parse keys like "name__5", "enabled__5" -> {5: {"name": "...", "enabled": "true"}}
    updates: dict[int, dict[str, str]] = {}
    for key, value in form.items():
        if "__" not in key:
            continue
        parts = key.rsplit("__", 1)
        if len(parts) != 2:
            continue
        field_name, id_str = parts
        try:
            channel_id = int(id_str)
        except ValueError:
            continue
        if channel_id not in updates:
            updates[channel_id] = {}
        updates[channel_id][field_name] = value if isinstance(value, str) else str(value)

    for channel_id, data in updates.items():
        channel = await db.get(Channel, channel_id)
        if not channel:
            continue
        if "name" in data:
            name = data["name"].strip()
            if name:
                channel.name = name
        if "enabled" in data:
            channel.enabled = data["enabled"].lower() not in ("false", "0", "off")
        if "check_interval_hours" in data:
            try:
                channel.check_interval_hours = max(1, int(data["check_interval_hours"]))
            except (ValueError, TypeError):
                pass
        if "max_video_age_days" in data:
            channel.max_video_age_days = _parse_optional_int(data["max_video_age_days"])
        if "min_duration_seconds" in data:
            channel.min_duration_seconds = _parse_optional_int(data["min_duration_seconds"])

    channels = await _load_channels_for_table(db)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "channels/_table.html",
            {"request": request, "channels": channels},
        )
    return RedirectResponse(url="/channels", status_code=303)


async def _load_channel_for_row(db: AsyncSession, channel_id: int) -> Channel | None:
    """Load a channel with relationships needed by row templates."""
    result = await db.execute(
        select(Channel)
        .where(Channel.id == channel_id)
        .options(selectinload(Channel.videos))
    )
    return result.scalar_one_or_none()


@router.get("/{channel_id}/row")
async def channel_row(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: render a single channel row."""
    channel = await _load_channel_for_row(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return templates.TemplateResponse(
        "channels/_row.html",
        {"request": request, "channel": channel},
    )


@router.get("/{channel_id}/edit")
async def edit_channel_row(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: render an editable channel row (rename)."""
    channel = await _load_channel_for_row(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return templates.TemplateResponse(
        "channels/_row_edit.html",
        {"request": request, "channel": channel},
    )


@router.get("/add-modal")
async def add_channel_modal_body(
    request: Request,
    url: str = "",
):
    """HTMX partial: Step 1 modal body (URL input). Used when opening modal or clicking Back from step 2."""
    return templates.TemplateResponse(
        "channels/_add_step1.html",
        {"request": request, "url": url, "error_message": None},
    )


@router.get("/add-modal/step2")
async def add_channel_modal_step2(
    request: Request,
    url: str = "",
    name: str = "",
    thumbnail: str = "",
    description: str = "",
    check_interval_hours: int = 6,
    max_video_age_days: str = "",
    min_duration_seconds: str = "",
    settings: Settings = Depends(get_settings),
):
    """HTMX partial: Step 2 modal body (review metadata). Used when clicking Back from step 3."""
    site = _derive_site(url) if url else "unknown"
    return templates.TemplateResponse(
        "channels/_add_step2.html",
        {
            "request": request,
            "url": url,
            "name": name or site,
            "thumbnail": thumbnail or None,
            "description": description or None,
            "site": site,
            "check_interval_hours": check_interval_hours,
            "max_video_age_days": max_video_age_days or None,
            "min_duration_seconds": min_duration_seconds or None,
        },
    )


@router.post("/preview")
async def channel_preview(
    request: Request,
    url: str = Form(...),
    settings: Settings = Depends(get_settings),
):
    """Scrape channel metadata via yt-dlp and return Step 2 partial (review metadata). On error returns Step 1 with error message."""
    url = url.strip()
    if not url:
        return templates.TemplateResponse(
            "channels/_add_step1.html",
            {"request": request, "error_message": "URL is required.", "url": ""},
        )
    site = _derive_site(url)
    try:
        meta = await async_extract_channel_metadata(url, settings)
        name = (meta.get("name") or "").strip() or site
        thumbnail = meta.get("thumbnail")
        description = meta.get("description")
        if description is not None and not isinstance(description, str):
            description = str(description) if description else None
    except Exception as e:
        logger.debug("Channel preview scrape failed for %s: %s", url, e, exc_info=True)
        return templates.TemplateResponse(
            "channels/_add_step1.html",
            {
                "request": request,
                "error_message": f"Could not scrape channel: {e!s}",
                "url": url,
            },
        )
    return templates.TemplateResponse(
        "channels/_add_step2.html",
        {
            "request": request,
            "url": url,
            "name": name,
            "thumbnail": thumbnail,
            "description": description,
            "site": site,
            "check_interval_hours": settings.default_check_interval_hours,
            "max_video_age_days": None,
            "min_duration_seconds": None,
        },
    )


@router.post("/preview/link")
async def channel_preview_link(
    request: Request,
    url: str = Form(...),
    name: str = Form(""),
    thumbnail: str = Form(""),
    description: str = Form(""),
    check_interval_hours: int = Form(6),
    max_video_age_days: str = Form(""),
    min_duration_seconds: str = Form(""),
    settings: Settings = Depends(get_settings),
):
    """Search Stash for performer/studio matches and return Step 3 partial (Stash linking results)."""
    url = url.strip()
    name = name.strip() or _derive_site(url)
    performer_match: dict | None = None
    studio_match: dict | None = None
    stash_error: str | None = None

    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            performer_match = await stash.find_performer_by_url(url)
            if not performer_match:
                performer_match = await stash.find_performer(name)
            studio_match = await stash.find_studio_by_url(url)
            if not studio_match:
                studio_id = await stash.find_studio(name)
                if studio_id:
                    studio_match = await stash.get_studio(studio_id)
    except Exception as e:
        logger.warning("Stash preview link failed: %s", e, exc_info=True)
        stash_error = str(e)

    return templates.TemplateResponse(
        "channels/_add_step3.html",
        {
            "request": request,
            "url": url,
            "name": name,
            "thumbnail": thumbnail,
            "description": description,
            "site": _derive_site(url),
            "check_interval_hours": check_interval_hours,
            "max_video_age_days": max_video_age_days,
            "min_duration_seconds": min_duration_seconds,
            "performer_match": performer_match,
            "studio_match": studio_match,
            "stash_error": stash_error,
            "stash_url": settings.stash_url,
        },
    )


@router.post("")
async def add_channel(
    request: Request,
    url: str = Form(...),
    name: str = Form(""),
    thumbnail_url: str = Form(""),
    check_interval_hours: int | None = Form(None),
    max_video_age_days: str = Form(""),
    min_duration_seconds: str = Form(""),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Add a new channel. Uses pre-scraped name/thumbnail from modal when provided; else extracts via yt-dlp. Returns HTMX partial _row.html or redirect."""
    user_name = name.strip()
    site = _derive_site(url)
    interval = (
        check_interval_hours
        if check_interval_hours is not None
        else settings.default_check_interval_hours
    )
    parsed_max_age = _parse_optional_int(max_video_age_days)
    parsed_min_duration = _parse_optional_int(min_duration_seconds)

    # Use pre-scraped thumbnail from modal when provided; otherwise we may scrape below
    thumb_from_modal = (thumbnail_url or "").strip() or None
    display_name = user_name
    thumbnail_url_final: str | None = thumb_from_modal

    # Scrape only when we don't have both name and thumbnail (e.g. non-modal add or missing data)
    if not display_name or not thumbnail_url_final:
        try:
            meta = await async_extract_channel_metadata(url, settings)
            if not display_name:
                display_name = meta.get("name") or ""
            if not thumbnail_url_final:
                thumbnail_url_final = meta.get("thumbnail")
        except Exception:
            logger.debug("Could not extract channel metadata for %s", url, exc_info=True)
    if not display_name:
        display_name = site

    channel = Channel(
        name=display_name,
        url=url,
        site=site,
        check_interval_hours=interval,
        performer_image_url=thumbnail_url_final,
        max_video_age_days=parsed_max_age,
        min_duration_seconds=parsed_min_duration,
    )
    db.add(channel)
    await db.flush()

    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Stash sync failed for channel %s", channel.id, exc_info=True)

    if request.headers.get("HX-Request"):
        channel = await _load_channel_for_row(db, channel.id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return templates.TemplateResponse(
            "channels/_row.html",
            {"request": request, "channel": channel},
            headers={"HX-Trigger": "closeAddChannelModal"},
        )
    return RedirectResponse(url="/channels", status_code=303)


@router.put("/{channel_id}")
async def update_channel(
    channel_id: int,
    request: Request,
    name: str = Form(""),
    enabled: str = Form("true"),
    check_interval_hours: int = Form(6),
    max_video_age_days: str = Form(""),
    min_duration_seconds: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Update channel. Returns HTMX partial _row.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.name = name.strip() or channel.name
    channel.enabled = enabled.lower() not in ("false", "0", "off")
    channel.check_interval_hours = check_interval_hours
    channel.max_video_age_days = _parse_optional_int(max_video_age_days)
    channel.min_duration_seconds = _parse_optional_int(min_duration_seconds)

    if request.headers.get("HX-Request"):
        channel = await _load_channel_for_row(db, channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return templates.TemplateResponse(
            "channels/_row.html",
            {"request": request, "channel": channel},
        )
    return RedirectResponse(url="/channels", status_code=303)


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete channel and its videos. Returns empty 200 for HTMX or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    await db.delete(channel)

    if request.headers.get("HX-Request"):
        return HTMLResponse(status_code=200)
    return RedirectResponse(url="/channels", status_code=303)


@router.post("/{channel_id}/check-now")
async def check_now(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Trigger immediate channel scan in background. Returns HTMX fragment or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    logger.info("Manual 'Check Now' triggered for channel %s (%s)", channel.id, channel.name)

    async def _run_scan() -> None:
        if db_module.async_session is None:
            logger.error("Background scan aborted for channel %s: database session not initialized", channel_id)
            return
        async with db_module.async_session() as session:
            ch = await session.get(Channel, channel_id)
            if not ch:
                logger.warning("Background scan aborted: channel %s not found in new session", channel_id)
                return
            try:
                logger.info("Background scan starting for channel %s (%s)", ch.id, ch.name)
                await process_channel_scan(ch, session, settings)
                logger.info("Background scan completed for channel %s (%s)", ch.id, ch.name)
            except Exception:
                logger.exception(
                    "Background scan failed for channel %s", channel_id
                )

    task = asyncio.create_task(_run_scan())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    if request.headers.get("HX-Request"):
        return HTMLResponse("<span>Scan started</span>", status_code=200)
    return RedirectResponse(url="/channels", status_code=303)
