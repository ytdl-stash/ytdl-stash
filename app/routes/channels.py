"""Channel routes: list (card grid), detail, add, update, delete, sync, check-now."""

import asyncio
import logging
from datetime import UTC, date, datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import database as db_module
from app.config import Settings, get_settings
from app.database import get_db
from app.download_control import download_control
from app.download_progress import download_progress
from app.downloader import async_extract_channel_metadata, normalize_channel_url
from app.main import templates
from app.models import Channel, Video
from app.performer_sync import sync_channel_performer
from app.pipeline import process_channel_scan
from app.studio_sync import sync_channel_studio
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])

ACTIVE_DOWNLOAD_STATUSES = ("downloading", "cancelling", "downloaded", "importing")


def _active_panel_filter(videos):
    """Videos for the active-downloads panel: transitional status OR a live
    pipeline phase (so synced-but-still-generating videos stay visible)."""
    phase_ids = download_progress.video_ids_with_phase()
    return [
        v for v in videos
        if v.status in ACTIVE_DOWNLOAD_STATUSES or v.id in phase_ids
    ]


CHANNEL_VIDEO_SORT_OPTIONS = {
    "created_at_desc": lambda: desc(Video.created_at),
    "upload_date_desc": lambda: desc(Video.upload_date),
    "upload_date_asc": lambda: asc(Video.upload_date),
    "title_asc": lambda: asc(Video.title),
    "title_desc": lambda: desc(Video.title),
    "duration_desc": lambda: desc(Video.duration_seconds),
    "duration_asc": lambda: asc(Video.duration_seconds),
    "status_asc": lambda: asc(Video.status),
}
CHANNEL_VIDEO_SORT_DEFAULT = "upload_date_desc"


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
    search: str = "",
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """List all channels as cards. filter: all|watched|not_watched, sort: name|video_count|last_checked, search: name substring."""
    stmt = (
        select(Channel)
        .options(selectinload(Channel.videos))
        .order_by(Channel.name)
    )
    if filter == "watched":
        stmt = stmt.where(Channel.enabled.is_(True))
    elif filter == "not_watched":
        stmt = stmt.where(Channel.enabled.is_(False))

    search = search.strip()
    if search:
        # Escape SQL LIKE wildcards so %, _ are matched literally
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        stmt = stmt.where(Channel.name.ilike(f"%{escaped}%", escape="\\"))

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

    ctx = {
        "request": request,
        "channels": channels,
        "filter": filter,
        "sort": sort,
        "search": search,
        "settings": settings,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "channels/_list_content.html", ctx,
        )
    return templates.TemplateResponse(
        "channels/list.html", ctx,
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
                "search": "",
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
    url = normalize_channel_url(url.strip())
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
        video_count = meta.get("video_count", 0)
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
            "video_count": video_count,
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
    url = normalize_channel_url(url.strip())
    name = name.strip() or _derive_site(url)
    performer_match: dict | None = None
    studio_match: dict | None = None
    stash_error: str | None = None

    try:
        async with StashClient.from_settings(settings) as stash:
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
    """Add a new channel. Stash performer/studio sync runs in background.

    Returns HTMX partial _card.html (append to grid) or redirect.
    """
    url = normalize_channel_url(url.strip())
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

    logger.info(
        "add_channel id=%s: create_performer=%r (want=%s), create_studio=%r (want=%s), "
        "name=%r, thumbnail_url=%s, performer_image_url=%s",
        channel.id, create_performer, want_performer,
        create_studio, want_studio,
        display_name,
        "yes" if thumb_from_modal else "no",
        "yes" if thumbnail_url_final else "no",
    )

    # Commit the channel to DB now so the background task can see it
    # in its own session.  The get_db dependency will commit again at
    # cleanup (harmless no-op if nothing else changed).
    await db.commit()

    # Kick off Stash sync (performer/studio) in background so the modal
    # closes immediately and the user isn't left waiting.
    channel_id = channel.id
    if want_performer or want_studio:
        async def _bg_stash_sync() -> None:
            if db_module.async_session is None:
                logger.error(
                    "Background Stash sync aborted for channel %s: database session not initialized",
                    channel_id,
                )
                return
            async with db_module.async_session() as session:
                ch = await session.get(Channel, channel_id)
                if not ch:
                    logger.warning(
                        "Background Stash sync aborted: channel %s not found",
                        channel_id,
                    )
                    return
                try:
                    async with StashClient.from_settings(settings) as stash:
                        if want_performer:
                            await sync_channel_performer(ch, session, stash, settings)
                            logger.info(
                                "add_channel bg id=%s: after performer sync — stash_performer_id=%s",
                                ch.id, ch.stash_performer_id,
                            )
                            if ch.stash_performer_id:
                                await _scrape_and_resync_performer(ch, stash, session, settings)
                            else:
                                logger.info(
                                    "add_channel bg id=%s: skipping performer scrape — no stash_performer_id",
                                    ch.id,
                                )
                        if want_studio:
                            await sync_channel_studio(ch, session, stash, settings)
                            logger.info(
                                "add_channel bg id=%s: after studio sync — stash_studio_id=%s",
                                ch.id, ch.stash_studio_id,
                            )
                    await session.commit()
                except Exception:
                    logger.warning(
                        "Background Stash sync failed for channel %s",
                        channel_id,
                        exc_info=True,
                    )

        task = asyncio.create_task(_bg_stash_sync())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

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
        logger.info(
            "Scraping performer for channel %s (performer_id=%s, url=%s)",
            channel.id, channel.stash_performer_id, channel.url,
        )
        scraped = await stash.scrape_performer_url(channel.url)
        if scraped:
            logger.info(
                "Scrape returned data for channel %s — applying to performer %s",
                channel.id, channel.stash_performer_id,
            )
            await stash.apply_scraped_performer(channel.stash_performer_id, scraped)
            # Re-sync so local cache reflects the scraped data
            await sync_channel_performer(channel, db, stash, settings)
        else:
            logger.info(
                "Scrape returned no data for channel %s (url=%s)",
                channel.id, channel.url,
            )
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
    active_videos = _active_panel_filter(channel.videos)
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
    search: str = "",
    sort: str = CHANNEL_VIDEO_SORT_DEFAULT,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """HTMX partial: video table for this channel (for polling refresh)."""
    ch_result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = ch_result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    sort_clean = sort.strip() if sort else CHANNEL_VIDEO_SORT_DEFAULT
    if sort_clean not in CHANNEL_VIDEO_SORT_OPTIONS:
        sort_clean = CHANNEL_VIDEO_SORT_DEFAULT

    stmt = (
        select(Video)
        .where(Video.channel_id == channel_id)
        .order_by(CHANNEL_VIDEO_SORT_OPTIONS[sort_clean]())
    )
    search_clean = search.strip()
    if search_clean:
        escaped = search_clean.replace("%", r"\%").replace("_", r"\_")
        stmt = stmt.where(Video.title.ilike(f"%{escaped}%", escape="\\"))

    result = await db.execute(stmt)
    videos = list(result.scalars().all())

    return templates.TemplateResponse(
        "channels/_channel_videos.html",
        {
            "request": request,
            "channel": channel,
            "videos": videos,
            "search": search_clean,
            "sort": sort_clean,
            "settings": settings,
            "download_progress": download_progress.snapshot(),
        },
    )


@router.get("/{channel_id}")
async def channel_detail(
    channel_id: int,
    request: Request,
    sort: str = CHANNEL_VIDEO_SORT_DEFAULT,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Channel detail: metadata, Stash link status, video table."""
    channel = await _load_channel_with_videos(db, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    sort_clean = sort.strip() if sort else CHANNEL_VIDEO_SORT_DEFAULT
    if sort_clean not in CHANNEL_VIDEO_SORT_OPTIONS:
        sort_clean = CHANNEL_VIDEO_SORT_DEFAULT

    stmt = (
        select(Video)
        .where(Video.channel_id == channel_id)
        .order_by(CHANNEL_VIDEO_SORT_OPTIONS[sort_clean]())
    )
    result = await db.execute(stmt)
    videos = list(result.scalars().all())

    active_videos = _active_panel_filter(channel.videos)
    return templates.TemplateResponse(
        "channels/detail.html",
        {
            "request": request,
            "channel": channel,
            "videos": videos,
            "active_videos": active_videos,
            "search": "",
            "sort": sort_clean,
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
            active_videos = _active_panel_filter(channel.videos)
            return templates.TemplateResponse(
                "channels/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "active_videos": active_videos,
                    "search": "",
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
        async with StashClient.from_settings(settings) as stash:
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
        async with StashClient.from_settings(settings) as stash:
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
        async with StashClient.from_settings(settings) as stash:
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
        async with StashClient.from_settings(settings) as stash:
            await sync_channel_performer(channel, db, stash, settings)
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Re-link failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/relink-performer")
async def channel_relink_performer(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Clear performer Stash link and re-lookup by channel URL."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel.stash_performer_id = None
    channel.stash_performer_data = None
    channel.performer_image_url = None
    try:
        async with StashClient.from_settings(settings) as stash:
            await sync_channel_performer(channel, db, stash, settings)
    except Exception:
        logger.warning("Re-link performer failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/relink-studio")
async def channel_relink_studio(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Clear studio Stash link and re-lookup by channel URL."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel.stash_studio_id = None
    channel.stash_studio_data = None
    try:
        async with StashClient.from_settings(settings) as stash:
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Re-link studio failed for channel %s", channel_id, exc_info=True)
    return await _channel_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/resync-videos")
async def channel_resync_videos(
    channel_id: int,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    """Re-sync all synced videos for this channel (scrape + generate) as a background task."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video.id).where(
            Video.stash_scene_id.isnot(None),
            Video.channel_id == channel_id,
        )
    )
    video_ids = [row[0] for row in result.all()]

    if not video_ids:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<span class="text-warning text-sm">No synced videos to re-sync</span>',
                status_code=200,
            )
        return RedirectResponse(url=f"/channels/{channel_id}", status_code=303)

    async def _run_resync() -> None:
        succeeded = 0
        failed = 0
        for vid in video_ids:
            try:
                async with db_module.async_session() as session:
                    video = await session.get(Video, vid)
                    if not video or not video.stash_scene_id:
                        continue
                    async with StashClient.from_settings(settings) as stash:
                        scene = await stash.find_scene_by_id(video.stash_scene_id)
                        if not scene:
                            logger.warning(
                                "Channel resync: scene %s not found for video %s, skipping",
                                video.stash_scene_id, vid,
                            )
                            failed += 1
                            continue
                        try:
                            scraped = await stash.scrape_scene_url(video.url)
                            if scraped:
                                await stash.apply_scraped_scene(
                                    scene_id=video.stash_scene_id,
                                    scraped=scraped,
                                )
                            video.scrape_attempted_at = datetime.now(UTC)
                        except Exception as e:
                            logger.warning("Channel resync: scrape failed for video %s: %s", vid, e)
                        if settings.stash_generate_after_sync:
                            try:
                                job_id = await stash.trigger_generate(
                                    scene_ids=[video.stash_scene_id],
                                    covers=settings.stash_generate_covers,
                                    previews=settings.stash_generate_previews,
                                    sprites=settings.stash_generate_sprites,
                                    phashes=settings.stash_generate_phashes,
                                )
                                if job_id:
                                    await stash.wait_for_job(job_id)
                                video.generate_triggered_at = datetime.now(UTC)
                            except Exception as e:
                                logger.warning("Channel resync: generate failed for video %s: %s", vid, e)
                    await session.commit()
                    succeeded += 1
                    logger.info("Channel resync: video %s complete", vid)
            except Exception:
                logger.exception("Channel resync: unexpected error for video %s", vid)
                failed += 1
        logger.info(
            "Channel %s resync finished: %d succeeded, %d failed out of %d total",
            channel_id, succeeded, failed, len(video_ids),
        )

    task = asyncio.create_task(_run_resync())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info("Channel %s resync started for %d videos", channel_id, len(video_ids))

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<span class="text-success text-sm">Re-syncing {len(video_ids)} video(s) in background…</span>',
            status_code=200,
        )
    return RedirectResponse(url=f"/channels/{channel_id}", status_code=303)


@router.post("/{channel_id}/retry-skipped")
async def channel_retry_skipped(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reset skipped videos for this channel back to pending/downloaded."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video).where(
            Video.status == "skipped",
            Video.channel_id == channel_id,
        )
    )
    videos = list(result.scalars().all())

    if not videos:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<span class="text-warning text-sm">No skipped videos to retry</span>',
                status_code=200,
            )
        return RedirectResponse(url=f"/channels/{channel_id}", status_code=303)

    for video in videos:
        if video.oshash or video.original_filename:
            video.status = "downloaded"
        else:
            video.status = "pending"
        video.error_message = None
    count = len(videos)

    logger.info("Channel %s retry-skipped: re-queued %d video(s)", channel_id, count)

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<span class="text-success text-sm">Re-queued {count} skipped video(s) for processing</span>',
            status_code=200,
        )
    return RedirectResponse(url=f"/channels/{channel_id}", status_code=303)


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
            active_videos = _active_panel_filter(channel.videos)
            return templates.TemplateResponse(
                "channels/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "active_videos": active_videos,
                    "search": "",
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
            active_videos = _active_panel_filter(channel.videos)
            return templates.TemplateResponse(
                "channels/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "active_videos": active_videos,
                    "search": "",
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
    """Delete channel and its videos. Cancels any active downloads first."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Cancel any in-flight downloads for this channel's videos before deleting
    active_ids = download_control.get_active_ids()
    if active_ids:
        result = await db.execute(
            select(Video.id).where(
                Video.channel_id == channel_id,
                Video.id.in_(active_ids),
            )
        )
        for video_id in result.scalars().all():
            download_control.request_cancel(video_id)
            logger.info(
                "Cancelling active download for video %s (channel %s deleted)",
                video_id,
                channel_id,
            )

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
