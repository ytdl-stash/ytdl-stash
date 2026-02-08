"""Performer browser routes: list performers (channels), detail, sync, toggle watch, delete."""

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import get_db
from app.download_progress import download_progress
from app.main import templates
from app.models import Channel
from app.performer_sync import sync_channel_performer
from app.studio_sync import sync_channel_studio
from app.stash_client import StashClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performers", tags=["performers"])


@router.get("")
async def list_performers(
    request: Request,
    filter: str = "all",
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """List all channels as performers. filter: all|watched|not_watched, sort: name|video_count|last_checked."""
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
    # else sort by name (already from DB)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "performers/_card_list.html",
            {"request": request, "channels": channels, "settings": settings},
        )
    return templates.TemplateResponse(
        "performers/list.html",
        {
            "request": request,
            "channels": channels,
            "filter": filter,
            "sort": sort,
            "settings": settings,
        },
    )


@router.get("/{channel_id}")
async def performer_detail(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Performer (channel) detail: metadata, Stash link status, video table."""
    result = await db.execute(
        select(Channel)
        .where(Channel.id == channel_id)
        .options(selectinload(Channel.videos))
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Performer not found")
    videos = sorted(
        channel.videos,
        key=lambda v: v.upload_date or date.min,
        reverse=True,
    )
    return templates.TemplateResponse(
        "performers/detail.html",
        {
            "request": request,
            "channel": channel,
            "videos": videos,
            "stash_url": settings.stash_url.rstrip("/"),
            "settings": settings,
            "download_progress": download_progress.snapshot(),
        },
    )


async def _performers_sync_response(
    channel_id: int,
    request: Request,
    db: AsyncSession,
    settings: Settings,
) -> Response:
    """Reload channel and return card (HTMX) or redirect to detail."""
    result = await db.execute(
        select(Channel)
        .where(Channel.id == channel_id)
        .options(selectinload(Channel.videos))
    )
    channel = result.scalar_one()
    if request.headers.get("HX-Request"):
        hx_target = request.headers.get("HX-Target") or ""
        if "performer-detail-card" in hx_target:
            videos = sorted(
                channel.videos,
                key=lambda v: v.upload_date or date.min,
                reverse=True,
            )
            return templates.TemplateResponse(
                "performers/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "stash_url": settings.stash_url.rstrip("/"),
                    "settings": settings,
                    "download_progress": download_progress.snapshot(),
                },
            )
        return templates.TemplateResponse(
            "performers/_card.html",
            {"request": request, "channel": channel, "settings": settings},
        )
    return RedirectResponse(url=f"/performers/{channel_id}", status_code=303)


@router.post("/{channel_id}/sync-performer")
async def performer_sync_performer(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger performer sync only. Returns updated _card.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Performer not found")
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
    except Exception:
        logger.warning("Performer sync failed for channel %s", channel_id, exc_info=True)
    return await _performers_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/sync-studio")
async def performer_sync_studio(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger studio sync only. Returns updated _card.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Performer not found")
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Studio sync failed for channel %s", channel_id, exc_info=True)
    return await _performers_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/sync")
async def performer_sync_both(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger performer and studio sync. Returns updated _card.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Performer not found")
    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
            await sync_channel_studio(channel, db, stash, settings)
    except Exception:
        logger.warning("Performer/studio sync failed for channel %s", channel_id, exc_info=True)
    return await _performers_sync_response(channel_id, request, db, settings)


@router.post("/{channel_id}/toggle")
async def performer_toggle(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Toggle channel enabled (watch/unwatch). Returns updated _card.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Performer not found")
    channel.enabled = not channel.enabled

    if request.headers.get("HX-Request"):
        hx_target = request.headers.get("HX-Target") or ""
        result = await db.execute(
            select(Channel)
            .where(Channel.id == channel_id)
            .options(selectinload(Channel.videos))
        )
        channel = result.scalar_one()
        if "performer-detail-card" in hx_target:
            videos = sorted(
                channel.videos,
                key=lambda v: v.upload_date or date.min,
                reverse=True,
            )
            return templates.TemplateResponse(
                "performers/_detail_card.html",
                {
                    "request": request,
                    "channel": channel,
                    "videos": videos,
                    "stash_url": settings.stash_url.rstrip("/"),
                    "settings": settings,
                    "download_progress": download_progress.snapshot(),
                },
            )
        return templates.TemplateResponse(
            "performers/_card.html",
            {"request": request, "channel": channel, "settings": settings},
        )
    return RedirectResponse(url="/performers", status_code=303)


@router.delete("/{channel_id}")
async def delete_performer(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a performer (channel) and all its videos. Returns empty 200 for HTMX or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Performer not found")

    await db.delete(channel)

    if request.headers.get("HX-Request"):
        return HTMLResponse(status_code=200)
    return RedirectResponse(url="/performers", status_code=303)
