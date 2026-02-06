"""Channel routes: list, add, update, delete, check-now."""

import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import async_session, get_db
from app.main import templates
from app.models import Channel
from app.performer_sync import sync_channel_performer
from app.pipeline import process_channel_scan
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])

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


@router.get("/add")
async def add_channel_page(request: Request):
    """Add channel form page."""
    return templates.TemplateResponse(
        "channels/add.html",
        {"request": request},
    )


@router.post("")
async def add_channel(
    request: Request,
    url: str = Form(...),
    name: str = Form(""),
    check_interval_hours: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Add a new channel. Derives site from URL. Returns HTMX partial _row.html or redirect."""
    display_name = name.strip() or _derive_site(url)
    site = _derive_site(url)
    interval = (
        check_interval_hours
        if check_interval_hours is not None
        else settings.default_check_interval_hours
    )

    channel = Channel(
        name=display_name,
        url=url,
        site=site,
        check_interval_hours=interval,
    )
    db.add(channel)
    await db.flush()

    try:
        async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
            await sync_channel_performer(channel, db, stash, settings)
    except Exception:
        logger.warning("Performer sync failed for channel %s", channel.id, exc_info=True)

    if request.headers.get("HX-Request"):
        result = await db.execute(
            select(Channel)
            .where(Channel.id == channel.id)
            .options(selectinload(Channel.videos))
        )
        channel = result.scalar_one()
        return templates.TemplateResponse(
            "channels/_row.html",
            {"request": request, "channel": channel},
        )
    return RedirectResponse(url="/channels", status_code=303)


@router.put("/{channel_id}")
async def update_channel(
    channel_id: int,
    request: Request,
    name: str = Form(""),
    enabled: str = Form("true"),
    check_interval_hours: int = Form(6),
    db: AsyncSession = Depends(get_db),
):
    """Update channel. Returns HTMX partial _row.html or redirect."""
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.name = name.strip() or channel.name
    channel.enabled = enabled.lower() not in ("false", "0", "off")
    channel.check_interval_hours = check_interval_hours

    if request.headers.get("HX-Request"):
        result = await db.execute(
            select(Channel)
            .where(Channel.id == channel_id)
            .options(selectinload(Channel.videos))
        )
        channel = result.scalar_one()
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

    async def _run_scan() -> None:
        if async_session is None:
            return
        async with async_session() as session:
            ch = await session.get(Channel, channel_id)
            if ch:
                try:
                    await process_channel_scan(ch, session, settings)
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
