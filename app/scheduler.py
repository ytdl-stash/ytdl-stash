"""APScheduler setup: periodic channel checks and download processing."""

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_, select

from app import database as db_module
from app.config import get_settings
from app.models import Channel
from app.pipeline import process_channel_scan, process_pending_downloads
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _channel_checker() -> None:
    """Run every 60s: scan due channels for new videos. max_instances=1."""
    if db_module.async_session is None:
        return
    async with db_module.async_session() as db:
        try:
            settings = get_settings()
            now = datetime.now(UTC)
            # Coarse filter: only load channels that might be due (checked >1h ago or never)
            one_hour_ago = now - timedelta(hours=1)
            stmt = select(Channel).where(
                Channel.enabled.is_(True),
                or_(
                    Channel.last_checked_at.is_(None),
                    Channel.last_checked_at < one_hour_ago,
                ),
            )
            result = await db.execute(stmt)
            channels = list(result.scalars().all())
            due = [
                ch
                for ch in channels
                if ch.last_checked_at is None
                or ch.last_checked_at < now - timedelta(hours=ch.check_interval_hours)
            ]
            for channel in due:
                try:
                    await process_channel_scan(channel, db, settings)
                except Exception as e:
                    logger.exception("Channel checker failed for channel %s: %s", channel.id, e)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _download_processor() -> None:
    """Run every 30s: process one pending video. max_instances=1."""
    if db_module.async_session is None:
        return
    async with db_module.async_session() as db:
        try:
            settings = get_settings()
            async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
                await process_pending_downloads(db, settings, stash)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.exception("Download processor failed: %s", e)
            raise


def start_scheduler() -> None:
    """Start the scheduler. Call from FastAPI lifespan after init_db."""
    scheduler.add_job(
        _channel_checker,
        "interval",
        seconds=60,
        id="channel_checker",
        max_instances=1,
    )
    scheduler.add_job(
        _download_processor,
        "interval",
        seconds=30,
        id="download_processor",
        max_instances=1,
    )
    scheduler.start()
    logger.info("Scheduler started (channel_checker=60s, download_processor=30s)")


def stop_scheduler() -> None:
    """Stop the scheduler. Call from FastAPI lifespan on shutdown.
    wait=True lets the current job (channel check or download) finish before stopping.
    """
    scheduler.shutdown(wait=True)
    logger.info("Scheduler stopped")
