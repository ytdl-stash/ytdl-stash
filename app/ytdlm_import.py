"""Import subscriptions and files from YoutubeDL-Material local_db.json."""

import json
import logging
import re
from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Video

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Sentinel URL for the synthetic orphan channel (avoids collision, enables dedup on re-import)
ORPHAN_CHANNEL_URL = "ytdl-stash://ytdlm-import/unlinked"
ORPHAN_CHANNEL_NAME = "YTDLM Import (unlinked)"

# Max size for local_db.json upload (bytes)
IMPORT_FILE_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class YTDLMSubscription(BaseModel):
    """Parsed YTDLM subscription (channel/playlist)."""

    model_config = {"populate_by_name": True}

    id: str
    name: str = ""
    url: str = ""
    is_playlist: bool = Field(alias="isPlaylist", default=False)
    type_: str = Field(alias="type", default="video")
    paused: bool = False


class YTDLMFile(BaseModel):
    """Parsed YTDLM file (downloaded video record)."""

    model_config = {"populate_by_name": True}

    uid: str
    title: str = ""
    url: str = ""
    video_id: str = Field(alias="id", default="")
    uploader: str | None = None
    upload_date: str | None = None
    duration: int | float | None = None
    thumbnail: str | None = None
    path: str | None = None
    sub_id: str | None = None
    extractor: str | None = None
    is_audio: bool = Field(alias="isAudio", default=False)


class YTDLMData(BaseModel):
    """Parsed local_db.json: subscriptions and files (merged, deduplicated by file uid)."""

    subscriptions: list[YTDLMSubscription] = Field(default_factory=list)
    files: list[YTDLMFile] = Field(default_factory=list)


class ImportResult(BaseModel):
    """Result of an import run (dry-run or committed)."""

    channels_created: int = 0
    channels_skipped: int = 0
    videos_created: int = 0
    videos_skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False


def detect_site_from_url(url: str) -> str:
    """Extract site name from URL (e.g. pornhub.com -> pornhub)."""
    if not url or not url.strip():
        return "unknown"
    url_lower = url.lower().strip()
    # Known patterns: domain -> short name
    patterns = [
        (r"pornhub\.com", "pornhub"),
        (r"xvideos\.com", "xvideos"),
        (r"youtube\.com|youtu\.be", "youtube"),
        (r"reddit\.com", "reddit"),
        (r"twitter\.com|x\.com", "twitter"),
        (r"twitch\.tv", "twitch"),
        (r"vimeo\.com", "vimeo"),
        (r"bilibili\.com", "bilibili"),
        (r"onlyfans\.com", "onlyfans"),
        (r"fansly\.com", "fansly"),
    ]
    for pattern, site in patterns:
        if re.search(pattern, url_lower):
            return site
    # Fallback: take first meaningful part of hostname (e.g. "www.example.com" -> "example")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url_lower)
        host = (parsed.netloc or parsed.path or "").strip()
        if host.startswith("www."):
            host = host[4:]
        if "." in host:
            host = host.split(".")[-2] if host.split(".")[-1] in ("com", "net", "org", "io", "co") else host.split(".")[0]
        return host or "unknown"
    except Exception:
        return "unknown"


def normalize_upload_date(date_str: str | None) -> date | None:
    """Parse YTDLM upload_date (YYYYMMDD or YYYY-MM-DD) to date."""
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
        except ValueError:
            return None
    return None


def parse_local_db(json_data: dict) -> YTDLMData:
    """Validate and extract subscriptions + files from raw local_db.json. Merges subscription.videos into files (dedup by uid)."""
    if not isinstance(json_data, dict):
        raise ValueError("Expected a JSON object")
    raw_subs = json_data.get("subscriptions")
    raw_files = json_data.get("files")
    if raw_subs is None and raw_files is None:
        raise ValueError("Missing top-level 'subscriptions' and 'files'; invalid local_db.json")
    subscriptions: list[YTDLMSubscription] = []
    if isinstance(raw_subs, list):
        for item in raw_subs:
            if isinstance(item, dict):
                try:
                    sub_dict = {k: v for k, v in item.items() if k != "videos"}
                    subscriptions.append(YTDLMSubscription.model_validate(sub_dict))
                except Exception as e:
                    logger.debug("Skip invalid subscription item: %s", e)
    files_by_uid: dict[str, YTDLMFile] = {}
    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, dict):
                try:
                    f = YTDLMFile.model_validate(item)
                    files_by_uid[f.uid] = f
                except Exception as e:
                    logger.debug("Skip invalid file item: %s", e)
    # Merge embedded subscription.videos (from raw dicts) into files_by_uid
    if isinstance(raw_subs, list):
        for item in raw_subs:
            if not isinstance(item, dict):
                continue
            sub_id = item.get("id")
            sub_videos = item.get("videos")
            if not isinstance(sub_videos, list) or not sub_id:
                continue
            for v in sub_videos:
                if isinstance(v, dict) and v.get("uid"):
                    try:
                        f = YTDLMFile.model_validate({**v, "sub_id": sub_id})
                        files_by_uid[f.uid] = f
                    except Exception as e:
                        logger.debug("Skip invalid embedded file: %s", e)
    return YTDLMData(subscriptions=subscriptions, files=list(files_by_uid.values()))


def map_subscription_to_channel(
    sub: YTDLMSubscription,
    files: list[YTDLMFile],
    settings: "Settings",
) -> dict:
    """Convert a YTDLM subscription to a Channel field dict. Uses associated files for site fallback from extractor."""
    site = detect_site_from_url(sub.url)
    if site == "unknown" and files:
        for f in files:
            if f.extractor and isinstance(f.extractor, str):
                ex = f.extractor.strip().lower()
                if ex:
                    site = ex.replace(" ", "")
                    break
    return {
        "name": sub.name or "Unnamed",
        "url": sub.url or ORPHAN_CHANNEL_URL,
        "site": site[:50] if len(site) > 50 else site,
        "enabled": not sub.paused,
        "check_interval_hours": settings.default_check_interval_hours,
    }


def map_file_to_video(file: YTDLMFile, channel_id: int) -> dict:
    """Convert a YTDLM file to a Video field dict. Stores original YTDLM JSON in metadata_json."""
    raw = file.model_dump(mode="json")
    metadata_json = json.dumps(raw) if raw else None
    duration_val: int | None = None
    if file.duration is not None:
        try:
            duration_val = int(float(file.duration))
        except (TypeError, ValueError):
            pass
    return {
        "channel_id": channel_id,
        "site_video_id": file.video_id,
        "title": (file.title or "Untitled")[:500],
        "url": (file.url or "")[:2048],
        "upload_date": normalize_upload_date(file.upload_date) if file.upload_date else None,
        "duration_seconds": duration_val,
        "thumbnail_url": (file.thumbnail[:2048] if file.thumbnail else None),
        "original_filename": (file.path[:500] if file.path else None),
        "status": "imported",
        "metadata_json": metadata_json,
    }


async def run_import(
    db: AsyncSession,
    json_data: dict,
    settings: "Settings",
    dry_run: bool = False,
    include_playlists: bool = False,
) -> ImportResult:
    """Parse local_db.json, map to Channels/Videos, deduplicate, and optionally commit. Uses savepoint for dry-run rollback."""
    result = ImportResult(dry_run=dry_run)
    try:
        data = parse_local_db(json_data)
    except Exception as e:
        result.errors.append(f"Parse error: {e!s}")
        return result

    # Filter subscriptions: skip audio; skip playlists unless include_playlists
    subs_to_import: list[YTDLMSubscription] = []
    audio_subs = 0
    playlist_skipped = 0
    for sub in data.subscriptions:
        if (getattr(sub, "type_", None) or getattr(sub, "type", "video")) == "audio":
            audio_subs += 1
            continue
        if getattr(sub, "is_playlist", False) and not include_playlists:
            playlist_skipped += 1
            continue
        subs_to_import.append(sub)
    if audio_subs:
        result.warnings.append(f"Skipped {audio_subs} audio-only subscription(s).")
    if playlist_skipped:
        result.warnings.append(f"Skipped {playlist_skipped} playlist subscription(s). Enable 'Include playlists' to import them.")

    # Filter files: skip audio
    files_to_import: list[YTDLMFile] = []
    audio_files = 0
    for f in data.files:
        if getattr(f, "is_audio", False):
            audio_files += 1
            continue
        files_to_import.append(f)
    if audio_files:
        result.warnings.append(f"Skipped {audio_files} audio-only file(s).")

    # Pre-load existing channel URLs and video site_video_ids
    ch_result = await db.execute(select(Channel.url))
    existing_urls = set(ch_result.scalars().all())
    v_result = await db.execute(select(Video.site_video_id))
    existing_site_video_ids = set(v_result.scalars().all())

    # Build sub_id -> list of files
    files_by_sub: dict[str, list[YTDLMFile]] = {}
    orphan_files: list[YTDLMFile] = []
    sub_ids = {s.id for s in subs_to_import}
    for f in files_to_import:
        sid = getattr(f, "sub_id", None)
        if not sid or sid not in sub_ids:
            orphan_files.append(f)
        else:
            files_by_sub.setdefault(sid, []).append(f)

    savepoint = None
    if dry_run:
        savepoint = await db.begin_nested()

    try:
        ytdlm_id_to_channel_id: dict[str, int] = {}
        orphan_channel_id: int | None = None

        # Import subscriptions as channels
        for sub in subs_to_import:
            if not sub.url or not sub.url.strip():
                result.warnings.append(f"Subscription '{sub.name}' has no URL; skipped.")
                result.channels_skipped += 1
                continue
            if sub.url in existing_urls:
                result.channels_skipped += 1
                # We still need to map sub.id to existing channel for files
                r = await db.execute(select(Channel.id).where(Channel.url == sub.url))
                existing_id = r.scalars().one_or_none()
                if existing_id is not None:
                    ytdlm_id_to_channel_id[sub.id] = existing_id
                continue
            try:
                fields = map_subscription_to_channel(sub, files_by_sub.get(sub.id, []), settings)
                ch = Channel(**fields)
                db.add(ch)
                await db.flush()
                ytdlm_id_to_channel_id[sub.id] = ch.id
                existing_urls.add(sub.url)
                result.channels_created += 1
            except Exception as e:
                logger.exception("Import channel failed for %s", sub.url)
                result.errors.append(f"Channel '{sub.name}': {e!s}")

        # Orphan channel (once)
        if orphan_files and orphan_channel_id is None:
            if ORPHAN_CHANNEL_URL in existing_urls:
                r = await db.execute(select(Channel.id).where(Channel.url == ORPHAN_CHANNEL_URL))
                orphan_channel_id = r.scalars().one_or_none()
            else:
                try:
                    orphan_ch = Channel(
                        name=ORPHAN_CHANNEL_NAME,
                        url=ORPHAN_CHANNEL_URL,
                        site="import",
                        enabled=True,
                        check_interval_hours=settings.default_check_interval_hours,
                    )
                    db.add(orphan_ch)
                    await db.flush()
                    orphan_channel_id = orphan_ch.id
                    existing_urls.add(ORPHAN_CHANNEL_URL)
                    result.channels_created += 1
                except Exception as e:
                    logger.exception("Create orphan channel failed")
                    result.errors.append(f"Orphan channel: {e!s}")
                    orphan_channel_id = -1

        # Import files as videos
        for f in files_to_import:
            vid = getattr(f, "video_id", None) or getattr(f, "id", None)
            if not vid or not str(vid).strip():
                result.warnings.append(f"File '{getattr(f, 'title', '')}' has no site video ID; skipped.")
                continue
            vid_str = str(vid).strip()
            if vid_str in existing_site_video_ids:
                result.videos_skipped += 1
                continue
            sid = getattr(f, "sub_id", None)
            if sid and sid in ytdlm_id_to_channel_id:
                ch_id = ytdlm_id_to_channel_id[sid]
            elif orphan_channel_id is not None and orphan_channel_id > 0:
                ch_id = orphan_channel_id
            else:
                result.warnings.append(f"Video '{vid_str}' has no channel; skipped.")
                continue
            try:
                row = map_file_to_video(f, ch_id)
                row["site_video_id"] = vid_str[:255]
                row["url"] = (row["url"] or "")[:2048]
                video = Video(**row)
                db.add(video)
                await db.flush()
                existing_site_video_ids.add(vid_str)
                result.videos_created += 1
            except Exception as e:
                logger.exception("Import video failed for %s", vid_str)
                result.errors.append(f"Video '{vid_str}': {e!s}")

        if dry_run and savepoint is not None:
            await savepoint.rollback()
    except Exception as e:
        if savepoint is not None:
            await savepoint.rollback()
        logger.exception("Import failed")
        result.errors.append(f"Import failed: {e!s}")

    return result
