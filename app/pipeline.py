"""Download-to-Stash pipeline: orchestration logic tying downloader and Stash client together."""

import asyncio
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.download_control import download_control
from app.download_progress import download_progress
from app.downloader import (
    _parse_date,
    DownloadCancelled,
    async_compute_oshash,
    async_download_video,
    async_extract_video_info,
    async_scan_channel,
)
from app.models import Channel, Video
from app.performer_sync import is_placeholder_name
from app.studio_sync import sync_channel_studio
from app.stash_client import StashClient, _normalize_performer_name

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
        scan_result = await async_scan_channel(channel.url, settings)
    except RuntimeError as e:
        logger.warning("Channel scan failed for channel %s: %s", channel.id, e)
        raise

    entries = scan_result["entries"]
    channel_meta = scan_result.get("channel_meta") or {}

    # Update channel name if it's still a placeholder (domain, "unknown", etc.)
    # — metadata extraction may have failed when the channel was first added.
    meta_name = channel_meta.get("name")
    if meta_name and is_placeholder_name(channel.name):
        logger.info(
            "Channel %s: updating name from placeholder '%s' to '%s'",
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


async def _resync_scene_from_stash(video: Video, stash: StashClient) -> None:
    """Fetch the latest scene data from Stash to verify scraper results were applied.

    Called automatically after scraping. The scene's thumbnail is served
    dynamically from Stash via URL, so no local field update is needed.
    Note: generate is fire-and-forget so its results won't be visible yet.
    """
    if not video.stash_scene_id:
        return
    scene = await stash.find_scene_by_id(video.stash_scene_id)
    if not scene:
        logger.warning(
            "Video %s: scene %s not found in Stash during re-sync",
            video.id,
            video.stash_scene_id,
        )
        return

    logger.info(
        "Video %s: re-synced scene %s from Stash (title=%r, performers=%d, tags=%d)",
        video.id,
        video.stash_scene_id,
        scene.get("title", ""),
        len(scene.get("performers") or []),
        len(scene.get("tags") or []),
    )

    if scene.get("organized") is not True:
        try:
            await stash.update_scene(scene_id=video.stash_scene_id, organized=True)
            logger.info(
                "Video %s: marked scene %s as organized in Stash",
                video.id,
                video.stash_scene_id,
            )
        except Exception as e:
            logger.warning(
                "Video %s: failed to mark scene %s as organized (non-fatal): %s",
                video.id,
                video.stash_scene_id,
                e,
            )


async def process_single_download(
    video: Video, db: AsyncSession, settings: Settings, stash: StashClient
) -> None:
    """Run the full lifecycle for one video: download -> oshash -> scan -> match -> tag."""
    try:
        download_control.set_active(video.id)
        video.status = "downloading"
        video.error_message = None
        download_progress.clear(video.id)
        await db.commit()
        logger.info("Video %s: downloading", video.id)

        # ------------------------------------------------------------------
        # Pre-download duration check
        # The channel scan uses extract_flat=True which often omits duration.
        # If min_duration_seconds is set and we don't have a duration yet,
        # extract full metadata (no download) to check before wasting bandwidth.
        # ------------------------------------------------------------------
        await db.refresh(video, ["channel"])
        channel = video.channel
        if channel.min_duration_seconds is not None:
            if video.duration_seconds is None:
                logger.info(
                    "Video %s: extracting metadata to check duration (min=%ds)",
                    video.id, channel.min_duration_seconds,
                )
                try:
                    info = await async_extract_video_info(video.url, settings)
                    if info.get("duration") is not None:
                        video.duration_seconds = int(info["duration"])
                        await db.commit()
                except Exception:
                    logger.debug(
                        "Video %s: metadata extraction failed, will check after download",
                        video.id, exc_info=True,
                    )

            if (
                video.duration_seconds is not None
                and video.duration_seconds < channel.min_duration_seconds
            ):
                logger.info(
                    "Video %s: skipped — duration %ds < min %ds",
                    video.id, video.duration_seconds, channel.min_duration_seconds,
                )
                video.status = "skipped"
                video.error_message = (
                    f"Too short ({video.duration_seconds}s < "
                    f"{channel.min_duration_seconds}s minimum)"
                )
                await db.commit()
                download_progress.clear(video.id)
                return

        last_hook_ts = 0.0
        last_hook_status: str | None = None

        def _hook(d: dict) -> None:
            # Called from the download worker thread. Throttle UI updates.
            nonlocal last_hook_ts, last_hook_status
            if download_control.is_cancel_requested(video.id):
                raise DownloadCancelled("Download cancelled by user")
            now = time.monotonic()
            status = d.get("status")
            if status != last_hook_status or (now - last_hook_ts) >= 0.25:
                download_progress.update_from_ytdlp_hook(video.id, d)
                last_hook_ts = now
                last_hook_status = str(status) if status is not None else None

        result = await async_download_video(
            video.url,
            settings.download_dir,
            settings.ytdlp_output_template,
            settings,
            progress_hook=_hook,
        )

        # Stop requested during the download (or right as it finished)
        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

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

        # ------------------------------------------------------------------
        # Post-download duration safety net
        # Catches videos whose duration was unavailable during both the
        # channel scan AND the pre-download metadata extraction.
        # ------------------------------------------------------------------
        if (
            channel.min_duration_seconds is not None
            and video.duration_seconds is not None
            and video.duration_seconds < channel.min_duration_seconds
        ):
            logger.info(
                "Video %s: skipped after download — duration %ds < min %ds",
                video.id, video.duration_seconds, channel.min_duration_seconds,
            )
            try:
                os.remove(result["filepath"])
                logger.info("Video %s: deleted file %s", video.id, result["filepath"])
            except OSError as exc:
                logger.warning(
                    "Video %s: could not delete file %s: %s",
                    video.id, result["filepath"], exc,
                )
            video.status = "skipped"
            video.error_message = (
                f"Too short ({video.duration_seconds}s < "
                f"{channel.min_duration_seconds}s minimum)"
            )
            await db.commit()
            download_progress.clear(video.id)
            return

        video.status = "downloaded"
        if video.downloaded_at is None:
            video.downloaded_at = datetime.now(UTC)
        await db.commit()
        download_progress.clear(video.id)
        logger.info("Video %s: downloaded", video.id)

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        logger.info("Video %s: computing oshash for %s", video.id, result["filepath"])
        oshash = await async_compute_oshash(result["filepath"])
        video.oshash = oshash
        await db.commit()
        logger.info("Video %s: oshash=%s", video.id, oshash)

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        video.status = "importing"
        await db.commit()

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        scan_path = result["filepath"]
        if settings.stash_download_dir:
            # Only replace when path is under download_dir (prefix match)
            d = settings.download_dir.rstrip("/")
            s = settings.stash_download_dir.rstrip("/")
            if scan_path == d or scan_path.startswith(d + "/"):
                scan_path = s + scan_path[len(d) :]
        logger.info("Video %s: triggering Stash scan for %s", video.id, scan_path)
        await stash.trigger_scan([scan_path])

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        logger.info("Video %s: waiting for Stash scene (oshash=%s)", video.id, oshash)
        scene = await stash.wait_for_scene(oshash)
        if scene is None:
            raise RuntimeError("Scene not found after scan timeout")
        logger.info("Video %s: Stash scene found (id=%s)", video.id, scene["id"])

        await db.refresh(video, ["channel"])
        performer_ids: list[str] = []
        channel = video.channel
        channel_norm = _normalize_performer_name(channel.name or "").lower() if channel else ""

        ch_result = await db.execute(select(Channel))
        channels = list(ch_result.scalars().all())
        channel_by_name: dict[str, Channel] = {}
        for ch in channels:
            key = _normalize_performer_name(ch.name or "").lower()
            if key:
                channel_by_name[key] = ch

        for name in video.performers or []:
            norm_name = _normalize_performer_name(name).lower()
            if not norm_name:
                continue
            ch: Channel | None = None
            if channel and channel_norm == norm_name:
                ch = channel
            elif norm_name in channel_by_name:
                ch = channel_by_name[norm_name]
            if ch:
                if ch.stash_performer_id:
                    pid = ch.stash_performer_id
                    logger.info("Video %s: performer '%s' -> Stash id %s (cached)", video.id, name, pid)
                else:
                    pid = await stash.find_or_create_performer_by_url(
                        name=name,
                        url=ch.url,
                        image_url=ch.performer_image_url,
                    )
                    logger.info("Video %s: performer '%s' -> Stash id %s (channel)", video.id, name, pid)
            else:
                pid = await stash.find_or_create_performer(name)
                logger.info("Video %s: performer '%s' -> Stash id %s", video.id, name, pid)
            performer_ids.append(pid)

        studio_id: str | None = None
        if channel and not channel.stash_studio_id:
            try:
                await sync_channel_studio(channel, db, stash, settings)
            except Exception as e:
                logger.warning(
                    "Video %s: studio sync failed for channel %s (non-fatal): %s",
                    video.id,
                    channel.id,
                    e,
                )
        if channel and channel.stash_studio_id:
            studio_id = channel.stash_studio_id
            logger.info("Video %s: studio from channel -> Stash id %s", video.id, studio_id)

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
        if video.synced_at is None:
            video.synced_at = datetime.now(UTC)
        await db.commit()
        logger.info("Video %s: synced to Stash scene %s", video.id, scene["id"])

        # ------------------------------------------------------------------
        # Post-sync actions (best-effort — failures are logged, not raised)
        # ------------------------------------------------------------------

        # 1. Scrape the video URL via Stash's configured scrapers
        if settings.stash_scrape_after_sync:
            try:
                logger.info("Video %s: scraping URL %s via Stash", video.id, video.url)
                scraped = await stash.scrape_scene_url(video.url)
                if scraped:
                    await stash.apply_scraped_scene(
                        scene_id=scene["id"],
                        scraped=scraped,
                        existing_performer_ids=performer_ids or None,
                        existing_studio_id=studio_id,
                    )
            except Exception as e:
                logger.warning("Video %s: post-sync scrape failed (non-fatal): %s", video.id, e)

        # 2. Trigger Stash generate (covers, previews, sprites, phashes)
        if settings.stash_generate_after_sync:
            try:
                logger.info("Video %s: triggering Stash generate for scene %s", video.id, scene["id"])
                await stash.trigger_generate(
                    scene_ids=[scene["id"]],
                    covers=settings.stash_generate_covers,
                    previews=settings.stash_generate_previews,
                    sprites=settings.stash_generate_sprites,
                    phashes=settings.stash_generate_phashes,
                )
            except Exception as e:
                logger.warning("Video %s: post-sync generate failed (non-fatal): %s", video.id, e)

        # 3. Re-sync scene from Stash to confirm scraper results were applied
        try:
            await _resync_scene_from_stash(video, stash)
        except Exception as e:
            logger.warning("Video %s: post-sync scene re-sync failed (non-fatal): %s", video.id, e)

    except DownloadCancelled as e:
        download_progress.clear(video.id)
        logger.info("Video %s cancelled: %s", video.id, e)
        try:
            await db.rollback()
            refreshed = await db.get(Video, video.id)
            if refreshed is not None:
                refreshed.status = "cancelled"
                refreshed.error_message = "Cancelled by user"
                await db.commit()
        except Exception:
            logger.exception("Video %s: could not persist cancelled status", video.id)

    except Exception as e:
        download_progress.clear(video.id)
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
    finally:
        # Always clear the active marker (even if cancelled/failed)
        download_control.clear_active(video.id)
        download_control.clear_cancel(video.id)


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
