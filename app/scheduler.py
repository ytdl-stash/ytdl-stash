"""APScheduler setup: periodic channel checks and download processing, plus job registry."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_, select, update

from app import database as db_module
from app.config import get_settings
from app.models import Channel, Video
from app.pipeline import process_channel_scan, process_pending_downloads
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Job registry: tracks metadata for all triggerable jobs
# ---------------------------------------------------------------------------


@dataclass
class JobInfo:
    """Metadata for a triggerable job."""

    id: str
    name: str
    description: str
    coro_fn: Callable[[], Coroutine[Any, Any, None]] | None = field(
        default=None, repr=False
    )
    last_run_at: datetime | None = None
    last_duration_seconds: float | None = None
    last_error: str | None = None
    running: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


# Registry populated at module import; running state updated at runtime.
job_registry: dict[str, JobInfo] = {
    "check_all_channels": JobInfo(
        id="check_all_channels",
        name="Check All Channels",
        description="Scan all enabled channels that are due for new videos.",
    ),
    "process_downloads": JobInfo(
        id="process_downloads",
        name="Process Downloads",
        description="Download and import the next pending video in the queue.",
    ),
    "retry_all_failed": JobInfo(
        id="retry_all_failed",
        name="Retry All Failed",
        description="Reset every failed video back to pending so the download processor retries them.",
    ),
}


async def _run_tracked(job_id: str, coro_fn) -> None:
    """Wrap a job coroutine with tracking: set running flag, record timing."""
    info = job_registry[job_id]
    # Guard against duplicate triggers:
    # - `info.running` is set eagerly for manual triggers so that a rapid double-click
    #   doesn't enqueue multiple sequential runs before the lock is acquired.
    # - `_lock.locked()` covers already-running jobs (manual or scheduled).
    if info.running or info._lock.locked():
        logger.debug("Job %s already running, skipping", job_id)
        return
    async with info._lock:
        info.running = True
        info.last_error = None
        start = datetime.now(UTC)
        try:
            await coro_fn()
        except Exception as exc:
            info.last_error = str(exc)[:500]
            raise
        finally:
            info.running = False
            info.last_run_at = datetime.now(UTC)
            info.last_duration_seconds = (
                info.last_run_at - start
            ).total_seconds()


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------


async def _do_check_all_channels() -> None:
    """Core logic: scan every due enabled channel."""
    if db_module.async_session is None:
        logger.warning("Channel checker skipped: database session not initialized")
        return
    async with db_module.async_session() as db:
        try:
            settings = get_settings()
            now = datetime.now(UTC)
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
            if not due:
                logger.debug("Channel checker: no channels due for scanning")
                return
            logger.info("Channel checker: %d channel(s) due for scanning", len(due))
            for channel in due:
                try:
                    await process_channel_scan(channel, db, settings)
                except Exception as e:
                    logger.exception(
                        "Channel checker failed for channel %s: %s", channel.id, e
                    )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _do_process_downloads() -> None:
    """Core logic: process one pending video."""
    if db_module.async_session is None:
        logger.warning("Download processor skipped: database session not initialized")
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


async def _do_retry_all_failed() -> None:
    """Core logic: reset all failed videos to pending."""
    if db_module.async_session is None:
        logger.warning("Retry all failed skipped: database session not initialized")
        return
    async with db_module.async_session() as db:
        try:
            result = await db.execute(
                update(Video)
                .where(Video.status == "failed")
                .values(status="pending", error_message=None)
            )
            count = result.rowcount
            await db.commit()
            logger.info("Retry all failed: reset %d video(s) to pending", count)
        except Exception:
            await db.rollback()
            raise


# Wire coroutine functions into registry (defined above, registered here).
job_registry["check_all_channels"].coro_fn = _do_check_all_channels
job_registry["process_downloads"].coro_fn = _do_process_downloads
job_registry["retry_all_failed"].coro_fn = _do_retry_all_failed


# ---------------------------------------------------------------------------
# Scheduled wrappers (called by APScheduler)
# ---------------------------------------------------------------------------


async def _channel_checker() -> None:
    """Run every 60s: scan due channels for new videos. max_instances=1."""
    await _run_tracked("check_all_channels", _do_check_all_channels)


async def _download_processor() -> None:
    """Run every 30s: process one pending video. max_instances=1."""
    await _run_tracked("process_downloads", _do_process_downloads)


# ---------------------------------------------------------------------------
# Manual triggers (called from routes)
# ---------------------------------------------------------------------------

# Hold strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()


def _task_done(task: asyncio.Task) -> None:
    """Cleanup callback for background job tasks."""
    _background_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background job failed: %s", task.exception())


def trigger_job(job_id: str) -> bool:
    """Trigger a job manually in the background. Returns False if already running."""
    info = job_registry.get(job_id)
    if info is None:
        raise ValueError(f"Unknown job: {job_id}")
    if info.running or info._lock.locked():
        return False
    if info.coro_fn is None:
        raise ValueError(f"Job {job_id} has no coroutine function registered")

    # Eagerly set running so the immediate HTMX response reflects the new state.
    # The background task hasn't started yet (create_task only schedules it),
    # so without this the response would still show "Idle".
    info.running = True

    task = asyncio.create_task(_run_tracked(job_id, info.coro_fn))
    _background_tasks.add(task)
    task.add_done_callback(_task_done)
    return True


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


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
