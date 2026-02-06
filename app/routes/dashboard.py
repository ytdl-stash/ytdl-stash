"""Dashboard route: GET / with aggregate stats."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.main import templates
from app.models import Channel, Video

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render dashboard with total channels, total videos, pending/failed counts, recent downloads."""
    # Total channels
    result = await db.execute(select(func.count(Channel.id)))
    total_channels = result.scalar() or 0

    # Total videos
    result = await db.execute(select(func.count(Video.id)))
    total_videos = result.scalar() or 0

    # Pending count
    result = await db.execute(
        select(func.count(Video.id)).where(Video.status == "pending")
    )
    pending_count = result.scalar() or 0

    # Failed count
    result = await db.execute(
        select(func.count(Video.id)).where(Video.status == "failed")
    )
    failed_count = result.scalar() or 0

    # Recent downloads (synced, ordered by updated_at desc, limit 10)
    result = await db.execute(
        select(Video)
        .options(selectinload(Video.channel))
        .where(Video.status == "synced")
        .order_by(Video.updated_at.desc())
        .limit(10)
    )
    recent_downloads = list(result.scalars().all())

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "total_channels": total_channels,
            "total_videos": total_videos,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "recent_downloads": recent_downloads,
        },
    )
