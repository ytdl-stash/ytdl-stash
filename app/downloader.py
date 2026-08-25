"""yt-dlp wrapper: channel scanning, video download, and oshash computation."""

import asyncio
import json
import logging
import os
import re
import struct
from datetime import date
from typing import Any
from urllib.parse import unquote, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError
from collections.abc import Callable

from app.config import Settings
from app import ytdlp_patches

logger = logging.getLogger(__name__)

# Apply local patches for upstream yt-dlp bugs before any YoutubeDL is built.
# All yt-dlp usage funnels through this module, so this is the one chokepoint
# that guarantees the patches are live for web, scheduler, and script paths.
ytdlp_patches.apply_patches()


class DownloadCancelled(Exception):
    """Raised to cooperatively abort a yt-dlp download when the user requests stop."""


def _parse_date(date_str: str | None) -> date | None:
    """Parse yt-dlp YYYYMMDD string to a Python date. Returns None for invalid input."""
    if not date_str:
        return None
    try:
        s = str(date_str).strip()
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError, TypeError):
        return None


# Matches bare domain names like "pornhub.com", "redtube.net", "site.co.uk".
# Uses a broad TLD list to catch real domains while allowing dotted names like
# "Mr.Beast" or "Dr.Wolf" through.
_DOMAIN_RE = re.compile(
    r"^[\w-]+"                          # second-level label
    r"(?:\.[\w-]+)*"                    # optional sub-labels (e.g. ".co")
    r"\."                               # mandatory dot before TLD
    r"(?:com|net|org|co|io|tv|xxx|adult|porn|sex|tube|me|info|biz|us|uk|de|fr|ru|jp|br|in|au|ca|nl|se|no|fi|dk|pl|cz|ch|at|be|es|it|pt)$",
    re.IGNORECASE,
)


def _looks_like_site_name(value: str, info: dict) -> bool:
    """Return True if *value* appears to be a site/domain name rather than a real channel name.

    Heuristics:
    - Matches the yt-dlp extractor key (e.g. "PornHub", "RedTube") case-insensitively,
      or when the extractor key starts with the value (e.g. "Pornhub" vs "PornHubUser").
    - Looks like a bare domain (e.g. "pornhub.com") based on TLD matching.
    - Matches the second-level domain from the channel URL (e.g. "pornhub" from pornhub.com).
    """
    v = value.strip().lower()
    if not v:
        return True

    v_clean = v.replace(".", "").replace(" ", "").replace("-", "").replace("_", "")

    # Compare against the extractor name (e.g. "PornHub", "PornHubUser")
    extractor = (info.get("extractor_key") or info.get("extractor") or "").strip().lower()
    extractor_clean = extractor.replace(".", "").replace(" ", "") if extractor else ""
    if extractor and v == extractor:
        return True
    # e.g. "Pornhub.com" vs extractor "PornHub"
    if extractor_clean and v_clean == extractor_clean:
        return True
    # e.g. "Pornhub" vs extractor "PornHubUser" — site name is prefix of extractor key
    if extractor_clean and v_clean and extractor_clean.startswith(v_clean):
        return True

    # Bare domain with a recognized TLD (e.g. "pornhub.com")
    if _DOMAIN_RE.match(v):
        return True

    # Compare against the URL's domain base (e.g. "pornhub" from https://www.pornhub.com/...)
    for url_field in ("webpage_url", "original_url", "url"):
        raw_url = info.get(url_field)
        if raw_url:
            hostname = urlparse(str(raw_url)).hostname or ""
            if hostname.lower().startswith("www."):
                hostname = hostname[4:]
            parts = hostname.rsplit(".", 1)
            domain_base = (parts[0].split(".")[-1] if parts else "") or ""
            domain_base_clean = domain_base.lower().replace("-", "").replace("_", "")
            if domain_base_clean and v_clean == domain_base_clean:
                return True
            break

    return False


def normalize_channel_url(url: str) -> str:
    """Normalize channel URLs for sites where the profile page doesn't enumerate
    videos but a subpage does (e.g. PornHub /model/X → /model/X/videos).

    With extract_flat=True, yt-dlp may not follow internal redirects to the
    videos subpage; normalizing the URL before calling yt-dlp fixes that.
    """
    u = (url or "").strip()
    if not u:
        return u
    # PornHub: /model/X or /pornstar/X or /channels/X or /users/X → append /videos
    m = re.match(
        r"(https?://(?:[^/]+\.)?pornhub\.(?:com|net|org)"
        r"/(?:model|pornstar|channels|users)/[^/?#]+)"
        r"(?:/(?!videos).*)?$",
        u,
        re.I,
    )
    if m:
        return m.group(1) + "/videos"
    return u


def _extract_performers(info: dict) -> list[str]:
    """Extract performer names from yt-dlp info_dict. Deduplicates (case-insensitive), preserves order.

    Falls back to ``uploader`` only when no ``cast``/``actors`` fields exist
    *and* the uploader doesn't look like a site/domain name.
    """
    performers: list[str] = []

    for field in ("cast", "actors"):
        val = info.get(field)
        if isinstance(val, list):
            performers.extend(val)
        elif val:
            performers.append(str(val))

    if not performers and info.get("uploader"):
        uploader = str(info["uploader"])
        if not _looks_like_site_name(uploader, info):
            performers.append(uploader)

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


def _extract_name_from_url(info: dict) -> str:
    """Extract a channel/creator name from the URL path as a last resort.

    Many sites use URL patterns like ``/<type>/<name>/videos`` where ``<type>``
    is a category like ``model``, ``pornstar``, ``channels``, ``creators``, etc.
    When yt-dlp doesn't populate any metadata fields, the URL slug is the best
    available name.

    Returns the slug with hyphens/underscores replaced by spaces and
    title-cased, or empty string if no pattern matches.
    """
    # Known path prefixes that precede a channel/creator slug.
    _CHANNEL_PATH_PREFIXES = {
        "model", "pornstar", "pornstars", "channels", "channel", "users",
        "creators", "profiles", "amateur-channels", "model-channels",
        "pornstar-channels",
    }

    for url_field in ("webpage_url", "original_url", "url"):
        raw_url = info.get(url_field)
        if not raw_url:
            continue
        path = urlparse(str(raw_url)).path.strip("/")
        segments = path.split("/")
        if len(segments) >= 2 and segments[0].lower() in _CHANNEL_PATH_PREFIXES:
            slug = unquote(segments[1])
            # Clean up the slug: replace separators with spaces, title-case
            candidate = slug.replace("-", " ").replace("_", " ").strip()
            if candidate:
                # Title-case only if the slug is all-lowercase; otherwise
                # preserve the original casing (e.g. "HottiesTwo").
                if candidate == candidate.lower():
                    candidate = candidate.title()
                logger.info(
                    "Channel name extracted from URL path: %r (url=%s)",
                    candidate, raw_url,
                )
                return candidate
        break  # Only try the first available URL field

    return ""


def _extract_channel_name(info: dict) -> str:
    """Extract the channel/uploader name from a yt-dlp info dict.

    Tries several fields that different extractors populate, in priority order.
    Skips values that look like a site or domain name (e.g. "Pornhub.com").
    Falls back to parsing the channel name from the URL path when all fields
    are empty or rejected.
    """
    for field in ("channel", "uploader", "uploader_id", "title", "playlist_title"):
        raw = info.get(field)
        if raw:
            candidate = str(raw).strip()
            if candidate and not _looks_like_site_name(candidate, info):
                logger.info("Channel name accepted: %r (from field '%s')", candidate, field)
                return candidate
            elif candidate:
                logger.info(
                    "Channel name candidate rejected (looks like site name): "
                    "field='%s' value=%r extractor=%r",
                    field, candidate,
                    info.get("extractor_key") or info.get("extractor") or "",
                )

    # Last resort: try to extract the name from the URL path slug.
    url_name = _extract_name_from_url(info)
    if url_name:
        return url_name

    logger.info(
        "No usable channel name found. Available info keys: %s",
        [k for k in ("channel", "uploader", "uploader_id", "title", "playlist_title") if info.get(k)],
    )
    return ""


def _extract_thumbnail(info: dict) -> str | None:
    """Extract the best channel avatar/profile thumbnail from a yt-dlp info dict.

    yt-dlp's ``thumbnails`` list for channels often contains a mix of video
    thumbnails and channel avatars.  We prefer entries whose ``id`` contains
    ``"avatar"`` (YouTube, some other extractors) since those are the actual
    channel profile pictures.  If no avatar-tagged entry exists we fall back
    to the top-level ``thumbnail`` field, then the last entry in the list.
    """
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        # Prefer avatar-tagged entries (e.g. YouTube "avatar_uncropped-…")
        avatar_entries = [
            t for t in thumbnails
            if isinstance(t, dict) and isinstance(t.get("id", ""), str)
            and "avatar" in t["id"].lower()
        ]
        if avatar_entries:
            # Pick the largest avatar (last in list — yt-dlp sorts ascending)
            best = avatar_entries[-1]
            url = best.get("url")
            if url:
                logger.debug("Using avatar thumbnail: id=%s url=%s", best.get("id"), url)
                return url

    # Fall back to top-level thumbnail field
    thumb = info.get("thumbnail")
    if thumb:
        return thumb

    # Last resort: last entry in thumbnails list
    if isinstance(thumbnails, list) and thumbnails:
        entry = thumbnails[-1]
        thumb = entry.get("url") if isinstance(entry, dict) else None
    return thumb


def _parse_json_obj(text: str, *, name: str) -> dict[str, Any]:
    """Parse a JSON object string from settings. Returns {} on invalid input."""
    s = (text or "").strip()
    if not s:
        return {}
    try:
        val = json.loads(s)
    except Exception:
        logger.warning("Invalid %s JSON (must be an object); ignoring", name)
        return {}
    if not isinstance(val, dict):
        logger.warning("Invalid %s JSON (must be an object); ignoring", name)
        return {}
    return val


def _build_common_ytdlp_opts(settings: Settings) -> dict[str, Any]:
    """Build common yt-dlp options shared by scan + download."""
    opts: dict[str, Any] = {}

    if settings.ytdlp_proxy:
        opts["proxy"] = settings.ytdlp_proxy
    if settings.ytdlp_socket_timeout_seconds is not None:
        opts["socket_timeout"] = settings.ytdlp_socket_timeout_seconds
    if settings.ytdlp_impersonate:
        opts["impersonate"] = settings.ytdlp_impersonate

    headers: dict[str, Any] = {}
    headers.update(
        _parse_json_obj(
            settings.ytdlp_http_headers_json, name="YTDL_YTDLP_HTTP_HEADERS_JSON"
        )
    )
    if settings.ytdlp_user_agent:
        headers["User-Agent"] = settings.ytdlp_user_agent
    if settings.ytdlp_referer:
        headers["Referer"] = settings.ytdlp_referer
    if headers:
        opts["http_headers"] = headers

    return opts


def _build_scan_opts(settings: Settings) -> dict[str, Any]:
    base: dict[str, Any] = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
    }
    if settings.cookies_file:
        base["cookiefile"] = settings.cookies_file

    base.update(_build_common_ytdlp_opts(settings))
    base.update(
        _parse_json_obj(settings.ytdlp_scan_opts_json, name="YTDL_YTDLP_SCAN_OPTS_JSON")
    )
    return base


def _build_download_opts(
    settings: Settings,
    *,
    outtmpl: str,
    progress_hook: Callable[[dict], None] | None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "retries": settings.ytdlp_retries,
        "fragment_retries": settings.ytdlp_fragment_retries,
        # Prevent re-downloading files that already exist at the output path.
        # The pipeline has its own file-existence fast-path, but this is a
        # safety net in case yt-dlp is reached for an already-downloaded file.
        "nooverwrites": True,
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    if settings.ytdlp_format:
        opts["format"] = settings.ytdlp_format
    if progress_hook is not None:
        # yt-dlp calls these with a progress dict (from the download thread)
        opts["progress_hooks"] = [progress_hook]

    opts.update(_build_common_ytdlp_opts(settings))
    opts.update(
        _parse_json_obj(
            settings.ytdlp_download_opts_json, name="YTDL_YTDLP_DOWNLOAD_OPTS_JSON"
        )
    )
    return opts


def extract_channel_metadata(url: str, settings: Settings) -> dict:
    """Extract channel-level metadata (name, thumbnail, description) from a channel URL.

    Uses flat extraction first (fast) so we get playlist/channel-level info
    without downloading metadata for every video.  Falls back to non-flat
    with ``playlistend=1`` when the name or thumbnail is missing (flat mode
    often omits channel/uploader fields on many extractors).  Processing one
    video entry allows the extractor to populate channel/uploader from
    video-level metadata.  As a final fallback, the channel name is parsed
    from the URL path slug (e.g. ``/model/hottiestwo/videos`` → ``hottiestwo``).

    Returns dict with name, thumbnail, description, and video_count keys.
    """
    url = normalize_channel_url(url)
    opts = _build_scan_opts(settings)

    try:
        logger.debug("Extracting channel metadata (flat): %s", url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # Consume entries while ydl context is alive (they may be lazy).
            raw_entries = info.get("entries") or [] if info else []
            if isinstance(raw_entries, dict):
                raw_entries = [raw_entries]
            flat_entries = _flatten_entries(raw_entries)
            video_count = len(flat_entries)
    except DownloadError as e:
        logger.warning("Channel metadata extraction failed for %s: %s", url, e)
        raise RuntimeError(f"Channel metadata failed for {url!r}: {e}") from e

    if not info:
        return {"name": "", "thumbnail": None, "description": None, "video_count": 0}

    name = _extract_channel_name(info)
    thumbnail = _extract_thumbnail(info)

    # If name or thumbnail missing from flat mode, try non-flat with one video
    # entry.  Flat extraction is fast but many extractors only populate
    # channel/uploader fields in non-flat mode.  Using playlistend=1 (instead
    # of 0) ensures at least one video entry is processed — some extractors
    # only populate channel/uploader from video-level metadata.
    if not thumbnail or not name:
        logger.info(
            "Flat extraction incomplete for %s (name=%r, thumbnail=%s) — "
            "retrying with non-flat extraction (1 entry)",
            url, name or "(empty)", "yes" if thumbnail else "no",
        )
        try:
            nf_opts = dict(opts)
            nf_opts.update(
                {
                    "extract_flat": False,
                    "playlistend": 1,  # Process one video entry for metadata
                }
            )
            with yt_dlp.YoutubeDL(nf_opts) as ydl:
                nf_info = ydl.extract_info(url, download=False)
                # Consume entries while ydl context is alive (may be lazy).
                nf_entries: list[dict] = []
                if nf_info:
                    raw_nf = nf_info.get("entries") or []
                    if isinstance(raw_nf, dict):
                        raw_nf = [raw_nf]
                    nf_entries = [e for e in raw_nf if isinstance(e, dict)]
            if nf_info:
                if not thumbnail:
                    thumbnail = _extract_thumbnail(nf_info)
                if not name:
                    name = _extract_channel_name(nf_info)
                # If playlist-level fields are still empty, try the first
                # video entry — video-level metadata often has the real
                # channel/uploader name even when the playlist wrapper doesn't.
                if not name:
                    for entry in nf_entries:
                        name = _extract_channel_name(entry)
                        if name:
                            logger.info(
                                "Channel name found from first video entry: %r",
                                name,
                            )
                            break
        except Exception:
            logger.debug("Non-flat metadata fallback failed for %s", url, exc_info=True)

    description = info.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description) if description else None

    logger.info(
        "Channel metadata result for %s: name=%r, thumbnail=%s, description=%s, video_count=%s",
        url,
        name or "(empty)",
        "yes" if thumbnail else "no",
        "yes" if description else "no",
        video_count,
    )
    return {
        "name": name or "",
        "thumbnail": thumbnail,
        "description": description,
        "video_count": video_count,
    }


def _flatten_entries(entries) -> list[dict]:
    """Recursively flatten nested playlist/tab entries into individual video entries.

    Some sites return channel -> sub-playlist -> videos. This ensures we
    always get the leaf video entries regardless of nesting depth.
    """
    result: list[dict] = []
    for entry in entries:
        if not entry or not isinstance(entry, dict):
            continue
        sub = entry.get("entries")
        if sub:
            # This entry is a sub-playlist / tab — recurse into it
            result.extend(_flatten_entries(sub))
        else:
            result.append(entry)
    return result


def _derive_video_id(entry: dict) -> str | None:
    """Derive a usable video ID from an entry dict.

    Prefers the explicit ``id`` field. Falls back to extracting an
    identifier from the URL so entries without ``id`` are not silently
    dropped.
    """
    vid = entry.get("id")
    if vid is not None:
        return str(vid)
    # Fallback: use the URL itself as a unique key
    raw_url = entry.get("url") or entry.get("webpage_url")
    if raw_url:
        return str(raw_url)
    return None


def scan_channel(url: str, settings: Settings) -> dict:
    """Scan a channel URL and return video entries plus channel-level metadata.

    Returns a dict with:
      - ``entries``: list[dict] — individual video entries (id, title, url, …)
      - ``channel_meta``: dict — channel-level info (name, thumbnail) extracted
        from the same yt-dlp call so no extra request is needed.
    """
    url = normalize_channel_url(url)
    opts = _build_scan_opts(settings)

    try:
        logger.info("yt-dlp scanning channel URL: %s", url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Consume entries INSIDE the context manager so lazy generators
            # are fully evaluated while the ydl instance is still alive.
            raw_entries = info.get("entries") or []
            if isinstance(raw_entries, dict):
                raw_entries = [raw_entries]
            flat_entries = _flatten_entries(raw_entries)
    except DownloadError as e:
        logger.warning("Channel scan failed for %s: %s", url, e)
        raise RuntimeError(f"Channel scan failed for {url!r}: {e}") from e

    logger.info("yt-dlp scan complete for %s: %d raw entries found", url, len(flat_entries))

    # Extract channel-level metadata from the top-level info dict.
    # This is "free" — the data comes from the same yt-dlp call.
    channel_name = _extract_channel_name(info) if info else ""
    channel_thumbnail = _extract_thumbnail(info) if info else None

    results: list[dict] = []
    for entry in flat_entries:
        if not entry:
            continue
        video_id = _derive_video_id(entry)
        video_url = entry.get("url") or entry.get("webpage_url")
        if not video_id or not video_url:
            continue
        results.append(
            {
                "id": video_id,
                "title": entry.get("title"),
                "url": video_url,
                "upload_date": entry.get("upload_date"),
                "uploader": entry.get("uploader"),
                "duration": entry.get("duration"),
                "thumbnail": entry.get("thumbnail"),
            }
        )
    logger.info("yt-dlp scan for %s: %d usable video entries", url, len(results))
    return {
        "entries": results,
        "channel_meta": {
            "name": channel_name,
            "thumbnail": channel_thumbnail,
        },
    }


def download_video(
    url: str,
    output_dir: str,
    output_template: str,
    settings: Settings,
    progress_hook: Callable[[dict], None] | None = None,
) -> dict:
    """Download a single video and return filepath plus metadata dict."""
    outtmpl = f"{output_dir}/{output_template}"
    opts = _build_download_opts(settings, outtmpl=outtmpl, progress_hook=progress_hook)

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
        "filename": os.path.relpath(filepath, output_dir),
        "title": info.get("title", ""),
        "upload_date": _parse_date(info.get("upload_date")),
        "performers": _extract_performers(info),
        "studio": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail_url": info.get("thumbnail"),
        "metadata_json": json.dumps(info, default=str),
    }


def extract_video_info(url: str, settings: Settings) -> dict:
    """Extract video metadata (duration, title, upload_date, etc.) without downloading.

    Used for pre-download filter checks when ``extract_flat=True`` during the
    channel scan didn't return duration or upload_date.

    Returns dict with ``duration`` (int | None), ``title`` (str),
    and ``upload_date`` (str | None, yt-dlp YYYYMMDD format).
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    opts.update(_build_common_ytdlp_opts(settings))
    # Re-use scan opts for consistency (proxy, headers, etc.)
    opts.update(
        _parse_json_obj(settings.ytdlp_scan_opts_json, name="YTDL_YTDLP_SCAN_OPTS_JSON")
    )

    try:
        logger.info("Extracting metadata (no download): %s", url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        duration = info.get("duration") if info else None
        upload_date_raw = info.get("upload_date") if info else None
        # Normalize upload_date to YYYYMMDD string; yt-dlp may return int or str
        upload_date_str: str | None = None
        if upload_date_raw is not None:
            parsed = _parse_date(
                str(upload_date_raw).strip() if isinstance(upload_date_raw, str) else str(upload_date_raw)
            )
            if parsed is not None:
                upload_date_str = parsed.strftime("%Y%m%d")
        return {
            "duration": int(duration) if duration is not None else None,
            "title": (info.get("title") or "") if info else "",
            "upload_date": upload_date_str,
        }
    except DownloadError as e:
        logger.warning("Metadata extraction failed for %s: %s", url, e)
        return {"duration": None, "title": "", "upload_date": None}


# Raised when a creator/playlist page is pasted into the Add Video flow. The
# route matches on this to offer the "Add as channel instead" handoff, so keep
# the two in sync via this constant rather than a literal.
PLAYLIST_URL_ERROR = "This URL is a channel or playlist, not a single video."


def extract_single_video_metadata(url: str, settings: Settings) -> dict:
    """Extract full metadata for one video URL, for the Add Video preview.

    Unlike :func:`extract_video_info` this raises on failure instead of
    returning empty values, so the modal can show why the URL was rejected.
    The URL is used as given — ``normalize_channel_url`` rewrites creator-page
    paths and must not touch a video URL.

    Returns dict with ``id``, ``title``, ``duration``, ``upload_date``
    (YYYYMMDD str), ``thumbnail``, ``uploader`` and ``webpage_url``.
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # Preview only needs title/duration/thumbnail. Some sites (xHamster in
        # particular) expose no formats until the download request itself, and
        # without this yt-dlp raises "No video formats found" and the video
        # could never be added.
        "ignore_no_formats_error": True,
        # `noplaylist` does not stop a URL that is *only* a playlist, so a
        # creator page pasted here would otherwise extract every video before
        # we could reject it. These bound that to one flat entry, which is all
        # the "this is a playlist" check below needs.
        "extract_flat": "in_playlist",
        "playlistend": 1,
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    opts.update(_build_common_ytdlp_opts(settings))
    opts.update(
        _parse_json_obj(settings.ytdlp_scan_opts_json, name="YTDL_YTDLP_SCAN_OPTS_JSON")
    )

    try:
        logger.info("Extracting single video metadata: %s", url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        logger.warning("Single video extraction failed for %s: %s", url, e)
        raise RuntimeError(str(e)) from e

    if not info:
        raise RuntimeError("No metadata returned for this URL.")
    if info.get("entries"):
        raise RuntimeError(PLAYLIST_URL_ERROR)

    upload_date_raw = info.get("upload_date")
    upload_date_str: str | None = None
    if upload_date_raw is not None:
        parsed = _parse_date(str(upload_date_raw).strip())
        if parsed is not None:
            upload_date_str = parsed.strftime("%Y%m%d")

    duration = info.get("duration")
    video_id = info.get("id")
    return {
        "id": str(video_id) if video_id is not None else None,
        "title": info.get("title") or "",
        "duration": int(duration) if duration is not None else None,
        "upload_date": upload_date_str,
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url") or url,
    }


async def async_compute_oshash(filepath: str) -> str:
    """Async wrapper for compute_oshash. Use this from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(compute_oshash, filepath)


async def async_extract_channel_metadata(
    url: str, settings: Settings
) -> dict:
    """Async wrapper for extract_channel_metadata. Use from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(extract_channel_metadata, url, settings)


async def async_extract_video_info(url: str, settings: Settings) -> dict:
    """Async wrapper for extract_video_info. Use from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(extract_video_info, url, settings)


async def async_extract_single_video_metadata(url: str, settings: Settings) -> dict:
    """Async wrapper for extract_single_video_metadata. Use from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(extract_single_video_metadata, url, settings)


async def async_scan_channel(url: str, settings: Settings) -> dict:
    """Async wrapper for scan_channel. Use this from async code to avoid blocking the event loop.

    Returns dict with ``entries`` (list[dict]) and ``channel_meta`` (dict).
    """
    return await asyncio.to_thread(scan_channel, url, settings)


async def async_download_video(
    url: str,
    output_dir: str,
    output_template: str,
    settings: Settings,
    progress_hook: Callable[[dict], None] | None = None,
) -> dict:
    """Async wrapper for download_video. Use this from async code to avoid blocking the event loop."""
    return await asyncio.to_thread(
        download_video, url, output_dir, output_template, settings, progress_hook
    )
