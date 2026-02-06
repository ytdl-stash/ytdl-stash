"""Video routes: list with filters, detail, retry, delete."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import get_db
from app.main import templates
from app.models import Channel, Video

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])


@router.get("")
async def list_videos(
    request: Request,
    channel_id: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List videos with optional filter by channel_id and status. HTMX returns _table_body.html."""
    channel_id_int: int | None = None
    if channel_id and channel_id.strip():
        try:
            channel_id_int = int(channel_id)
        except ValueError:
            pass
    status_clean = status.strip() if status else None

    stmt = (
        select(Video)
        .options(selectinload(Video.channel))
        .order_by(Video.created_at.desc())
    )
    if channel_id_int is not None:
        stmt = stmt.where(Video.channel_id == channel_id_int)
    if status_clean:
        stmt = stmt.where(Video.status == status_clean)

    result = await db.execute(stmt)
    videos = list(result.scalars().all())

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "videos/_table_body.html",
            {"request": request, "videos": videos},
        )

    channels = list(
        (await db.execute(select(Channel).order_by(Channel.name))).scalars().all()
    )
    return templates.TemplateResponse(
        "videos/list.html",
        {
            "request": request,
            "videos": videos,
            "channels": channels,
            "selected_channel_id": channel_id_int,
            "selected_status": status_clean,
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
        {"request": request, "video": video, "settings": settings},
    )


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
    if video.status != "failed":
        raise HTTPException(
            status_code=400,
            detail="Only failed videos can be retried",
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
        return HTMLResponse(
            status_code=200,
            headers={"HX-Redirect": "/videos"},
        )
    return RedirectResponse(url="/videos", status_code=303)
