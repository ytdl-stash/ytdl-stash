"""Channel routes: list (card grid), detail, add, update, delete, sync, check-now."""

import asyncio
import logging
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import database as db_module
from app.config import Settings, get_settings
from app.database import get_db
from app.download_progress import download_progress
from app.downloader import async_extract_channel_metadata
from app.main import templates
from app.models import Channel
from app.performer_sync import sync_channel_performer
from app.pipeline import process_channel_scan
from app.studio_sync import sync_channel_studio
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])

ACTIVE_DOWNLOAD_STATUSES = ("downloading", "cancelling", "downloaded", "importing")


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


async def _load_channels_with_videos(db: AsyncSession) -> list[Channel]:
    """Load all channels with videos (for list, bulk edit)."""
    result = await db.execute(
        select(Channel)
        .options(selectinload(Channel.videos))
        .order_by(Channel.name)
    )
    return list(result.scalars().all())


async def _load_channel_with_videos(db: AsyncSession, channel_id: int) -> Channel | None:
    """Load a single channel with videos (for card/detail)."""
    result = await db.execute(
        select(Channel)
        .where(Channel.id == channel_id)
        .options(selectinload(Channel.videos))
    )
    return result.scalar_one_or_none()


# ----- List (card grid with filter/sort) -----

@router.get("")
async def list_channels(
    request: Request,
    filter: str = "all",
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """List all channels as cards. filter: all|watched|not_watched, sort: name|video_count|last_checked."""
    stmt = (
        select(Channel)
        .options(selectinload(Channel.videos))
        .order_by(Channel.name)
    )
    if filter == "watched":
        stmt = stmt.where(Channel.enabled.is_(True))
    elif filter == "not_watched":
        stmt = stmt.where(Channel.enabled.is_(False))

    result = await db.execute(stmt)
    channels = list(result.scalars().all())

    if sort == "video_count":
        channels.sort(key=lambda c: len(c.videos), reverse=True)
    elif sort == "last_checked":
        _min_dt = datetime.min.replace(tzinfo=timezone.utc)
        channels.sort(
            key=lambda c: c.last_checked_at or _min_dt,
            reverse=True,
        )

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "channels/_list_content.html",
            {
                "request": request,
                "channels": channels,
                "filter": filter,
                "sort": sort,
                "settings": settings,
            },
        )
    return templates.TemplateResponse(
        "channels/list.html",
        {
            "request": request,
            "channels": channels,
            "filter": filter,
            "sort": sort,
            "settings": settings,
        },
    )


# ----- Bulk edit -----

@router.get("/bulk-edit")
async def bulk_edit_channels(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: render the bulk edit form."""
    channels = await _load_channels_with_videos(db)
    return templates.TemplateResponse(
        "channels/_bulk_edit.html",
        {"request": request, "channels": channels},
    )


@router.put("/bulk")
async def bulk_update_channels(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Update multiple channels from bulk edit form. Returns _list_content.html partial."""
    form = await request.form()

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

    channels = await _load_channels_with_videos(db)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "channels/_list_content.html",
            {
                "request": request,
                "channels": channels,
                "filter": "all",
                "sort": "name",
                "settings": settings,
            },
        )
    return RedirectResponse(url="/channels", status_code=303)


# ----- Add channel wizard -----

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
    create_performer: str = Form(""),
    create_studio: str = Form(""),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Add a new channel. Returns HTMX partial _card.html (append to grid) or redirect."""
    user_name = name.strip()
    site = _derive_site(url)
    interval = (
        check_interval_hours
        if check_interval_hours is not None
        else settings.default_check_interval_hours
    )
    parsed_max_age = _parse_optional_int(max_video_age_days)
    parsed_min_duration = _parse_optional_int(min_duration_seconds)

    thumb_from_modal = (thumbnail_url or "").strip() or None
    display_name = user_name
    thumbnail_url_final: str | None = thumb_from_modal

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

    want_performer = bool(create_performer)
    want_studio = bool(create_studio)

    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            if want_performer:
                await sync_channel_performer(channel, db, stash, settings)
                # If performer was just created/linked, scrape it via Stash
                # scrapers and re-sync so we pull enriched metadata immediately.
                if channel.stash_performer_id:
                    await _scrape_and_resync_performer(channel, stash, db, settings)
            if want_studio:
                await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Stash sync failed for channel %s", channel.id, exc_info=True)

    if request.headers.get("HX-Request"):
        channel = await _load_channel_with_videos(db, channel.id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return templates.TemplateResponse(
            "channels/_card.html",
            {"request": request, "channel": channel, "settings": settings},
            headers={"HX-Trigger": "closeAddChannelModal"},
        )
    return RedirectResponse(url="/channels", status_code=303)


async def _scrape_and_resync_performer(
    channel: Channel,
    stash: StashClient,
    db: AsyncSession,
    settings: Settings,
) -> None:
    """Scrape performer URL via Stash scrapers, apply data, then re-sync.

    Non-fatal: logs warnings on failure but never raises.
    """
    if not channel.stash_performer_id:
        return
    try:
        scraped = await stash.scrape_performer_url(channel.url)
        if scraped:
            await stash.apply_scraped_performer(channel.stash_performer_id, scraped)
            # Re-sync so local cache reflects the scraped data
            await sync_channel_performer(channel, db, stash, settings)
    except Exception:
        logger.warning(
            "Performer scrape+resync failed for channel %s (performer %s)",
            channel.id,
            channel.stash_performer_id,
            exc_info=True,
        )


# ----- Detail -----

@router.get("/{channel_id}/active_downloads")
async def channel_active_downloads(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """HTMX partial: active downloads for this channel only."""
    channel = await _load_channel_with_videos(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    active_videos = [v for v in channel.videos if v.status in ACTIVE_DOWNLOAD_STATUSES]
    return templates.TemplateResponse(
        "videos/_active_downloads.html",
        {
            "request": request,
            "active_videos": active_videos,
            "download_progress": download_progress.snapshot(),
            "settings": settings,
            "poll_url": f"/channels/{channel_id}/active_downloads",
            "container_id": f"channel-active-downloads-{channel_id}",
        },
    )


@router.get("/{channel_id}/videos")
async def channel_videos(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """HTMX partial: video table for this channel (for polling refresh)."""
    channel = await _load_channel_with_videos(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    videos = sorted(
        channel.videos,
        key=lambda v: v.upload_date or date.min,
        reverse=True,
    )
    return templates.TemplateResponse(
        "channels/_channel_videos.html",
        {
            "request": request,
            "channel": channel,
            "videos": videos,
            "settings": settings,
            "download_progress": download_progress.snapshot(),
        },
    )


@router.get("/{channel_id}")
async def channel_detail(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Channel detail: metadata, Stash link status, video table."""
    channel = await _load_channel_with_videos(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    videos = sorted(
        channel.videos,
        key=lambda v: v.upload_date or date.min,
        reverse=True,
    )
    active_videos = [v for v in channel.videos if v.status in ACTIVE_DOWNLOAD_STATUSES]
    return templates.TemplateResponse(
        "channels/detail.html",
        {
            "request": request,
            "channel": channel,
            "videos": videos,
            "active_videos": active_videos,
            "stash_url": settings.stash_url.rstrip("/"),
            "settings": settings,
            "download_progress": download_progress.snapshot(),
        },
    )


async def _channel_sync_response(
    channel_id: int,
    request: Request,
    db: AsyncSession,
    settings: Settings,
) -> Response:
    """Reload channel and return card (HTMX) or redirect to detail."""
    channel = await _load_channel_with_videos(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if request.headers.get("HX-Request"):
        hx_target = request.headers.get("HX-Target") or ""
        if "channel-detail-card" in hx_target:
            videos = sorted(
                channel.videos,
                key=lambda v: v.upload_date or date.min,
                reverse=True,
            )
            active_videos = [v for v in channel.videos if v.status in ACTIVE_DOWNLOAD_STATUSES]
            return templates.TemplateResponse(
                "channels/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "active_videos": active_videos,
                    "stash_url": settings.stash_url.rstrip("/"),
                    "settings": settings,
                    "download_progress": download_progress.snapshot(),
                },
            )
        return templates.TemplateResponse(
            "channels/_card.html",
            {"request": request, "channel": channel, "settings": settings},
        )
    return RedirectResponse(url=f"/channels/{channel_id}", status_code=303)


@router.post("/{channel_id}/sync-performer")
async def channel_sync_performer(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger performer sync only. Returns updated card or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
    except Exception:
        logger.warning("Performer sync failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/sync-studio")
async def channel_sync_studio(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger studio sync only. Returns updated card or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Studio sync failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/sync")
async def channel_sync_both(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger performer and studio sync. Returns updated card or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Performer/studio sync failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/relink")
async def channel_relink(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Clear Stash links and re-lookup performer/studio by channel URL in Stash."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel.stash_performer_id = None
    channel.stash_performer_data = None
    channel.stash_studio_id = None
    channel.stash_studio_data = None
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Re-link failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/toggle")
async def channel_toggle(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Toggle channel enabled (watch/unwatch). Returns updated card or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel.enabled = not channel.enabled

    if request.headers.get("HX-Request"):
        hx_target = request.headers.get("HX-Target") or ""
        channel = await _load_channel_with_videos(db, channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        if "channel-detail-card" in hx_target:
            videos = sorted(
                channel.videos,
                key=lambda v: v.upload_date or date.min,
                reverse=True,
            )
            active_videos = [v for v in channel.videos if v.status in ACTIVE_DOWNLOAD_STATUSES]
            return templates.TemplateResponse(
                "channels/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "active_videos": active_videos,
                    "stash_url": settings.stash_url.rstrip("/"),
                    "settings": settings,
                    "download_progress": download_progress.snapshot(),
                },
            )
        return templates.TemplateResponse(
            "channels/_card.html",
            {"request": request, "channel": channel, "settings": settings},
        )
    return RedirectResponse(url="/channels", status_code=303)


# ----- Update channel -----

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
    settings: Settings = Depends(get_settings),
):
    """Update channel. Returns HTMX partial _detail_card.html or _card.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.name = name.strip() or channel.name
    channel.enabled = enabled.lower() not in ("false", "0", "off")
    channel.check_interval_hours = check_interval_hours
    channel.max_video_age_days = _parse_optional_int(max_video_age_days)
    channel.min_duration_seconds = _parse_optional_int(min_duration_seconds)

    if request.headers.get("HX-Request"):
        channel = await _load_channel_with_videos(db, channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        hx_target = request.headers.get("HX-Target") or ""
        if "channel-detail-card" in hx_target:
            videos = sorted(
                channel.videos,
                key=lambda v: v.upload_date or date.min,
                reverse=True,
            )
            active_videos = [v for v in channel.videos if v.status in ACTIVE_DOWNLOAD_STATUSES]
            return templates.TemplateResponse(
                "channels/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "active_videos": active_videos,
                    "stash_url": settings.stash_url.rstrip("/"),
                    "settings": settings,
                    "download_progress": download_progress.snapshot(),
                },
            )
        return templates.TemplateResponse(
            "channels/_card.html",
            {"request": request, "channel": channel, "settings": settings},
        )
    return RedirectResponse(url="/channels", status_code=303)


# ----- Delete -----

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


# ----- Check now -----

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
