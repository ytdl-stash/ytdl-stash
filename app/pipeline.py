"""Download-to-Stash pipeline: orchestration logic tying downloader and Stash client together."""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.downloader import (
    _parse_date,
    async_compute_oshash,
    async_download_video,
    async_scan_channel,
)
from app.models import Channel, Video
from app.stash_client import StashClient

logger = logging.getLogger(__name__)

# Per-channel asyncio locks to prevent concurrent scans of the same channel
# (e.g. manual "Check Now" racing with the scheduled channel checker).
_channel_locks: dict[int, asyncio.Lock] = {}


def _get_channel_lock(channel_id: int) -> asyncio.Lock:
    """Return (and lazily create) an asyncio.Lock for the given channel."""
    if channel_id not in _channel_locks:
        _channel_locks[channel_id] = asyncio.Lock()
    return _channel_locks[channel_id]


async def process_channel_scan(
    channel: Channel, db: AsyncSession, settings: Settings
) -> int:
    """Scan a channel for new videos, insert them as pending, update last_checked_at.

    Acquires a per-channel lock to prevent concurrent scans (e.g. a manual
    "Check Now" racing with the scheduled channel checker).

    Returns the count of newly inserted videos.
    """
    lock = _get_channel_lock(channel.id)
    logger.info("Acquiring scan lock for channel %s", channel.id)
    async with lock:
        logger.info("Scan lock acquired for channel %s, starting scan", channel.id)
        return await _process_channel_scan_locked(channel, db, settings)


async def _process_channel_scan_locked(
    channel: Channel, db: AsyncSession, settings: Settings
) -> int:
    """Inner scan logic, called while holding the per-channel lock."""
    try:
        scan_result = await async_scan_channel(channel.url, settings.cookies_file)
    except RuntimeError as e:
        logger.warning("Channel scan failed for channel %s: %s", channel.id, e)
        raise

    entries = scan_result["entries"]
    channel_meta = scan_result.get("channel_meta") or {}

    # Update channel name if it's still the domain fallback (i.e. metadata
    # extraction failed when the channel was first added).
    meta_name = channel_meta.get("name")
    if meta_name and channel.name == channel.site:
        logger.info(
            "Channel %s: updating name from fallback '%s' to '%s'",
            channel.id, channel.name, meta_name,
        )
        channel.name = meta_name

    # Back-fill thumbnail if we didn't have one before.
    meta_thumb = channel_meta.get("thumbnail")
    if meta_thumb and not channel.performer_image_url:
        logger.info("Channel %s: updating thumbnail from scan metadata", channel.id)
        channel.performer_image_url = meta_thumb

    if not entries:
        logger.info("Channel %s: yt-dlp returned no entries", channel.id)
        channel.last_checked_at = datetime.now(UTC)
        await db.commit()
        return 0

    site_ids = [str(e["id"]) for e in entries if e.get("id") is not None]
    if not site_ids:
        logger.info("Channel %s: yt-dlp returned %d entries but none had valid IDs", channel.id, len(entries))
        channel.last_checked_at = datetime.now(UTC)
        await db.commit()
        return 0

    logger.info("Channel %s: yt-dlp returned %d entries, %d with valid IDs", channel.id, len(entries), len(site_ids))

    result = await db.execute(
        select(Video.site_video_id).where(Video.site_video_id.in_(site_ids))
    )
    existing_ids = set(result.scalars().all())

    # Pre-compute filter thresholds from channel settings
    min_upload_date: date | None = None
    if channel.max_video_age_days is not None:
        min_upload_date = (datetime.now(UTC) - timedelta(days=channel.max_video_age_days)).date()

    new_count = 0
    skipped_age = 0
    skipped_duration = 0
    for entry in entries:
        site_video_id = (
            str(entry["id"]) if entry.get("id") is not None else None
        )
        url = entry.get("url") or entry.get("webpage_url")
        if not site_video_id or not url or site_video_id in existing_ids:
            continue

        upload_date = (
            _parse_date(entry.get("upload_date"))
            if entry.get("upload_date")
            else None
        )
        duration = entry.get("duration")
        duration_seconds = int(duration) if duration is not None else None

        # Filter: skip videos older than max_video_age_days
        if min_upload_date is not None and upload_date is not None:
            if upload_date < min_upload_date:
                skipped_age += 1
                continue

        # Filter: skip videos shorter than min_duration_seconds
        if channel.min_duration_seconds is not None and duration_seconds is not None:
            if duration_seconds < channel.min_duration_seconds:
                skipped_duration += 1
                continue

        video = Video(
            channel_id=channel.id,
            site_video_id=site_video_id,
            title=entry.get("title") or "",
            url=str(url),
            upload_date=upload_date,
            duration_seconds=duration_seconds,
            thumbnail_url=entry.get("thumbnail"),
            status="pending",
        )
        db.add(video)
        existing_ids.add(site_video_id)  # Prevent duplicates within same batch
        new_count += 1

    channel.last_checked_at = datetime.now(UTC)
    await db.commit()
    if skipped_age or skipped_duration:
        logger.info(
            "Channel %s: found %d new videos (skipped %d too old, %d too short)",
            channel.id, new_count, skipped_age, skipped_duration,
        )
    else:
        logger.info("Channel %s: found %d new videos", channel.id, new_count)
    return new_count


async def process_single_download(
    video: Video, db: AsyncSession, settings: Settings, stash: StashClient
) -> None:
    """Run the full lifecycle for one video: download -> oshash -> scan -> match -> tag."""
    try:
        video.status = "downloading"
        video.error_message = None
        await db.commit()
        logger.info("Video %s: downloading", video.id)

        result = await async_download_video(
            video.url,
            settings.download_dir,
            settings.ytdlp_output_template,
            settings.cookies_file,
        )

        video.original_filename = result["filename"]
        video.title = result["title"]
        video.upload_date = result["upload_date"]
        video.performers = result["performers"]
        video.studio = result.get("studio")
        video.duration_seconds = (
            int(result["duration"]) if result.get("duration") is not None else None
        )
        video.thumbnail_url = result.get("thumbnail_url")
        video.metadata_json = result.get("metadata_json")
        video.status = "downloaded"
        await db.commit()
        logger.info("Video %s: downloaded", video.id)

        logger.info("Video %s: computing oshash for %s", video.id, result["filepath"])
        oshash = await async_compute_oshash(result["filepath"])
        video.oshash = oshash
        await db.commit()
        logger.info("Video %s: oshash=%s", video.id, oshash)

        video.status = "importing"
        await db.commit()

        scan_path = result["filepath"]
        if settings.stash_download_dir:
            # Only replace when path is under download_dir (prefix match)
            d = settings.download_dir.rstrip("/")
            s = settings.stash_download_dir.rstrip("/")
            if scan_path == d or scan_path.startswith(d + "/"):
                scan_path = s + scan_path[len(d) :]
        logger.info("Video %s: triggering Stash scan for %s", video.id, scan_path)
        await stash.trigger_scan([scan_path])

        logger.info("Video %s: waiting for Stash scene (oshash=%s)", video.id, oshash)
        scene = await stash.wait_for_scene(oshash)
        if scene is None:
            raise RuntimeError("Scene not found after scan timeout")
        logger.info("Video %s: Stash scene found (id=%s)", video.id, scene["id"])

        performer_ids: list[str] = []
        for name in video.performers or []:
            pid = await stash.find_or_create_performer(name)
            logger.info("Video %s: performer '%s' -> Stash id %s", video.id, name, pid)
            performer_ids.append(pid)

        studio_id: str | None = None
        if video.studio:
            studio_id = await stash.find_or_create_studio(video.studio)
            logger.info("Video %s: studio '%s' -> Stash id %s", video.id, video.studio, studio_id)

        date_str = video.upload_date.isoformat() if video.upload_date else None
        logger.info("Video %s: updating Stash scene %s with metadata", video.id, scene["id"])
        await stash.update_scene(
            scene_id=scene["id"],
            title=video.title,
            urls=[video.url],
            date=date_str,
            studio_id=studio_id,
            performer_ids=performer_ids,
        )

        video.stash_scene_id = scene["id"]
        video.status = "synced"
        await db.commit()
        logger.info("Video %s: synced to Stash scene %s", video.id, scene["id"])

    except Exception as e:
        logger.exception("Video %s failed: %s", video.id, e)
        # The session may be in a dirty/broken state after the original error,
        # so rollback first to clear it, then mark the video as failed.
        try:
            await db.rollback()
            # Re-fetch the video to get a clean ORM object after rollback
            refreshed = await db.get(Video, video.id)
            if refreshed is not None:
                refreshed.status = "failed"
                refreshed.error_message = str(e)[:2000]  # cap to avoid huge tracebacks
                await db.commit()
        except Exception:
            logger.exception(
                "Video %s: could not persist failed status (will be recovered on restart)",
                video.id,
            )


async def process_pending_downloads(
    db: AsyncSession, settings: Settings, stash: StashClient
) -> None:
    """Process one pending video (FIFO), then wait the configured delay."""
    result = await db.execute(
        select(Video)
        .where(Video.status == "pending")
        .order_by(Video.created_at)
        .limit(1)
    )
    video = result.scalar_one_or_none()
    if video is None:
        logger.debug("Download processor: no pending videos in queue")
        return

    logger.info("Download processor: picked up video %s (%s)", video.id, video.title)
    await process_single_download(video, db, settings, stash)
    await asyncio.sleep(settings.download_delay_seconds)
