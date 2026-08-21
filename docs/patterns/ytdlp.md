# yt-dlp Usage Patterns

Reference patterns for how this project uses yt-dlp as a Python library. Read this before modifying the downloader module.

**Version**: The project tracks yt-dlp **nightly builds** (not stable PyPI). The Dockerfile installs from `yt-dlp/yt-dlp-nightly-builds`; the Settings page checks GitHub nightly releases and the "Update yt-dlp" button installs from the same source.

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
def scan_channel(url: str, cookies_file: str | None = None) -> dict:
    opts = {
        "extract_flat": True,       # Do NOT resolve each video, just list them
        "quiet": True,              # Suppress console output
        "no_warnings": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    flat_entries = info.get("entries", [])
    entries = [
        {
            "id": entry.get("id"),
            "title": entry.get("title"),
            "url": entry.get("url") or entry.get("webpage_url"),
            "upload_date": entry.get("upload_date"),
            "uploader": entry.get("uploader"),
            "duration": entry.get("duration"),
            "thumbnail": entry.get("thumbnail"),
        }
        for entry in flat_entries
        if entry  # Some extractors yield None entries
    ]

    # Channel-level metadata is extracted from the same response ("free").
    channel_name = (
        info.get("channel")
        or info.get("uploader")
        or info.get("title")
        or info.get("playlist_title")
        or ""
    )
    channel_thumbnail = info.get("thumbnail")
    if not channel_thumbnail and isinstance(info.get("thumbnails"), list) and info["thumbnails"]:
        last_thumb = info["thumbnails"][-1]
        channel_thumbnail = last_thumb.get("url") if isinstance(last_thumb, dict) else None

    return {
        "entries": entries,
        "channel_meta": {
            "name": str(channel_name).strip(),
            "thumbnail": channel_thumbnail,
        },
    }
```

**Key points:**
- `extract_flat=True` returns a shallow list of video stubs. Each entry has `id`, `title`, `url` but NOT full metadata.
- `download=False` is redundant with `extract_flat` but explicit for safety.
- Some extractors return `url` while others use `webpage_url`. Check both.
- `upload_date` is a string in `YYYYMMDD` format or `None`.
- This function returns a dict with:
  - `entries`: list of video dicts
  - `channel_meta`: `{name, thumbnail}` from the same yt-dlp call

---

## Single Video Download

Download one video and extract its full metadata:

```python
from collections.abc import Callable

def download_video(
    url: str,
    output_dir: str,
    output_template: str,
    cookies_file: str | None = None,
    progress_hook: Callable[[dict], None] | None = None,
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
    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]

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
- `progress_hooks` can be used to report download progress (percent/speed/ETA) to the UI.

---

## Running yt-dlp in Async Context

yt-dlp is synchronous (blocking I/O). Wrap calls with `asyncio.to_thread()`:

```python
import asyncio

async def async_compute_oshash(filepath: str) -> str:
    return await asyncio.to_thread(compute_oshash, filepath)

async def async_scan_channel(url: str, cookies_file: str | None = None) -> dict:
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
- **"HTTP Error 410: Gone" (PornHub)**: Site rejects yt-dlp's TLS handshake.
  Patched locally — see "Local Patches" below.

---

## Local Patches

When yt-dlp ships a bug we can't wait for upstream to fix, we monkeypatch it at
runtime rather than forking. Patches live in [`app/ytdlp_patches.py`](../../app/ytdlp_patches.py)
and are applied once at import time of `app/downloader.py` (the single chokepoint
for all yt-dlp usage). Each patch is idempotent and failure-tolerant.

**Active: PornHub "HTTP Error 410: Gone"** ([yt-dlp #16729](https://github.com/yt-dlp/yt-dlp/issues/16729))
- PornHub rejects yt-dlp's default TLS handshake with a 410 on both the watch
  page and the media CDN. The request succeeds with a *legacy* OpenSSL context.
- The shim wraps `YoutubeDL.urlopen` to attach the `legacy_ssl=True` request
  extension for any PornHub host or media CDN (`phncdn`/`phprcdn`), and bumps the
  `accessAgeDisclaimerPH` cookie to `2` so HLS formats are exposed. This mirrors
  the (still-unmerged) upstream PR #16776.
- **Remove this patch** once a fix is merged upstream and we pin a nightly that
  includes it. Updating yt-dlp alone does NOT fix this today — neither PR is merged.

> Alternative (no code): some users report the impersonation approach (PR #16794)
> works. Since the app already supports `impersonate`, you can try setting
> `YTDL_YTDLP_IMPERSONATE=chrome` instead of / in addition to the patch.

## Bundled Extractor Plugins

Some sites have a yt-dlp *single-video* extractor but no *channel/playlist*
extractor, so watching a creator page fails with `Unsupported URL`. We ship
yt-dlp plugin extractors for those cases in `yt_dlp_plugins/extractor/`.

Currently bundled (`yt_dlp_plugins/extractor/ytdlstash.py`):

| Extractor | Handles | Why |
|---|---|---|
| `xvideos:channel` | `xvideos.com/<slug>`, `/channels/<slug>`, `/profiles/<slug>`, `/models/<slug>`, `/{amateur,model,pornstar}-channels/<slug>` | upstream has **no** xvideos playlist extractor |
| `xhamster:pornstar` | `xhamster.com/pornstars/<name>` (+ mirror domains) | upstream `XHamsterUserIE` only covers `/users/` and `/creators/` |

Each enumerates videos via the site's own listing (xvideos exposes a JSON
endpoint at `/{kind}/{slug}/videos/new/{page}`; xHamster is server-rendered
with `page-button-link` pagination) and hands individual video URLs back to the
upstream single-video extractor, so downloading/formats stay upstream's job.
Entries carry title/duration/thumbnail so flat scans populate the video list.

### How discovery works
yt-dlp scans every `sys.path` entry for a `yt_dlp_plugins` namespace package.
`app/ytdlp_patches.py:_register_bundled_plugins()` adds the repo root
explicitly so this never depends on the working directory or launcher, and the
`Dockerfile` has a `COPY yt_dlp_plugins/ yt_dlp_plugins/` line. There are
deliberately **no `__init__.py` files** — they are namespace packages.

### Rules when adding an extractor here
- **Plugins are *prepended* to yt-dlp's extractor lookup**, so an over-greedy
  `_VALID_URL` will hijack URLs from upstream extractors. Bare-slug patterns
  (like xvideos) need negative lookaheads for video URLs, reserved site
  sections, and other extractors' URL shapes (e.g. `#quickies`). Always test
  routing both ways after a change.
- Supply a channel avatar as `thumbnails=[{'id': 'avatar', ...}]`. Without it
  `extract_channel_metadata()` falls back to a slow non-flat retry that fully
  extracts a video just to find an image (and can fail on videos with no
  formats).
- Guard imports of upstream internals (e.g. `XHamsterIE._DOMAINS`) with
  try/except — an upstream rename would otherwise raise at module import and
  silently disable *every* extractor in the file.
- Verify with the real app path, not just yt-dlp:
  `python -c "from app.downloader import scan_channel; ..."`.
