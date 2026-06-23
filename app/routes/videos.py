"""Video routes: list with filters, detail, retry, delete, resync."""

import asyncio
import logging
import math
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import database as db_module
from app.config import Settings, get_settings
from app.database import get_db
from app.download_control import download_control
from app.download_progress import download_progress
from app.main import templates
from app.models import Channel, Video
from app.pipeline import generate_for_scene, hard_reset_video
from app.stash_client import StashClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])

ACTIVE_DOWNLOAD_STATUSES = ("downloading", "cancelling", "downloaded", "importing")
REDOWNLOAD_ALLOWED_STATUSES = frozenset(
    {"imported", "synced", "failed", "cancelled", "skipped"}
)


def _active_panel_condition():
    """SQL filter for the active-downloads panel: videos in a transitional
    status, OR any with a live pipeline phase (e.g. synced-but-still-generating)
    so post-sync work stays visible instead of vanishing when status flips."""
    phase_ids = download_progress.video_ids_with_phase()
    cond = Video.status.in_(ACTIVE_DOWNLOAD_STATUSES)
    return or_(cond, Video.id.in_(phase_ids)) if phase_ids else cond


def _active_panel_filter(videos):
    """In-memory equivalent of _active_panel_condition for loaded video lists."""
    phase_ids = download_progress.video_ids_with_phase()
    return [
        v for v in videos
        if v.status in ACTIVE_DOWNLOAD_STATUSES or v.id in phase_ids
    ]

VIDEO_SORT_OPTIONS = {
    "created_at_desc": lambda: desc(Video.created_at),
    "upload_date_desc": lambda: desc(Video.upload_date),
    "upload_date_asc": lambda: asc(Video.upload_date),
    "title_asc": lambda: asc(Video.title),
    "title_desc": lambda: desc(Video.title),
    "duration_desc": lambda: desc(Video.duration_seconds),
    "duration_asc": lambda: asc(Video.duration_seconds),
    "channel_asc": lambda: asc(Channel.name),
    "status_asc": lambda: asc(Video.status),
}
VIDEO_SORT_DEFAULT = "created_at_desc"

# Hold strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()


@router.get("")
async def list_videos(
    request: Request,
    channel_id: str | None = None,
    status: str | None = None,
    search: str = "",
    sort: str = VIDEO_SORT_DEFAULT,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """List videos with optional filter by channel_id, status, and title search. HTMX returns _video_list.html."""
    channel_id_int: int | None = None
    if channel_id and channel_id.strip():
        try:
            channel_id_int = int(channel_id)
        except ValueError:
            pass
    status_clean = (status.strip() or None) if status else None
    # Default to "synced" when no status filter is provided (full-page load only)
    if status_clean is None and not request.headers.get("HX-Request"):
        status_clean = "synced"

    search_clean = search.strip()

    sort_clean = sort.strip() if sort else VIDEO_SORT_DEFAULT
    if sort_clean not in VIDEO_SORT_OPTIONS:
        sort_clean = VIDEO_SORT_DEFAULT

    base_stmt = select(Video)
    if sort_clean == "channel_asc":
        base_stmt = base_stmt.join(Video.channel).order_by(VIDEO_SORT_OPTIONS[sort_clean]())
    else:
        base_stmt = base_stmt.order_by(VIDEO_SORT_OPTIONS[sort_clean]())
    if channel_id_int is not None:
        base_stmt = base_stmt.where(Video.channel_id == channel_id_int)
    if status_clean:
        base_stmt = base_stmt.where(Video.status == status_clean)
    if search_clean:
        escaped = search_clean.replace("%", r"\%").replace("_", r"\_")
        base_stmt = base_stmt.where(Video.title.ilike(f"%{escaped}%", escape="\\"))

    count_stmt = select(func.count()).select_from(Video)
    if channel_id_int is not None:
        count_stmt = count_stmt.where(Video.channel_id == channel_id_int)
    if status_clean:
        count_stmt = count_stmt.where(Video.status == status_clean)
    if search_clean:
        escaped = search_clean.replace("%", r"\%").replace("_", r"\_")
        count_stmt = count_stmt.where(Video.title.ilike(f"%{escaped}%", escape="\\"))
    total = (await db.execute(count_stmt)).scalar_one() or 0
    total_pages = math.ceil(total / per_page) if total else 1
    if page > total_pages:
        page = total_pages

    data_stmt = (
        base_stmt.options(selectinload(Video.channel))
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(data_stmt)
    videos = list(result.scalars().all())
    progress_map = download_progress.snapshot()

    ctx = {
        "request": request,
        "videos": videos,
        "download_progress": progress_map,
        "settings": settings,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "search": search_clean,
        "sort": sort_clean,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("videos/_video_list.html", ctx)

    channels = list(
        (await db.execute(select(Channel).order_by(Channel.name))).scalars().all()
    )
    active_stmt = (
        select(Video)
        .where(_active_panel_condition())
        .options(selectinload(Video.channel))
        .order_by(Video.created_at.desc())
    )
    active_result = await db.execute(active_stmt)
    active_videos = list(active_result.scalars().all())
    return templates.TemplateResponse(
        "videos/list.html",
        {
            **ctx,
            "channels": channels,
            "selected_channel_id": channel_id_int,
            "selected_status": status_clean,
            "active_videos": active_videos,
            "downloads_paused": download_control.is_downloads_paused(),
        },
    )


@router.get("/active_downloads")
async def active_downloads(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """HTMX partial: active downloads panel (videos in transitional status with progress)."""
    stmt = (
        select(Video)
        .where(_active_panel_condition())
        .options(selectinload(Video.channel))
        .order_by(Video.created_at.desc())
    )
    result = await db.execute(stmt)
    active_videos = list(result.scalars().all())
    progress_map = download_progress.snapshot()
    return templates.TemplateResponse(
        "videos/_active_downloads.html",
        {
            "request": request,
            "active_videos": active_videos,
            "download_progress": progress_map,
            "settings": settings,
        },
    )


@router.post("/resync_all")
async def resync_all_videos(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    """Re-sync all synced videos from Stash (scrape + generate) as a background task.

    Finds every video with a stash_scene_id and queues a background task that
    processes each one sequentially — scraping metadata and optionally regenerating.
    """
    # Gather IDs of all synced videos
    result = await db.execute(
        select(Video.id).where(Video.stash_scene_id.isnot(None))
    )
    video_ids = [row[0] for row in result.all()]

    if not video_ids:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<span class="text-warning text-sm">No synced videos to re-sync</span>',
                status_code=200,
            )
        return RedirectResponse(url=request.headers.get("HX-Current-URL", "/videos"), status_code=303)

    async def _run_resync_all() -> None:
        """Background: re-sync each video sequentially."""
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
                                "Resync-all: scene %s not found for video %s, skipping",
                                video.stash_scene_id, vid,
                            )
                            failed += 1
                            continue

                        # Scrape
                        try:
                            scraped = await stash.scrape_scene_url(video.url)
                            if scraped:
                                await stash.apply_scraped_scene(
                                    scene_id=video.stash_scene_id,
                                    scraped=scraped,
                                )
                            video.scrape_attempted_at = datetime.now(UTC)
                        except Exception as e:
                            logger.warning("Resync-all: scrape failed for video %s: %s", vid, e)

                        # Generate
                        if settings.stash_generate_after_sync:
                            try:
                                await generate_for_scene(
                                    video, video.stash_scene_id, stash, settings
                                )
                                video.generate_triggered_at = datetime.now(UTC)
                            except Exception as e:
                                logger.warning("Resync-all: generate failed for video %s: %s", vid, e)

                    await session.commit()
                    succeeded += 1
                    logger.info("Resync-all: video %s complete", vid)
            except Exception:
                logger.exception("Resync-all: unexpected error for video %s", vid)
                failed += 1

        logger.info(
            "Resync-all finished: %d succeeded, %d failed out of %d total",
            succeeded, failed, len(video_ids),
        )

    task = asyncio.create_task(_run_resync_all())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info("Resync-all started for %d videos", len(video_ids))

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<span class="text-success text-sm">Re-syncing {len(video_ids)} video(s) in background…</span>',
            status_code=200,
        )
    return RedirectResponse(url=request.headers.get("Referer", "/videos"), status_code=303)


@router.post("/retry_all_skipped")
async def retry_all_skipped(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reset all skipped videos back to pending/downloaded so they get re-evaluated.

    Useful after changing channel filter settings (min_duration_seconds,
    max_video_age_days) — previously-skipped videos that now fall within bounds
    will be picked up by the download processor and re-evaluated against the
    channel's current settings.
    """
    result = await db.execute(
        select(Video).where(Video.status == "skipped")
    )
    videos = list(result.scalars().all())

    if not videos:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<span class="text-warning text-sm">No skipped videos to retry</span>',
                status_code=200,
            )
        return RedirectResponse(url=request.headers.get("Referer", "/videos"), status_code=303)

    for video in videos:
        if video.oshash or video.original_filename:
            video.status = "downloaded"  # file exists, skip download
        else:
            video.status = "pending"  # full pipeline including download
        video.error_message = None
    count = len(videos)

    logger.info("Retry-all-skipped: re-queued %d video(s)", count)

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<span class="text-success text-sm">Re-queued {count} skipped video(s) for processing</span>',
            status_code=200,
        )
    return RedirectResponse(url=request.headers.get("Referer", "/videos"), status_code=303)


@router.get("/{video_id}")
async def video_detail(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Video detail page."""
    result = await db.execute(
        select(Video)
        .where(Video.id == video_id)
        .options(selectinload(Video.channel))
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return templates.TemplateResponse(
        "videos/detail.html",
        {
            "request": request,
            "video": video,
            "settings": settings,
            "download_progress": download_progress.snapshot(),
            "poll_status_badge": True,
        },
    )


@router.get("/{video_id}/status_badge")
async def video_status_badge(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    detail: int | None = Query(None, description="1 for detail-page layout (status + channel on one line, progress on next)"),
):
    """HTMX partial: status badge (optionally includes download progress)."""
    if detail:
        result = await db.execute(
            select(Video).where(Video.id == video_id).options(selectinload(Video.channel))
        )
        video = result.scalar_one_or_none()
    else:
        video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    ctx = {
        "request": request,
        "video": video,
        "download_progress": download_progress.snapshot(),
        "poll_status_badge": True,
    }
    if detail:
        ctx["progress_own_line"] = True
        ctx["channel"] = video.channel
    return templates.TemplateResponse("videos/_status_badge.html", ctx)


@router.post("/{video_id}/retry")
async def retry_video(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Reset a failed/cancelled/skipped video for another run.

    If the video is still linked to a Stash scene, that scene — plus its file and
    generated content — is destroyed first so the import is fully redone rather
    than re-syncing to the stale scene. Otherwise it's an import-only retry when a
    local file/oshash exists, or a full download when not.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status not in {"failed", "cancelled", "skipped"}:
        raise HTTPException(
            status_code=400,
            detail="Only failed/cancelled/skipped videos can be retried",
        )

    if video.stash_scene_id:
        # Linked to Stash → wipe the scene + file and re-download fresh for a clean re-import.
        async with StashClient.from_settings(settings) as stash:
            await hard_reset_video(video, stash, settings)
        logger.info("Video %s reset for retry (Stash scene wiped, full re-download)", video_id)
    elif video.oshash or video.original_filename:
        video.status = "downloaded"  # skip download, retry import only
        video.error_message = None
        logger.info("Video %s reset for retry (status=downloaded, import-only)", video_id)
    else:
        video.status = "pending"  # full pipeline including download
        video.error_message = None
        logger.info("Video %s reset for retry (status=pending, full download)", video_id)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "videos/_status_badge.html",
            {"request": request, "video": video, "poll_status_badge": True},
        )
    return RedirectResponse(url=f"/videos/{video_id}", status_code=303)


@router.post("/{video_id}/redownload")
async def redownload_video(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Force a fresh download.

    For videos already linked to a Stash scene, the scene is destroyed first
    (with delete_file=True, delete_generated=True) so re-import produces a
    clean replacement rather than orphaning the old scene. Then any remaining
    local file is removed and the video is reset to ``pending``.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status not in REDOWNLOAD_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot redownload a video in status '{video.status}'",
        )

    if video.stash_scene_id:
        async with StashClient.from_settings(settings) as stash:
            await hard_reset_video(video, stash, settings)
    else:
        await hard_reset_video(video, None, settings)
    logger.info("Video %s reset to pending for redownload", video_id)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "videos/_status_badge.html",
            {"request": request, "video": video, "poll_status_badge": True},
        )
    return RedirectResponse(url=f"/videos/{video_id}", status_code=303)


@router.post("/{video_id}/stop")
async def stop_video(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Request a pending/in-flight video to stop.

    - pending: mark cancelled immediately
    - downloading/downloaded/importing: request cooperative cancellation and mark "cancelling"
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.status == "pending":
        video.status = "cancelled"
        video.error_message = "Cancelled by user"
        download_progress.clear(video.id)
        download_control.clear_cancel(video.id)
        await db.commit()
    elif video.status in {"downloading", "downloaded", "importing"}:
        download_control.request_cancel(video.id)
        # Force-cancel the asyncio download task so a hung yt-dlp thread
        # doesn't block the download processor job forever.
        download_control.cancel_download_task(video.id)
        if video.status != "cancelling":
            video.status = "cancelling"
            await db.commit()
    elif video.status == "cancelling":
        # Already requested; no-op.
        pass
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot stop a video in status '{video.status}'",
        )

    if request.headers.get("HX-Request"):
        # Return the full active-downloads panel when it's the swap target (global or channel-scoped).
        hx_target = request.headers.get("HX-Target") or ""
        if hx_target == "active-downloads":
            stmt = (
                select(Video)
                .where(_active_panel_condition())
                .options(selectinload(Video.channel))
                .order_by(Video.created_at.desc())
            )
            result = await db.execute(stmt)
            active_videos = list(result.scalars().all())
            return templates.TemplateResponse(
                "videos/_active_downloads.html",
                {
                    "request": request,
                    "active_videos": active_videos,
                    "download_progress": download_progress.snapshot(),
                    "settings": settings,
                },
            )
        target_id = (hx_target or "").lstrip("#")
        if target_id.startswith("channel-active-downloads-"):
            try:
                channel_id = int(target_id.removeprefix("channel-active-downloads-"))
            except ValueError:
                channel_id = None
            if channel_id is not None:
                result = await db.execute(
                    select(Channel)
                    .where(Channel.id == channel_id)
                    .options(selectinload(Channel.videos))
                )
                channel = result.scalar_one_or_none()
                if channel:
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
        return templates.TemplateResponse(
            "videos/_status_badge.html",
            {
                "request": request,
                "video": video,
                "download_progress": download_progress.snapshot(),
                "poll_status_badge": True,
            },
        )
    return RedirectResponse(url=f"/videos/{video_id}", status_code=303)


@router.delete("/{video_id}")
async def delete_video(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove video record. Returns empty 200 for HTMX or redirect."""
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    await db.delete(video)
    logger.info("Video %s deleted", video_id)

    if request.headers.get("HX-Request"):
        hx_target = request.headers.get("HX-Target") or ""
        # List and performer detail target the row for swap; return empty to remove it
        if "video-row-" in hx_target:
            return HTMLResponse("", status_code=200)
        # Video detail page has no target; redirect back to list
        return HTMLResponse(
            status_code=200,
            headers={"HX-Redirect": "/videos"},
        )
    return RedirectResponse(url="/videos", status_code=303)


@router.post("/{video_id}/resync")
async def resync_video(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Re-sync a synced video's scene from Stash: scrape, apply, generate.

    Always scrapes regardless of the ``stash_scrape_after_sync`` setting because
    this is an explicit user action.  Scraped performers/studio are applied without
    preserving yt-dlp originals so the scraper can fully refresh the scene.

    Returns HTMX _status_badge or redirect.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if not video.stash_scene_id:
        raise HTTPException(
            status_code=400,
            detail="Video has no Stash scene ID — sync must complete first",
        )

    async with StashClient.from_settings(settings) as stash:
        # Verify scene still exists in Stash
        scene = await stash.find_scene_by_id(video.stash_scene_id)
        if not scene:
            raise HTTPException(
                status_code=404,
                detail=f"Scene {video.stash_scene_id} not found in Stash",
            )

        # Re-scrape and apply
        try:
            scraped = await stash.scrape_scene_url(video.url)
            if scraped:
                await stash.apply_scraped_scene(
                    scene_id=video.stash_scene_id,
                    scraped=scraped,
                )
                logger.info(
                    "Video %s: re-sync scraped and applied for scene %s",
                    video_id,
                    video.stash_scene_id,
                )
            else:
                logger.info(
                    "Video %s: re-sync scrape returned no data for scene %s",
                    video_id,
                    video.stash_scene_id,
                )
            video.scrape_attempted_at = datetime.now(UTC)
        except Exception as e:
            logger.warning(
                "Video %s: re-sync scrape failed: %s", video_id, e
            )

        # Re-generate
        if settings.stash_generate_after_sync:
            try:
                await generate_for_scene(
                    video, video.stash_scene_id, stash, settings
                )
                video.generate_triggered_at = datetime.now(UTC)
            except Exception as e:
                logger.warning(
                    "Video %s: re-sync generate failed: %s", video_id, e
                )

    logger.info("Video %s: re-sync complete", video_id)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "videos/_status_badge.html",
            {
                "request": request,
                "video": video,
                "download_progress": download_progress.snapshot(),
                "poll_status_badge": True,
            },
        )
    return RedirectResponse(url=f"/videos/{video_id}", status_code=303)
