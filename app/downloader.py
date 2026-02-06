"""yt-dlp wrapper: channel scanning, video download, and oshash computation."""

import asyncio
import json
import logging
import os
import struct
from datetime import date

import yt_dlp
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)


def _parse_date(date_str: str | None) -> date | None:
    """Parse yt-dlp YYYYMMDD string to a Python date. Returns None for invalid input."""
    if not date_str:
        return None
    try:
        s = str(date_str).strip()
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError, TypeError):
        return None


def _extract_performers(info: dict) -> list[str]:
    """Extract performer names from yt-dlp info_dict. Deduplicates (case-insensitive), preserves order."""
    performers: list[str] = []

    for field in ("cast", "actors"):
        val = info.get(field)
        if isinstance(val, list):
            performers.extend(val)
        elif val:
            performers.append(str(val))

    if not performers and info.get("uploader"):
        performers.append(str(info["uploader"]))

    seen: set[str] = set()
    unique: list[str] = []
    for p in performers:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            unique.append(p)

    return unique


def compute_oshash(filepath: str) -> str:
    """Compute OpenSubtitles hash (first + last 64KB + file size). Returns 16-char lowercase hex."""
    block_size = 65536  # 64KB
    min_size = block_size * 2  # 128KB — oshash requires at least this for meaningful results
    file_size = os.path.getsize(filepath)

    if file_size < min_size:
        raise ValueError(
            f"File too small for oshash ({file_size} bytes, need >= {min_size}): {filepath}"
        )

    hash_value = file_size

    with open(filepath, "rb") as f:
        buf = f.read(block_size)
        hash_value += sum(struct.unpack(f"<{len(buf) // 8}Q", buf))

        f.seek(max(0, file_size - block_size))
        buf = f.read(block_size)
        hash_value += sum(struct.unpack(f"<{len(buf) // 8}Q", buf))

    hash_value &= 0xFFFFFFFFFFFFFFFF
    return f"{hash_value:016x}"


def extract_channel_metadata(url: str, cookies_file: str | None = None) -> dict:
    """Extract channel-level metadata (name, thumbnail, description) from a channel URL.
    Uses non-flat extract so avatar/thumbnail is available. Returns dict with name, thumbnail, description keys.
    """
    opts: dict = {
        "extract_flat": False,
        "quiet": True,
        "no_warnings": True,
        "playlist_items": "0",  # Only channel page, no video entries
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        logger.debug("Extracting channel metadata: %s", url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        logger.warning("Channel metadata extraction failed for %s: %s", url, e)
        raise RuntimeError(f"Channel metadata failed for {url!r}: {e}") from e

    if not info:
        return {"name": "", "thumbnail": None, "description": None}

    raw_name = info.get("uploader") or info.get("channel")
    name = str(raw_name) if raw_name else ""
    thumbnail = info.get("thumbnail")
    if not thumbnail and isinstance(info.get("thumbnails"), list) and info["thumbnails"]:
        thumb = info["thumbnails"][-1]
        thumbnail = thumb.get("url") if isinstance(thumb, dict) else None
    description = info.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description) if description else None
    return {"name": name or "", "thumbnail": thumbnail, "description": description}


def scan_channel(url: str, cookies_file: str | None = None) -> list[dict]:
    """List video entries from a channel URL without downloading. Returns list of dicts with id, title, url, upload_date, uploader, duration, thumbnail."""
    opts: dict = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        logger.debug("Scanning channel: %s", url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        logger.warning("Channel scan failed for %s: %s", url, e)
        raise RuntimeError(f"Channel scan failed for {url!r}: {e}") from e

    raw_entries = info.get("entries") or []
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]

    return [
        {
            "id": entry.get("id"),
            "title": entry.get("title"),
            "url": entry.get("url") or entry.get("webpage_url"),
            "upload_date": entry.get("upload_date"),
            "uploader": entry.get("uploader"),
            "duration": entry.get("duration"),
            "thumbnail": entry.get("thumbnail"),
        }
        for entry in raw_entries
        if entry
    ]


def download_video(
    url: str,
    output_dir: str,
    output_template: str,
    cookies_file: str | None = None,
) -> dict:
    """Download a single video and return filepath plus metadata dict."""
    outtmpl = f"{output_dir}/{output_template}"
    opts: dict = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        logger.info("Downloading: %s -> %s", url, outtmpl)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # prefer the actual filepath from requested_downloads (accounts for
            # post-processing / format merging that may change the extension)
            requested = info.get("requested_downloads") or []
            if requested and requested[0].get("filepath"):
                filepath = requested[0]["filepath"]
            else:
                filepath = ydl.prepare_filename(info)
        logger.info("Download complete: %s", filepath)
    except DownloadError as e:
        logger.warning("Download failed for %s: %s", url, e)
        raise RuntimeError(f"Download failed for {url!r}: {e}") from e

    return {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
        "title": info.get("title", ""),
        "upload_date": _parse_date(info.get("upload_date")),
        "performers": _extract_performers(info),
        "studio": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail_url": info.get("thumbnail"),
        "metadata_json": json.dumps(info, default=str),
    }


async def async_compute_oshash(filepath: str) -> str:
    """Async wrapper for compute_oshash. Use this from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(compute_oshash, filepath)


async def async_extract_channel_metadata(
    url: str, cookies_file: str | None = None
) -> dict:
    """Async wrapper for extract_channel_metadata. Use from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(extract_channel_metadata, url, cookies_file)


async def async_scan_channel(url: str, cookies_file: str | None = None) -> list[dict]:
    """Async wrapper for scan_channel. Use this from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(scan_channel, url, cookies_file)


async def async_download_video(
    url: str,
    output_dir: str,
    output_template: str,
    cookies_file: str | None = None,
) -> dict:
    """Async wrapper for download_video. Use this from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(
        download_video, url, output_dir, output_template, cookies_file
    )
