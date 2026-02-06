# yt-dlp Usage Patterns

Reference patterns for how this project uses yt-dlp as a Python library. Read this before modifying the downloader module.

---

## Core Import

```python
import yt_dlp
```

yt-dlp is used as a library, NOT as a CLI subprocess. See [ADR-003](../adr/003-ytdlp-as-library.md).

---

## Channel Scanning (Flat Extraction)

List all videos on a channel without downloading anything:

```python
def scan_channel(url: str, cookies_file: str | None = None) -> list[dict]:
    opts = {
        "extract_flat": True,       # Do NOT resolve each video, just list them
        "quiet": True,              # Suppress console output
        "no_warnings": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries", [])
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
        for entry in entries
        if entry  # Some extractors yield None entries
    ]
```

**Key points:**
- `extract_flat=True` returns a shallow list of video stubs. Each entry has `id`, `title`, `url` but NOT full metadata.
- `download=False` is redundant with `extract_flat` but explicit for safety.
- Some extractors return `url` while others use `webpage_url`. Check both.
- `upload_date` is a string in `YYYYMMDD` format or `None`.

---

## Single Video Download

Download one video and extract its full metadata:

```python
def download_video(
    url: str,
    output_dir: str,
    output_template: str,
    cookies_file: str | None = None,
) -> dict:
    outtmpl = f"{output_dir}/{output_template}"
    opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

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
```

**Key points:**
- `ydl.prepare_filename(info)` returns the actual path the file was saved to, with template variables resolved.
- `info` is the full `info_dict` -- a huge dictionary with every piece of metadata yt-dlp could extract.
- `retries` and `fragment_retries` handle transient network errors.

---

## Running yt-dlp in Async Context

yt-dlp is synchronous (blocking I/O). Wrap calls with `asyncio.to_thread()`:

```python
import asyncio

async def async_compute_oshash(filepath: str) -> str:
    return await asyncio.to_thread(compute_oshash, filepath)

async def async_scan_channel(url: str, cookies_file: str | None = None) -> list[dict]:
    return await asyncio.to_thread(scan_channel, url, cookies_file)

async def async_download_video(url: str, output_dir: str, ...) -> dict:
    return await asyncio.to_thread(download_video, url, output_dir, ...)
```

**Rule**: Never call `scan_channel()`, `download_video()`, or `compute_oshash()` directly from an async function. Always use the `async_*` wrappers (or `asyncio.to_thread()`) to avoid blocking the event loop.

---

## Extracting Performers

Different extractors provide performer data in different fields. Check multiple:

```python
def _extract_performers(info: dict) -> list[str]:
    """Extract performer names from yt-dlp info_dict."""
    # Some extractors put cast/actors in these fields
    performers = []

    # Check 'cast' (some adult site extractors)
    if info.get("cast"):
        performers.extend(info["cast"])

    # Check 'actors' (alternative field)
    if info.get("actors"):
        performers.extend(info["actors"])

    # Fall back to 'uploader' if no cast data
    if not performers and info.get("uploader"):
        performers.append(info["uploader"])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in performers:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            unique.append(p)

    return unique
```

---

## Parsing Upload Date

yt-dlp returns dates as `YYYYMMDD` strings:

```python
from datetime import date

def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    except (ValueError, IndexError):
        return None
```

---

## Output Template Variables

The default template is `%(uploader)s - %(title)s [%(id)s].%(ext)s`.

Common yt-dlp template variables:
| Variable | Description | Example |
|----------|-------------|---------|
| `%(id)s` | Video ID | `ph5f3a1b2c3d4e` |
| `%(title)s` | Video title | `My Video Title` |
| `%(uploader)s` | Channel/uploader name | `SomeUser` |
| `%(upload_date)s` | Upload date (YYYYMMDD) | `20250115` |
| `%(ext)s` | File extension | `mp4` |
| `%(duration)s` | Duration in seconds | `300` |
| `%(resolution)s` | Video resolution | `1920x1080` |

---

## Cookies

Some sites require authentication via cookies. yt-dlp accepts a Netscape-format cookies.txt file:

```python
opts["cookiefile"] = "/app/cookies.txt"
```

Users mount their cookies file into the container:
```yaml
volumes:
  - ./cookies.txt:/app/cookies.txt:ro
```

Set `YTDL_COOKIES_FILE=/app/cookies.txt` to enable.

---

## Error Handling

yt-dlp raises `yt_dlp.utils.DownloadError` on failures:

```python
from yt_dlp.utils import DownloadError

try:
    info = ydl.extract_info(url, download=True)
except DownloadError as e:
    # e.g., "Video unavailable", "HTTP 403", "Geo-restricted"
    raise RuntimeError(f"Download failed: {e}")
```

Common errors and their meanings:
- **"Video unavailable"**: Deleted or geo-blocked.
- **"HTTP Error 403: Forbidden"**: Rate-limited or needs cookies.
- **"Unable to extract"**: Extractor broken; update yt-dlp.
- **"Incomplete data"**: Transient network issue; retry.
