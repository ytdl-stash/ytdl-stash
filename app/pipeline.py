"""Download-to-Stash pipeline: orchestration logic tying downloader and Stash client together."""

import asyncio
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, or_, select
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

    # ------------------------------------------------------------------
    # Imported-video dedup: YTDLM imports may have stored the video title
    # (or another non-standard value) as site_video_id, so yt-dlp's real
    # ID won't match.  Build secondary lookup maps by URL and title for
    # videos belonging to *this* channel so we can recognise them and
    # back-fill the correct site_video_id instead of re-downloading.
    # ------------------------------------------------------------------
    ch_videos_result = await db.execute(
        select(Video).where(Video.channel_id == channel.id)
    )
    ch_videos: list[Video] = list(ch_videos_result.scalars().all())

    url_to_video: dict[str, Video] = {}
    title_to_video: dict[str, Video] = {}
    for v in ch_videos:
        if v.url:
            url_to_video[v.url] = v
        if v.title:
            title_to_video[v.title] = v

    # Pre-compute filter thresholds from channel settings
    min_upload_date: date | None = None
    if channel.max_video_age_days is not None:
        min_upload_date = (datetime.now(UTC) - timedelta(days=channel.max_video_age_days)).date()

    new_count = 0
    backfilled = 0
    skipped_age = 0
    skipped_duration = 0
    for entry in entries:
        site_video_id = (
            str(entry["id"]) if entry.get("id") is not None else None
        )
        url = entry.get("url") or entry.get("webpage_url")
        if not site_video_id or not url:
            continue

        # Primary dedup: exact site_video_id match (normal case)
        if site_video_id in existing_ids:
            continue

        # Secondary dedup: match by URL or title against existing channel
        # videos whose site_video_id doesn't match (e.g. YTDLM imports
        # stored the video title as site_video_id instead of the real ID).
        # Try URL first, then fall back to title within the same channel.
        entry_title = entry.get("title") or ""
        matched_video: Video | None = url_to_video.get(str(url))
        if matched_video is None and entry_title:
            matched_video = title_to_video.get(entry_title)
        if matched_video is not None and matched_video.site_video_id != site_video_id:
            old_sid = matched_video.site_video_id
            matched_video.site_video_id = site_video_id
            # Also back-fill URL in case the imported record had a different one
            if matched_video.url != str(url):
                matched_video.url = str(url)
            existing_ids.add(site_video_id)
            backfilled += 1
            logger.info(
                "Video %s: back-filled site_video_id '%s' -> '%s' (matched by URL/title)",
                matched_video.id, old_sid, site_video_id,
            )
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
    parts = [f"found {new_count} new videos"]
    if backfilled:
        parts.append(f"back-filled {backfilled} imported")
    if skipped_age:
        parts.append(f"skipped {skipped_age} too old")
    if skipped_duration:
        parts.append(f"skipped {skipped_duration} too short")
    logger.info("Channel %s: %s", channel.id, ", ".join(parts))
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


async def generate_for_scene(
    video: Video,
    scene_id: str,
    stash: StashClient,
    settings: Settings,
) -> None:
    """Trigger Stash generate for a scene and wait for the job to finish.

    Centralizes the generate call shared by the download pipeline, the
    backfill/regenerate jobs, and the manual re-sync routes.

    When ``stash_expect_renamer_on_import`` is set, a renamer plugin may move
    the file *during* the generate job — especially while Stash's job queue is
    busy and the move is delayed past our import settle. In that mode we re-read
    the scene's primary path after generate and, if it moved, let it settle and
    regenerate once so the artifacts aren't left keyed to a stale location. With
    the flag off (the default, no renamer) this is a plain
    trigger-generate-and-wait, so behavior is unchanged for those setups.

    Raises on a hard generate failure (mutation or job error) so callers leave
    ``generate_triggered_at`` unset and the Backfill job can retry.
    """
    expect_renamer = settings.stash_expect_renamer_on_import
    max_attempts = 2 if expect_renamer else 1
    # Only the renamer path needs the pre-generate location; skip the extra
    # query entirely when no renamer is configured (the common case).
    path_before = (
        stash.scene_primary_path(await stash.find_scene_by_id(scene_id))
        if expect_renamer
        else None
    )

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "Video %s: triggering Stash generate for scene %s%s",
            video.id, scene_id,
            f" (attempt {attempt}/{max_attempts})" if max_attempts > 1 else "",
        )
        job_id = await stash.trigger_generate(
            scene_ids=[scene_id],
            covers=settings.stash_generate_covers,
            previews=settings.stash_generate_previews,
            sprites=settings.stash_generate_sprites,
            phashes=settings.stash_generate_phashes,
        )
        if job_id:
            await stash.wait_for_job(job_id)

        if not expect_renamer:
            return  # No renamer configured — nothing can race the generate.

        # Renamer expected: confirm the file didn't move during the generate
        # job. If it did, the artifacts may be stale, so settle and regenerate.
        settled = await stash.wait_for_scene_path_stable(
            scene_id,
            settle=settings.stash_organized_settle_seconds,
            total_timeout=settings.stash_import_settle_timeout_seconds,
        )
        if settled is None or settled == path_before:
            return
        logger.warning(
            "Video %s: scene %s file moved during generate (%r -> %r); regenerating",
            video.id, scene_id, path_before, settled,
        )
        path_before = settled

    logger.warning(
        "Video %s: scene %s file path still unstable after %d generate attempts; "
        "keeping best-effort generation result",
        video.id, scene_id, max_attempts,
    )


async def run_scrape_and_generate(
    video: Video,
    scene_id: str,
    stash: StashClient,
    settings: Settings,
    db: AsyncSession,
    *,
    performer_ids: list[str] | None = None,
    studio_id: str | None = None,
    set_organized: bool = False,
    skip_scrape: bool = False,
    skip_generate: bool = False,
) -> None:
    """Run scrape and generate for a synced scene, update tracking timestamps on success.

    Generate runs before set_organized so that the generate job sees the file
    at its original location. After generate completes, organized is set;
    any file-move rule runs after generate is done, avoiding races.

    *skip_scrape* / *skip_generate* let callers (e.g. the backfill job)
    run only the step that is actually needed.
    """
    # 1. Scrape the video URL via Stash's configured scrapers
    if settings.stash_scrape_after_sync and not skip_scrape:
        try:
            download_progress.set_phase(video.id, "Scraping")
            logger.info("Video %s: scraping URL %s via Stash", video.id, video.url)
            scraped = await stash.scrape_scene_url(video.url)
            if scraped:
                await stash.apply_scraped_scene(
                    scene_id=scene_id,
                    scraped=scraped,
                    existing_performer_ids=performer_ids or None,
                    existing_studio_id=studio_id,
                )
            video.scrape_attempted_at = datetime.now(UTC)
        except Exception as e:
            logger.warning("Video %s: post-sync scrape failed (non-fatal): %s", video.id, e)

    # 2. Trigger Stash generate and wait for completion (before organized).
    #    generate_for_scene guards against a renamer moving the file mid-job.
    if settings.stash_generate_after_sync and not skip_generate:
        try:
            download_progress.set_phase(video.id, "Generating")
            await generate_for_scene(video, scene_id, stash, settings)
            video.generate_triggered_at = datetime.now(UTC)
        except Exception as e:
            logger.warning("Video %s: post-sync generate failed (non-fatal): %s", video.id, e)

    # 3. Mark scene as organized (after generate; file-move happens last)
    if set_organized:
        try:
            await stash.update_scene(scene_id=scene_id, organized=True)
            logger.info(
                "Video %s: marked scene %s as organized in Stash",
                video.id, scene_id,
            )
        except Exception as e:
            logger.warning(
                "Video %s: failed to mark scene %s as organized (non-fatal): %s",
                video.id, scene_id, e,
            )

    # 4. Re-sync scene from Stash to confirm scraper results were applied
    if not skip_scrape:
        try:
            await _resync_scene_from_stash(video, stash)
        except Exception as e:
            logger.warning("Video %s: post-sync scene re-sync failed (non-fatal): %s", video.id, e)

    # This is the final pipeline step for every caller — the main download, the
    # early-scene-lookup shortcut, and the scheduler backfill/regenerate jobs.
    # Clear the live phase here so callers that don't otherwise clear can't leave
    # a stale "Scraping"/"Generating" entry pinning the video in the active panel.
    download_progress.clear(video.id)


async def hard_reset_video(
    video: Video, stash: "StashClient | None", settings: Settings
) -> None:
    """Tear a video down for a fully fresh download + import.

    Destroys any linked Stash scene (scene, file, and generated content),
    deletes the local file if it lives under ``download_dir``, clears all
    tracking fields, and sets ``status='pending'``. Shared by Redownload and by
    Retry (when a scene is linked). The caller owns the StashClient lifecycle
    and the DB commit; ``stash`` may be ``None`` when no client is available
    (the link is still cleared so processing isn't blocked on a Stash outage).
    """
    if video.stash_scene_id:
        if stash is not None:
            try:
                await stash.destroy_scene(
                    video.stash_scene_id, delete_file=True, delete_generated=True
                )
                logger.info(
                    "Video %s: destroyed Stash scene %s", video.id, video.stash_scene_id
                )
            except Exception as e:
                logger.warning(
                    "Video %s: failed to destroy Stash scene %s (non-fatal): %s",
                    video.id, video.stash_scene_id, e,
                )
        else:
            logger.warning(
                "Video %s: no Stash client available to destroy scene %s; clearing link only",
                video.id, video.stash_scene_id,
            )

    if video.original_filename:
        candidate = os.path.abspath(os.path.join(settings.download_dir, video.original_filename))
        download_root = os.path.abspath(settings.download_dir)
        # Guard against an absolute/foreign original_filename escaping download_dir.
        within_download_dir = (
            candidate == download_root or candidate.startswith(download_root + os.sep)
        )
        if within_download_dir and os.path.isfile(candidate):
            try:
                os.remove(candidate)
                logger.info("Video %s: deleted existing file %s", video.id, candidate)
            except OSError as e:
                logger.warning("Video %s: could not delete %s: %s", video.id, candidate, e)
        elif not within_download_dir:
            logger.info(
                "Video %s: skipping local delete of %r (outside download_dir; Stash-managed)",
                video.id, video.original_filename,
            )

    video.original_filename = None
    video.oshash = None
    video.stash_scene_id = None
    video.downloaded_at = None
    video.synced_at = None
    video.scrape_attempted_at = None
    video.generate_triggered_at = None
    video.status = "pending"
    video.error_message = None


async def _apply_metadata_and_sync(
    video: Video,
    scene: dict,
    stash: StashClient,
    settings: Settings,
    db: AsyncSession,
) -> None:
    """Resolve performers/studio, update scene metadata in Stash, mark synced, run post-sync."""
    await db.refresh(video, ["channel"])
    channel = video.channel
    channel_norm = _normalize_performer_name(channel.name or "").lower() if channel else ""

    ch_result = await db.execute(select(Channel))
    channels = list(ch_result.scalars().all())
    channel_by_name: dict[str, Channel] = {}
    for ch in channels:
        key = _normalize_performer_name(ch.name or "").lower()
        if key:
            channel_by_name[key] = ch

    performer_ids: list[str] = []
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
            else:
                pid = await stash.find_or_create_performer_by_url(
                    name=name,
                    url=ch.url,
                    image_url=ch.performer_image_url,
                )
        else:
            pid = await stash.find_or_create_performer(name)
        performer_ids.append(pid)

    studio_id: str | None = None
    if channel and channel.stash_studio_id:
        # Verify the studio still exists in Stash (may have been deleted)
        existing = await stash.get_studio(channel.stash_studio_id)
        if existing:
            studio_id = channel.stash_studio_id
        else:
            logger.warning(
                "Video %s: linked studio %s no longer exists in Stash, skipping studio assignment",
                video.id, channel.stash_studio_id,
            )

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
    if video.synced_at is None:
        video.synced_at = datetime.now(UTC)
    logger.info("Video %s: synced to Stash scene %s", video.id, scene["id"])

    await run_scrape_and_generate(
        video, scene["id"], stash, settings, db,
        performer_ids=performer_ids, studio_id=studio_id,
        set_organized=True,
    )


async def _cancellable_download(
    video_id: int,
    url: str,
    download_dir: str,
    output_template: str,
    settings,
    progress_hook,
) -> dict:
    """Run async_download_video in a separate task with periodic cancel checks.

    asyncio.to_thread() cannot be interrupted — if yt-dlp hangs (network
    stall, etc.) the cooperative progress-hook cancel never fires.  This
    wrapper polls the cancel flag every 2 seconds so we can abort even when
    the download thread is stuck.  A configurable timeout
    (download_timeout_seconds) acts as a safety net.
    """
    task = asyncio.create_task(
        async_download_video(url, download_dir, output_template, settings, progress_hook=progress_hook)
    )
    download_control.set_download_task(video_id, task)
    check_interval = 2.0
    timeout = getattr(settings, "download_timeout_seconds", 0) or 0
    elapsed = 0.0
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=check_interval)
            if done:
                break
            elapsed += check_interval
            if download_control.is_cancel_requested(video_id):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise DownloadCancelled("Download cancelled by user")
            if timeout > 0 and elapsed >= timeout:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise RuntimeError(f"Download timed out after {int(elapsed)}s")
        try:
            return task.result()
        except asyncio.CancelledError:
            # Inner task was cancelled (e.g. via cancel_download_task from
            # stop route).  Convert to DownloadCancelled so the caller's
            # except-handler persists the "cancelled" status correctly.
            raise DownloadCancelled("Download task was cancelled")
    except asyncio.CancelledError:
        # Job-level cancellation (e.g. stop_job) — propagate to download task.
        # Convert to DownloadCancelled so process_single_download handles it.
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise DownloadCancelled("Download cancelled (job stopped)")
    finally:
        download_control.clear_download_task(video_id)


async def process_single_download(
    video: Video, db: AsyncSession, settings: Settings, stash: StashClient
) -> None:
    """Run the full lifecycle for one video: download -> oshash -> scan -> match -> tag."""
    # Capture the id before entering the try block so error handlers can use
    # the plain integer even after a rollback expires the ORM object's attrs.
    video_id = video.id
    try:
        download_control.set_active(video_id)
        video.error_message = None
        download_progress.clear(video.id)
        await db.refresh(video, ["channel"])
        channel = video.channel

        if channel is None:
            logger.warning(
                "Video %s: channel was deleted, skipping download", video.id
            )
            return

        was_import_retry = video.status == "downloaded"

        # ------------------------------------------------------------------
        # Early scene lookup: if scene already in Stash (e.g. retry after
        # timeout, or Stash moved file), skip download and scan entirely.
        # ------------------------------------------------------------------
        download_progress.set_phase(video.id, "Checking Stash")
        scene: dict | None = None
        if video.oshash:
            scene = await stash.find_scene_by_oshash(video.oshash)
        if scene is None and video.title:
            scene = await stash.find_scene_by_title(video.title)
        if scene:
            logger.info(
                "Video %s: scene already in Stash (id=%s), skipping download",
                video.id, scene["id"],
            )
            # Keep our record of the on-disk filename in sync with Stash — a
            # renamer plugin may have moved/renamed the file since we last saw it.
            existing_path = stash.scene_primary_path(scene)
            if existing_path:
                video.original_filename = os.path.basename(existing_path)
            await _apply_metadata_and_sync(video, scene, stash, settings, db)
            await db.commit()
            return

        # ------------------------------------------------------------------
        # File-existence fast-path
        # If a previous attempt already downloaded the file (e.g. server
        # crashed mid-import, or startup recovery reset us to pending),
        # skip the download entirely and jump to the oshash/import stage.
        # ------------------------------------------------------------------
        video.status = "downloading"
        await db.commit()
        logger.info("Video %s: downloading", video.id)
        existing_filepath: str | None = None

        if video.original_filename:
            candidate = os.path.join(settings.download_dir, video.original_filename)
            if os.path.isfile(candidate):
                existing_filepath = candidate
                logger.info(
                    "Video %s: file already on disk (%s), skipping download",
                    video.id, candidate,
                )

        if existing_filepath is None:
            # Retry set status to "downloaded" but file is gone (e.g. Stash moved it)
            # and we didn't find the scene in Stash via early lookup.
            if was_import_retry:
                raise RuntimeError(
                    "File not found and scene not in Stash. Use Redownload to fetch again."
                )
            # ------------------------------------------------------------------
            # Pre-download metadata check
            # The channel scan uses extract_flat=True which often omits duration
            # and upload_date. If min_duration_seconds or max_video_age_days is
            # set and we're missing those fields, extract full metadata (no
            # download) to check before wasting bandwidth.
            # ------------------------------------------------------------------
            needs_metadata = (
                (channel.min_duration_seconds is not None and video.duration_seconds is None)
                or (channel.max_video_age_days is not None and video.upload_date is None)
            )
            if needs_metadata:
                download_progress.set_phase(video.id, "Extracting metadata")
                logger.info(
                    "Video %s: extracting metadata to check filters (min_duration=%s, max_age=%s)",
                    video.id,
                    channel.min_duration_seconds,
                    channel.max_video_age_days,
                )
                try:
                    info = await async_extract_video_info(video.url, settings)
                    if info.get("duration") is not None:
                        video.duration_seconds = int(info["duration"])
                    if info.get("upload_date") is not None:
                        video.upload_date = _parse_date(info["upload_date"])
                    await db.commit()
                except Exception:
                    logger.debug(
                        "Video %s: metadata extraction failed, will check after download",
                        video.id,
                        exc_info=True,
                    )

            # Filter: skip if duration below min (when known)
            if (
                channel.min_duration_seconds is not None
                and video.duration_seconds is not None
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

            # Filter: skip if older than max age (when known)
            if channel.max_video_age_days is not None and video.upload_date is not None:
                min_upload_date = (
                    datetime.now(UTC) - timedelta(days=channel.max_video_age_days)
                ).date()
                if video.upload_date < min_upload_date:
                    logger.info(
                        "Video %s: skipped — upload date %s older than max %d days",
                        video.id, video.upload_date, channel.max_video_age_days,
                    )
                    video.status = "skipped"
                    video.error_message = (
                        f"Too old (uploaded {video.upload_date}, max age "
                        f"{channel.max_video_age_days} days)"
                    )
                    await db.commit()
                    download_progress.clear(video.id)
                    return

            download_progress.set_phase(video.id, "Downloading")

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

            result = await _cancellable_download(
                video.id,
                video.url,
                settings.download_dir,
                settings.ytdlp_output_template,
                settings,
                _hook,
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

            # ------------------------------------------------------------------
            # Post-download filter safety nets
            # Catches videos whose duration/upload_date was unavailable during
            # both the channel scan AND the pre-download metadata extraction.
            # ------------------------------------------------------------------
            def _skip_after_download(error_msg: str) -> None:
                try:
                    os.remove(result["filepath"])
                    logger.info("Video %s: deleted file %s", video.id, result["filepath"])
                except OSError as exc:
                    logger.warning(
                        "Video %s: could not delete file %s: %s",
                        video.id, result["filepath"], exc,
                    )
                video.status = "skipped"
                video.error_message = error_msg

            if (
                channel.min_duration_seconds is not None
                and video.duration_seconds is not None
                and video.duration_seconds < channel.min_duration_seconds
            ):
                logger.info(
                    "Video %s: skipped after download — duration %ds < min %ds",
                    video.id, video.duration_seconds, channel.min_duration_seconds,
                )
                _skip_after_download(
                    f"Too short ({video.duration_seconds}s < "
                    f"{channel.min_duration_seconds}s minimum)",
                )
                await db.commit()
                download_progress.clear(video.id)
                return

            if (
                channel.max_video_age_days is not None
                and video.upload_date is not None
            ):
                min_upload_date = (
                    datetime.now(UTC) - timedelta(days=channel.max_video_age_days)
                ).date()
                if video.upload_date < min_upload_date:
                    logger.info(
                        "Video %s: skipped after download — upload date %s older than max %d days",
                        video.id, video.upload_date, channel.max_video_age_days,
                    )
                    _skip_after_download(
                        f"Too old (uploaded {video.upload_date}, max age "
                        f"{channel.max_video_age_days} days)",
                    )
                    await db.commit()
                    download_progress.clear(video.id)
                    return

            # Use filepath from the download result for the rest of the pipeline
            existing_filepath = result["filepath"]

        video.status = "downloaded"
        if video.downloaded_at is None:
            video.downloaded_at = datetime.now(UTC)
        await db.commit()
        download_progress.clear(video.id)
        logger.info("Video %s: downloaded", video.id)

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        filepath = existing_filepath
        download_progress.set_phase(video.id, "Computing file hash")
        logger.info("Video %s: computing oshash for %s", video.id, filepath)
        oshash = await async_compute_oshash(filepath)
        video.oshash = oshash
        await db.commit()
        logger.info("Video %s: oshash=%s", video.id, oshash)

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        video.status = "importing"
        await db.commit()

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        download_progress.set_phase(video.id, "Scanning in Stash")
        scan_path = filepath
        if settings.stash_download_dir:
            # Only replace when path is under download_dir (prefix match)
            d = settings.download_dir.rstrip("/")
            s = settings.stash_download_dir.rstrip("/")
            if scan_path == d or scan_path.startswith(d + "/"):
                scan_path = s + scan_path[len(d) :]
        logger.info("Video %s: triggering Stash scan for %s", video.id, scan_path)
        scan_job_id = await stash.trigger_scan([scan_path])

        if download_control.is_cancel_requested(video.id):
            raise DownloadCancelled("Download cancelled by user")

        logger.info("Video %s: waiting for Stash scan job %s", video.id, scan_job_id)
        await stash.wait_for_job(scan_job_id)
        scene = await stash.find_scene_by_oshash(oshash)
        if scene is None:
            scene = await stash.find_scene_by_title(video.title)
        if scene is None:
            raise RuntimeError("Scene not found in Stash after scan job completed")
        logger.info("Video %s: Stash scene found (id=%s)", video.id, scene["id"])

        # The scan job is done, but a Stash renamer plugin may still be moving/
        # renaming the file asynchronously (it isn't part of the scan job).
        # Wait for the path to settle BEFORE update_scene/generate so they don't
        # race a file mid-move, and record where the file actually ended up.
        download_progress.set_phase(video.id, "Finalizing in Stash")
        try:
            settled_path = await stash.wait_for_scene_path_stable(
                scene["id"],
                settle=settings.stash_organized_settle_seconds,
                total_timeout=settings.stash_import_settle_timeout_seconds,
                require_change=settings.stash_expect_renamer_on_import,
            )
        except Exception as e:
            # Best-effort: the scene is already found and the download succeeded.
            # A transient Stash error here must not fail the video.
            logger.warning("Video %s: path-settle check failed (non-fatal): %s", video.id, e)
            settled_path = None
        if settled_path:
            video.original_filename = os.path.basename(settled_path)
            logger.info("Video %s: file settled at %s", video.id, settled_path)

        download_progress.set_phase(video.id, "Syncing metadata")
        await _apply_metadata_and_sync(video, scene, stash, settings, db)
        await db.commit()
        download_progress.clear(video.id)

    except DownloadCancelled as e:
        download_progress.clear(video_id)
        logger.info("Video %s cancelled: %s", video_id, e)
        try:
            await db.rollback()
            refreshed = await db.get(Video, video_id)
            if refreshed is not None:
                refreshed.status = "cancelled"
                refreshed.error_message = "Cancelled by user"
                await db.commit()
        except Exception:
            logger.exception("Video %s: could not persist cancelled status", video_id)

    except Exception as e:
        download_progress.clear(video_id)
        logger.exception("Video %s failed: %s", video_id, e)
        # The session may be in a dirty/broken state after the original error,
        # so rollback first to clear it, then mark the video as failed.
        try:
            await db.rollback()
            # Re-fetch the video to get a clean ORM object after rollback
            refreshed = await db.get(Video, video_id)
            if refreshed is not None:
                refreshed.status = "failed"
                refreshed.error_message = str(e)[:2000]  # cap to avoid huge tracebacks
                await db.commit()
        except Exception:
            logger.exception(
                "Video %s: could not persist failed status (will be recovered on restart)",
                video_id,
            )
    finally:
        # Always clear the active marker (even if cancelled/failed)
        download_control.clear_active(video_id)
        download_control.clear_cancel(video_id)


async def process_pending_downloads(
    db: AsyncSession, settings: Settings, stash: StashClient
) -> None:
    """Process one pending or downloaded (import-retry) video (FIFO), then wait the configured delay."""
    result = await db.execute(
        select(Video)
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
        .limit(1)
    )
    video = result.scalar_one_or_none()
    if video is None:
        logger.debug("Download processor: no pending videos in queue")
        return

    logger.info("Download processor: picked up video %s (%s)", video.id, video.title)
    await process_single_download(video, db, settings, stash)
    await asyncio.sleep(settings.download_delay_seconds)
