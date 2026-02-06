# Phase 3: yt-dlp Downloader Module

**Status**: COMPLETE

## Prerequisites

- Phase 1 complete (config.py for settings)
- Phase 2 complete (models.py — pipeline needs Video model, but this module is standalone)

## Deliverables

- [x] `app/downloader.py` — three functions + two helpers + async wrappers

### Functions to implement

**`scan_channel(url, cookies_file) -> list[dict]`**
- Uses `yt_dlp.YoutubeDL` with `extract_flat=True`
- Returns list of dicts: `{id, title, url, upload_date, uploader, duration, thumbnail}`
- Does NOT download anything

**`download_video(url, output_dir, output_template, cookies_file) -> dict`**
- Downloads a single video via yt-dlp
- Returns dict: `{filepath, filename, title, upload_date, performers, studio, duration, thumbnail_url, metadata_json}`
- Prefers `requested_downloads[0]["filepath"]` for the actual saved path (accounts for post-processing/muxing extension changes), falls back to `ydl.prepare_filename(info)`

**`compute_oshash(filepath) -> str`**
- OpenSubtitles hash: read first 64KB + last 64KB + file size
- Returns 16-char hex string
- See `docs/glossary.md` for oshash definition

**Helper: `_extract_performers(info) -> list[str]`**
- Check `info["cast"]`, `info["actors"]`, fallback to `info["uploader"]`
- Deduplicate, preserve order

**Helper: `_parse_date(date_str) -> date | None`**
- Parse yt-dlp `YYYYMMDD` string to Python `date`

### Async wrappers

All three public functions are **synchronous** (yt-dlp is blocking). Each has a standalone async wrapper via `asyncio.to_thread()`:
```
async_scan_channel()      -> asyncio.to_thread(scan_channel, ...)
async_download_video()    -> asyncio.to_thread(download_video, ...)
async_compute_oshash()    -> asyncio.to_thread(compute_oshash, ...)
```

## Patterns to Follow

- `docs/patterns/ytdlp.md` — **READ THIS FIRST**. Complete reference implementations for all functions, error handling, async wrapping, cookies, output template variables.
- `docs/adr/003-ytdlp-as-library.md` — why we import yt-dlp, not subprocess.
- `docs/adr/008-sequential-downloads.md` — one download at a time.
- `docs/adr/004-oshash-scene-matching.md` — why oshash, how it works.

## Acceptance Criteria

- [x] `scan_channel()` returns a list of video dicts from a channel URL
- [x] `download_video()` downloads a file and returns metadata dict
- [x] `compute_oshash()` returns a 16-char hex string matching Stash's algorithm
- [x] `_extract_performers()` handles cast, actors, and uploader fallback
- [x] `_parse_date()` handles valid dates, None, and malformed strings
- [x] Async wrappers use `asyncio.to_thread()` (never block the event loop)
- [x] `DownloadError` is caught and re-raised as `RuntimeError` with context
- [x] No direct yt-dlp calls outside this module (single responsibility)

## Deviations

- `async_compute_oshash()` added as a standalone wrapper (original plan said to call `to_thread` inline). Keeps the API surface consistent — all three public functions get matching `async_` wrappers.
- `download_video()` prefers `requested_downloads[0]["filepath"]` over `prepare_filename()` to handle post-processing extension changes (e.g. muxed `.mkv` → `.mp4`).
