"""Video routes: list with filters, detail, retry, delete, resync."""

import logging
import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import get_db
from app.download_control import download_control
from app.download_progress import download_progress
from app.main import templates
from app.models import Channel, Video
from app.stash_client import StashClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])

ACTIVE_DOWNLOAD_STATUSES = ("downloading", "cancelling", "downloaded", "importing")


@router.get("")
async def list_videos(
    request: Request,
    channel_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """List videos with optional filter by channel_id and status. HTMX returns _video_list.html."""
    channel_id_int: int | None = None
    if channel_id and channel_id.strip():
        try:
            channel_id_int = int(channel_id)
        except ValueError:
            pass
    status_clean = (status.strip() or None) if status else None

    base_stmt = select(Video).order_by(Video.created_at.desc())
    if channel_id_int is not None:
        base_stmt = base_stmt.where(Video.channel_id == channel_id_int)
    if status_clean:
        base_stmt = base_stmt.where(Video.status == status_clean)

    count_stmt = select(func.count()).select_from(Video)
    if channel_id_int is not None:
        count_stmt = count_stmt.where(Video.channel_id == channel_id_int)
    if status_clean:
        count_stmt = count_stmt.where(Video.status == status_clean)
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
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("videos/_video_list.html", ctx)

    channels = list(
        (await db.execute(select(Channel).order_by(Channel.name))).scalars().all()
    )
    active_stmt = (
        select(Video)
        .where(Video.status.in_(ACTIVE_DOWNLOAD_STATUSES))
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
        .where(Video.status.in_(ACTIVE_DOWNLOAD_STATUSES))
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
):
    """Reset failed video to pending. Returns HTMX _status_badge.html or redirect."""
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status not in {"failed", "cancelled", "skipped"}:
        raise HTTPException(
            status_code=400,
            detail="Only failed/cancelled/skipped videos can be retried",
        )

    video.status = "pending"
    video.error_message = None
    logger.info("Video %s reset to pending for retry", video_id)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "videos/_status_badge.html",
            {"request": request, "video": video},
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
                .where(Video.status.in_(ACTIVE_DOWNLOAD_STATUSES))
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
                    active_videos = [
                        v for v in channel.videos if v.status in ACTIVE_DOWNLOAD_STATUSES
                    ]
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

    async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
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
                await stash.trigger_generate(
                    scene_ids=[video.stash_scene_id],
                    covers=settings.stash_generate_covers,
                    previews=settings.stash_generate_previews,
                    sprites=settings.stash_generate_sprites,
                    phashes=settings.stash_generate_phashes,
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
