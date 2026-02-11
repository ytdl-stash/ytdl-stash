"""APScheduler setup: periodic channel checks and download processing, plus job registry."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import and_, or_, select, update

from app import database as db_module
from app.config import get_settings
from app.models import Channel, Video
from app.pipeline import process_channel_scan, process_pending_downloads, run_scrape_and_generate
from app.stash_client import StashClient
from app.ytdlp_updates import check_for_update as ytdlp_check_for_update

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
        description="Download and import pending videos in the queue (up to configured concurrency).",
    ),
    "retry_all_failed": JobInfo(
        id="retry_all_failed",
        name="Retry All Failed",
        description="Retry all failed videos (import-only when oshash/filename exists; full re-download otherwise).",
    ),
    "check_ytdlp_updates": JobInfo(
        id="check_ytdlp_updates",
        name="Check yt-dlp Updates",
        description="Check GitHub nightly builds for a newer yt-dlp version (does not rebuild container).",
    ),
    "backfill_scrape_generate": JobInfo(
        id="backfill_scrape_generate",
        name="Backfill Scrape & Generate",
        description="Run scrape and generate for synced videos that haven't had them yet.",
    ),
    "regenerate_all": JobInfo(
        id="regenerate_all",
        name="Regenerate All",
        description="Re-trigger Stash generate (previews, sprites, etc.) for all synced videos.",
    ),
}

# Registry job_id -> APScheduler job id (None = manual-only, no scheduled run).
APSCHEDULER_ID_MAP: dict[str, str | None] = {
    "check_all_channels": "channel_checker",
    "process_downloads": "download_processor",
    "check_ytdlp_updates": "ytdlp_update_checker",
    "retry_all_failed": None,
    "backfill_scrape_generate": None,
    "regenerate_all": None,
}


def get_job_schedule_info(job_id: str) -> tuple[str, datetime | None]:
    """Return (interval_display, next_run_time) for a registry job.
    Manual-only jobs return ('Manual', None)."""
    apscheduler_id = APSCHEDULER_ID_MAP.get(job_id)
    if apscheduler_id is None:
        return ("Manual", None)
    job = scheduler.get_job(apscheduler_id)
    if job is None:
        return ("—", None)
    next_run = job.next_run_time
    # Format interval from trigger (IntervalTrigger has .interval as timedelta).
    interval_display = "—"
    trigger = getattr(job, "trigger", None)
    if trigger is not None:
        interval = getattr(trigger, "interval", None)
        if interval is not None and isinstance(interval, timedelta):
            total = int(interval.total_seconds())
            if total >= 3600:
                h = total // 3600
                interval_display = f"Every {h}h"
            else:
                interval_display = f"Every {total}s"
    return (interval_display, next_run)


def get_job_schedule_edit_value(job_id: str) -> tuple[int, str] | None:
    """Return (numeric_value, unit) for the schedule edit form, or None if manual-only.
    unit is 'seconds' or 'hours'."""
    apscheduler_id = APSCHEDULER_ID_MAP.get(job_id)
    if apscheduler_id is None:
        return None
    job = scheduler.get_job(apscheduler_id)
    if job is None:
        return None
    trigger = getattr(job, "trigger", None)
    if trigger is None:
        return None
    interval = getattr(trigger, "interval", None)
    if interval is None or not isinstance(interval, timedelta):
        return None
    total = int(interval.total_seconds())
    if apscheduler_id == "ytdlp_update_checker":
        return (max(1, total // 3600), "hours")
    return (max(10, total), "seconds")


def reschedule_job(
    job_id: str,
    *,
    seconds: int | None = None,
    hours: int | None = None,
) -> bool:
    """Reschedule a job with a new interval. Returns True if rescheduled, False if not schedulable or invalid."""
    apscheduler_id = APSCHEDULER_ID_MAP.get(job_id)
    if apscheduler_id is None:
        return False
    if scheduler.get_job(apscheduler_id) is None:
        return False
    if apscheduler_id == "ytdlp_update_checker":
        if hours is None or hours < 1:
            return False
        scheduler.reschedule_job(
            apscheduler_id,
            trigger="interval",
            hours=hours,
        )
    else:
        if seconds is None or seconds < 10:
            return False
        scheduler.reschedule_job(
            apscheduler_id,
            trigger="interval",
            seconds=seconds,
        )
    return True


async def _run_tracked(job_id: str, coro_fn) -> None:
    """Wrap a job coroutine with tracking: set running flag, record timing."""
    info = job_registry[job_id]
    # Guard against already-running jobs.
    # Note: manual triggers set `info.running` eagerly so the UI updates immediately,
    # but we only use the lock to determine if the job is truly executing.
    if info._lock.locked():
        logger.debug("Job %s already running, skipping", job_id)
        return

    # Store the current asyncio task so stop_job() can cancel it regardless
    # of whether this was started by APScheduler or a manual trigger.
    current_task = asyncio.current_task()
    if current_task is not None:
        _background_tasks_by_job_id[job_id] = current_task

    start = datetime.now(UTC)
    info.running = True
    info.last_error = None
    try:
        async with info._lock:
            await coro_fn()
    except asyncio.CancelledError:
        info.last_error = "Stopped by user"
        logger.info("Job %s cancelled", job_id)
    except Exception as exc:
        info.last_error = str(exc)[:500]
        raise
    finally:
        info.running = False
        info.last_run_at = datetime.now(UTC)
        info.last_duration_seconds = (info.last_run_at - start).total_seconds()
        # Clean up task reference if it still points to us.
        if _background_tasks_by_job_id.get(job_id) is current_task:
            _background_tasks_by_job_id.pop(job_id, None)


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------


def _ensure_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (UTC) for safe comparison with now(UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def _do_check_all_channels() -> None:
    """Core logic: scan every due enabled channel."""
    from app.download_control import download_control

    if download_control.is_channels_paused():
        logger.debug("Channel checker: paused, skipping")
        return

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
                if ch.url and ch.url.startswith(("http://", "https://"))
                and (
                    ch.last_checked_at is None
                    or _ensure_aware(ch.last_checked_at)
                    < now - timedelta(hours=ch.check_interval_hours)
                )
            ]
            if not due:
                logger.debug("Channel checker: no channels due for scanning")
                return
            logger.info("Channel checker: %d channel(s) due for scanning", len(due))
            for channel in due:
                channel_id = channel.id  # capture before possible rollback expires attrs
                try:
                    await process_channel_scan(channel, db, settings)
                except Exception as e:
                    # Rollback to clear any dirty session state (e.g. failed
                    # commit) so the next channel scan starts with a clean session.
                    await db.rollback()
                    logger.exception(
                        "Channel checker failed for channel %s: %s", channel_id, e
                    )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _do_process_downloads() -> None:
    """Core logic: process pending videos (sequential or concurrent)."""
    from app.download_control import download_control

    if download_control.is_downloads_paused():
        logger.debug("Download processor: paused, skipping")
        return

    if db_module.async_session is None:
        logger.warning("Download processor skipped: database session not initialized")
        return

    settings = get_settings()
    max_concurrent = max(1, int(getattr(settings, "max_concurrent_downloads", 1) or 1))

    # Preserve existing sequential behavior exactly when concurrency == 1.
    if max_concurrent <= 1:
        async with db_module.async_session() as db:
            try:
                async with StashClient.from_settings(settings) as stash:
                    await process_pending_downloads(db, settings, stash)
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.exception("Download processor failed: %s", e)
                raise
        return

    # Concurrent mode: pick up to N pending videos, then run each in its own
    # DB session + Stash client (AsyncSession is not concurrency-safe).
    async with db_module.async_session() as db:
        try:
            result = await db.execute(
                select(Video.id)
                .where(
                    or_(
                        Video.status == "pending",
                        and_(
                            Video.status == "downloaded",
                            Video.stash_scene_id.is_(None),
                        ),
                    )
                )
                .order_by(Video.created_at)
                .limit(max_concurrent)
            )
            video_ids = [row[0] for row in result.all()]
        except Exception as e:
            await db.rollback()
            logger.exception("Download processor failed while selecting pending videos: %s", e)
            raise

    if not video_ids:
        logger.debug("Download processor: no pending videos in queue")
        return

    logger.info(
        "Download processor: starting up to %d download(s) (picked %d)",
        max_concurrent,
        len(video_ids),
    )

    async def _run_one(video_id: int) -> None:
        if db_module.async_session is None:
            return
        async with db_module.async_session() as db2:
            try:
                async with StashClient.from_settings(settings) as stash2:
                    from app.pipeline import process_single_download

                    video = await db2.get(Video, video_id)
                    if video is None:
                        return
                    # Video may have been cancelled/retried between selection and now.
                    if video.status not in ("pending", "downloaded"):
                        return
                    await process_single_download(video, db2, settings, stash2)
                await db2.commit()
            except Exception as e:
                await db2.rollback()
                # Do not fail the whole job if a single worker crashes unexpectedly.
                logger.exception("Download worker failed for video %s: %s", video_id, e)

    tasks: list[asyncio.Task] = []
    for idx, vid in enumerate(video_ids):
        tasks.append(asyncio.create_task(_run_one(vid)))
        # Keep the existing "delay between downloads" knob useful by staggering
        # starts. (If set to 0, starts immediately.)
        if idx < len(video_ids) - 1 and settings.download_delay_seconds > 0:
            await asyncio.sleep(settings.download_delay_seconds)

    await asyncio.gather(*tasks)


async def _do_retry_all_failed() -> None:
    """Reset failed videos: to downloaded (import-only retry) when oshash/filename
    exists, otherwise to pending (full re-download).
    """
    if db_module.async_session is None:
        logger.warning("Retry all failed skipped: database session not initialized")
        return
    async with db_module.async_session() as db:
        try:
            result = await db.execute(
                select(Video.id, Video.oshash, Video.original_filename)
                .where(Video.status == "failed")
            )
            rows = result.all()
            to_downloaded = 0
            to_pending = 0
            for video_id, oshash, original_filename in rows:
                if oshash or original_filename:
                    await db.execute(
                        update(Video)
                        .where(Video.id == video_id)
                        .values(status="downloaded", error_message=None)
                    )
                    to_downloaded += 1
                else:
                    await db.execute(
                        update(Video)
                        .where(Video.id == video_id)
                        .values(status="pending", error_message=None)
                    )
                    to_pending += 1
            await db.commit()
            total = to_downloaded + to_pending
            if total:
                logger.info(
                    "Retry all failed: reset %d video(s) — %d to downloaded, %d to pending",
                    total, to_downloaded, to_pending,
                )
        except Exception:
            await db.rollback()
            raise


# ---------------------------------------------------------------------------
# Backfill scrape & generate
# ---------------------------------------------------------------------------


_BACKFILL_BATCH_SIZE = 50
_BACKFILL_DELAY_SECONDS = 2


async def _do_backfill_scrape_generate() -> None:
    """Run scrape and generate for synced videos missing scrape_attempted_at or generate_triggered_at."""
    if db_module.async_session is None:
        logger.warning("Backfill scrape/generate skipped: database session not initialized")
        return

    settings = get_settings()
    async with db_module.async_session() as db:
        try:
            result = await db.execute(
                select(Video)
                .where(
                    Video.status == "synced",
                    Video.stash_scene_id.isnot(None),
                    or_(
                        Video.scrape_attempted_at.is_(None),
                        Video.generate_triggered_at.is_(None),
                    ),
                )
                .order_by(Video.synced_at.nullslast(), Video.id)
                .limit(_BACKFILL_BATCH_SIZE)
            )
            videos = list(result.scalars().all())
        except Exception as e:
            await db.rollback()
            logger.exception("Backfill scrape/generate failed while selecting videos: %s", e)
            raise

    if not videos:
        logger.debug("Backfill scrape/generate: no videos need processing")
        return

    logger.info(
        "Backfill scrape/generate: processing %d video(s)",
        len(videos),
    )

    for v in videos:
        if db_module.async_session is None:
            return
        video_id = v.id
        # Remember which steps are already done so we only run what's missing.
        already_scraped = v.scrape_attempted_at is not None
        already_generated = v.generate_triggered_at is not None
        async with db_module.async_session() as db2:
            try:
                async with StashClient.from_settings(settings) as stash:
                    video = await db2.get(Video, video_id)
                    if video is None:
                        continue
                    if video.status != "synced" or not video.stash_scene_id:
                        continue
                    await run_scrape_and_generate(
                        video,
                        video.stash_scene_id,
                        stash,
                        settings,
                        db2,
                        performer_ids=None,
                        studio_id=None,
                        skip_scrape=already_scraped,
                        skip_generate=already_generated,
                    )
                await db2.commit()
            except Exception as e:
                await db2.rollback()
                logger.warning(
                    "Backfill scrape/generate failed for video %s: %s",
                    video_id, e,
                )

        if _BACKFILL_DELAY_SECONDS > 0:
            await asyncio.sleep(_BACKFILL_DELAY_SECONDS)


# ---------------------------------------------------------------------------
# Regenerate all
# ---------------------------------------------------------------------------


async def _do_regenerate_all() -> None:
    """Re-trigger Stash generate for every synced video.

    Resets ``generate_triggered_at`` to NULL so each video is treated as
    un-generated, then walks through them in batches calling
    ``trigger_generate``.  Useful after fixing a bug that caused generate
    to silently fail (e.g. the organized-flag file-move race).
    """
    if db_module.async_session is None:
        logger.warning("Regenerate all skipped: database session not initialized")
        return

    settings = get_settings()
    if not settings.stash_generate_after_sync:
        logger.info("Regenerate all skipped: YTDL_STASH_GENERATE_AFTER_SYNC is disabled")
        return

    # 1. Reset generate_triggered_at for all synced videos.
    async with db_module.async_session() as db:
        try:
            result = await db.execute(
                update(Video)
                .where(
                    Video.status == "synced",
                    Video.stash_scene_id.isnot(None),
                    Video.generate_triggered_at.isnot(None),
                )
                .values(generate_triggered_at=None)
            )
            count = result.rowcount  # type: ignore[union-attr]
            await db.commit()
            logger.info("Regenerate all: reset generate_triggered_at on %d video(s)", count)
        except Exception as e:
            await db.rollback()
            logger.exception("Regenerate all: failed to reset timestamps: %s", e)
            raise

    if count == 0:
        logger.info("Regenerate all: no videos to regenerate")
        return

    # 2. Walk through all synced videos with NULL generate_triggered_at.
    processed = 0
    while True:
        if db_module.async_session is None:
            return
        async with db_module.async_session() as db:
            try:
                result = await db.execute(
                    select(Video)
                    .where(
                        Video.status == "synced",
                        Video.stash_scene_id.isnot(None),
                        Video.generate_triggered_at.is_(None),
                    )
                    .order_by(Video.id)
                    .limit(_BACKFILL_BATCH_SIZE)
                )
                batch = list(result.scalars().all())
            except Exception as e:
                await db.rollback()
                logger.exception("Regenerate all: failed selecting batch: %s", e)
                break

        if not batch:
            break

        for v in batch:
            if db_module.async_session is None:
                return
            async with db_module.async_session() as db2:
                try:
                    async with StashClient.from_settings(settings) as stash:
                        video = await db2.get(Video, v.id)
                        if video is None or video.status != "synced" or not video.stash_scene_id:
                            continue
                        await run_scrape_and_generate(
                            video,
                            video.stash_scene_id,
                            stash,
                            settings,
                            db2,
                            skip_scrape=True,
                            skip_generate=False,
                        )
                    await db2.commit()
                    processed += 1
                except Exception as e:
                    await db2.rollback()
                    logger.warning("Regenerate all: failed for video %s: %s", v.id, e)

            if _BACKFILL_DELAY_SECONDS > 0:
                await asyncio.sleep(_BACKFILL_DELAY_SECONDS)

    logger.info("Regenerate all: triggered generate for %d video(s)", processed)


# ---------------------------------------------------------------------------
# yt-dlp update checks
# ---------------------------------------------------------------------------


async def _do_check_ytdlp_updates() -> None:
    """Core logic: check whether yt-dlp has an update available."""
    await ytdlp_check_for_update()


# Wire coroutine functions into registry (defined above, registered here).
job_registry["check_all_channels"].coro_fn = _do_check_all_channels
job_registry["process_downloads"].coro_fn = _do_process_downloads
job_registry["retry_all_failed"].coro_fn = _do_retry_all_failed
job_registry["check_ytdlp_updates"].coro_fn = _do_check_ytdlp_updates
job_registry["backfill_scrape_generate"].coro_fn = _do_backfill_scrape_generate
job_registry["regenerate_all"].coro_fn = _do_regenerate_all


# ---------------------------------------------------------------------------
# Scheduled wrappers (called by APScheduler)
# ---------------------------------------------------------------------------


async def _channel_checker() -> None:
    """Run every 60s: scan due channels for new videos. max_instances=1."""
    await _run_tracked("check_all_channels", _do_check_all_channels)


async def _download_processor() -> None:
    """Run every 30s: process pending downloads. max_instances=1."""
    await _run_tracked("process_downloads", _do_process_downloads)


async def _ytdlp_update_checker() -> None:
    """Run periodically: check whether yt-dlp has an update available."""
    await _run_tracked("check_ytdlp_updates", _do_check_ytdlp_updates)


# ---------------------------------------------------------------------------
# Manual triggers (called from routes)
# ---------------------------------------------------------------------------

# Hold strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()
_background_tasks_by_job_id: dict[str, asyncio.Task] = {}


def _task_done(task: asyncio.Task) -> None:
    """Cleanup callback for background job tasks."""
    _background_tasks.discard(task)
    # Remove from per-job map if it matches
    for jid, t in list(_background_tasks_by_job_id.items()):
        if t is task:
            _background_tasks_by_job_id.pop(jid, None)
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
    _background_tasks_by_job_id[job_id] = task
    task.add_done_callback(_task_done)
    return True


def stop_job(job_id: str) -> bool:
    """Request a running job to stop by cancelling its background task.

    Returns True if a running task was found and cancelled, False otherwise.
    """
    task = _background_tasks_by_job_id.get(job_id)
    if task is None:
        return False
    if task.done():
        return False
    task.cancel()
    return True


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


def start_scheduler() -> None:
    """Start the scheduler. Call from FastAPI lifespan after init_db."""
    settings = get_settings()
    channel_secs = max(10, settings.channel_check_interval_seconds)
    download_secs = max(10, settings.download_process_interval_seconds)
    ytdlp_hours = max(1, int(settings.ytdlp_update_check_interval_hours))
    scheduler.add_job(
        _channel_checker,
        "interval",
        seconds=channel_secs,
        id="channel_checker",
        max_instances=1,
    )
    scheduler.add_job(
        _download_processor,
        "interval",
        seconds=download_secs,
        id="download_processor",
        max_instances=1,
    )
    scheduler.add_job(
        _ytdlp_update_checker,
        "interval",
        hours=ytdlp_hours,
        id="ytdlp_update_checker",
        max_instances=1,
    )
    scheduler.start()
    logger.info(
        "Scheduler started (channel_checker=%ds, download_processor=%ds, ytdlp_update_checker=%dh)",
        channel_secs,
        download_secs,
        ytdlp_hours,
    )


def stop_scheduler() -> None:
    """Stop the scheduler. Call from FastAPI lifespan on shutdown.
    wait=True lets the current job (channel check or download) finish before stopping.
    """
    scheduler.shutdown(wait=True)
    logger.info("Scheduler stopped")
