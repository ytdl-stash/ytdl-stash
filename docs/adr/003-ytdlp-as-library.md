# ADR-003: Use yt-dlp as a Python Library, Not CLI

**Status**: Accepted

## Context

yt-dlp is the core tool for scanning channels and downloading videos. It can be used two ways:
1. As a CLI tool via `subprocess.run(["yt-dlp", ...])`.
2. As a Python library via `import yt_dlp; yt_dlp.YoutubeDL(opts).extract_info(url)`.

## Decision

Use yt-dlp as a **Python library import**. Import `yt_dlp.YoutubeDL` and call methods directly.

## Alternatives Considered

### CLI via subprocess
- More familiar to shell-script users.
- Output parsing is fragile (JSON stdout, error messages on stderr).
- Harder to extract structured metadata.
- Process management adds complexity.
- Rejected because structured Python dicts are far easier to work with.

## Consequences

**Positive:**
- Direct access to `info_dict` -- the full metadata dictionary yt-dlp builds for each video. Contains title, uploader, upload date, duration, thumbnails, tags, cast, categories, and more.
- `extract_flat=True` mode allows listing channel videos without downloading (fast scans).
- Progress hooks can be registered to track download percentage.
- Error handling via Python exceptions instead of parsing stderr.
- No subprocess overhead or shell escaping concerns.

**Negative:**
- yt-dlp is a large dependency (~50MB installed).
- yt-dlp's Python API is less documented than the CLI; must read source code for advanced options.
- yt-dlp runs synchronously (blocking I/O). Downloads must be run in a thread via `asyncio.to_thread()` to avoid blocking the event loop.

## Key Usage Patterns

### Channel scan (flat extraction, no download):
```python
opts = {"extract_flat": True, "quiet": True}
if cookies_file:
    opts["cookiefile"] = cookies_file
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info(channel_url, download=False)
    entries = info.get("entries", [])
```

### Single video download:
```python
opts = {
    "outtmpl": f"{output_dir}/{output_template}",
    "quiet": True,
    "no_warnings": True,
}
if cookies_file:
    opts["cookiefile"] = cookies_file
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info(video_url, download=True)
    filepath = ydl.prepare_filename(info)
```

### Running in async context:
```python
import asyncio

result = await asyncio.to_thread(scan_channel, url, cookies_file)
```

## References

- [yt-dlp Python embedding docs](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp)
- [yt-dlp YoutubeDL options](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py) (read the `__init__` params docstring)
