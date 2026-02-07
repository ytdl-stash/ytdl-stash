"""Dashboard route: GET / with aggregate stats."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import get_db
from app.main import templates
from app.models import Channel, Video

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
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

    # Downloads by day (last 90 days): count by COALESCE(downloaded_at, synced_at) date
    start_date = (datetime.now(UTC) - timedelta(days=89)).date()
    day_count_result = await db.execute(
        text(
            "SELECT date(COALESCE(downloaded_at, synced_at)) AS d, COUNT(*) AS c "
            "FROM videos "
            "WHERE COALESCE(downloaded_at, synced_at) IS NOT NULL "
            "AND date(COALESCE(downloaded_at, synced_at)) >= :start "
            "GROUP BY d ORDER BY d"
        ),
        {"start": start_date.isoformat()},
    )
    count_by_date = {row[0]: row[1] for row in day_count_result.fetchall()}

    chart_labels = []
    chart_values = []
    for i in range(90):
        d = start_date + timedelta(days=i)
        key = d.isoformat()
        chart_labels.append(key)
        chart_values.append(count_by_date.get(key, 0))

    chart_data = {"labels": chart_labels, "values": chart_values}

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "total_channels": total_channels,
            "total_videos": total_videos,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "recent_downloads": recent_downloads,
            "settings": settings,
            "chart_data": chart_data,
        },
    )
