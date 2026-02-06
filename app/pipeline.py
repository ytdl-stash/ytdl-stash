"""Download-to-Stash pipeline: orchestration logic tying downloader and Stash client together."""

import asyncio
import logging
from datetime import UTC, datetime

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


async def process_channel_scan(
    channel: Channel, db: AsyncSession, settings: Settings
) -> int:
    """Scan a channel for new videos, insert them as pending, update last_checked_at.

    Returns the count of newly inserted videos.
    """
    try:
        entries = await async_scan_channel(channel.url, settings.cookies_file)
    except RuntimeError as e:
        logger.warning("Channel scan failed for channel %s: %s", channel.id, e)
        raise

    if not entries:
        channel.last_checked_at = datetime.now(UTC)
        await db.commit()
        return 0

    site_ids = [str(e["id"]) for e in entries if e.get("id") is not None]
    if not site_ids:
        channel.last_checked_at = datetime.now(UTC)
        await db.commit()
        return 0

    result = await db.execute(
        select(Video.site_video_id).where(Video.site_video_id.in_(site_ids))
    )
    existing_ids = set(result.scalars().all())

    new_count = 0
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

        oshash = await async_compute_oshash(result["filepath"])
        video.oshash = oshash
        await db.commit()

        video.status = "importing"
        await db.commit()
        logger.info("Video %s: triggering Stash scan", video.id)

        scan_path = result["filepath"]
        if settings.stash_download_dir:
            # Only replace when path is under download_dir (prefix match)
            d = settings.download_dir.rstrip("/")
            s = settings.stash_download_dir.rstrip("/")
            if scan_path == d or scan_path.startswith(d + "/"):
                scan_path = s + scan_path[len(d) :]
        await stash.trigger_scan([scan_path])

        scene = await stash.wait_for_scene(oshash)
        if scene is None:
            raise RuntimeError("Scene not found after scan timeout")

        performer_ids: list[str] = []
        for name in video.performers or []:
            pid = await stash.find_or_create_performer(name)
            performer_ids.append(pid)

        studio_id: str | None = None
        if video.studio:
            studio_id = await stash.find_or_create_studio(video.studio)

        date_str = video.upload_date.isoformat() if video.upload_date else None
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
        video.status = "failed"
        video.error_message = str(e)
        await db.commit()


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
        return

    await process_single_download(video, db, settings, stash)
    await asyncio.sleep(settings.download_delay_seconds)
