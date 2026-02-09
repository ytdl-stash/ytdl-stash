# Fix Channel Performer/Studio Creation Bugs - February 9, 2026

## Overview

Fixed three interrelated bugs in the channel add workflow that prevented proper performer creation, performer scraping, and studio photo uploads. Added diagnostic INFO-level logging at all key decision points in the performer/studio sync pipeline.

## Root Causes

### 1. Channel name not found during initial scrape

`extract_channel_metadata()` used flat extraction (`extract_flat=True`) for speed, but many yt-dlp extractors only populate `channel`/`uploader` fields in non-flat mode. The non-flat fallback was only triggered when the **thumbnail** was missing — not when the **name** was missing. If flat mode returned a thumbnail but no usable name, the fallback never ran and the name defaulted to the site domain (e.g. `"pornhub.com"`).

### 2. Performer scrape not running on newly created performer

This cascaded directly from Bug 1. When the channel name was a domain placeholder, `is_placeholder_name()` returned `True`, causing `sync_channel_performer()` to skip performer creation entirely. With no `stash_performer_id`, the scrape step was also skipped.

### 3. Studio photo not uploading properly

Two sub-issues:
- **Same cascade as Bug 2** — studio creation was also skipped due to placeholder name.
- **Image download missing cookies/auth** — `_url_to_data_uri()` used a bare `httpx.AsyncClient` without cookies or custom headers. Many sites require authentication to serve thumbnail images, but yt-dlp extracted the URL using cookies/browser impersonation that `_url_to_data_uri()` didn't have.

## Implementation Approach

### Fix 1: Non-flat fallback for missing name

Changed the fallback condition in `extract_channel_metadata()` from `if not thumbnail:` to `if not thumbnail or not name:`. This ensures the slower non-flat extraction runs whenever either piece of metadata is missing.

### Fix 2: Image downloads with cookies and headers

- Added `cookies_file` and `headers` parameters to `_url_to_data_uri()`.
- Added `_load_cookies_from_file()` helper using Python's `http.cookiejar.MozillaCookieJar` to parse Netscape cookies.txt files for httpx.
- Added `cookies_file` and `image_request_headers` to `StashClient.__init__()`.
- Added `StashClient.from_settings()` classmethod that populates these from app settings (`cookies_file`, `ytdlp_user_agent`, `ytdlp_referer`, `ytdlp_http_headers_json`).
- Updated all `StashClient(settings.stash_url, settings.stash_api_key)` call sites to use `StashClient.from_settings(settings)`.
- Updated all internal `_url_to_data_uri()` calls to use `self.download_image_data_uri()` which passes through cookies/headers.

### Fix 3: Diagnostic logging

Added INFO-level logging at all key decision points:
- `_extract_channel_name()`: logs each candidate tried, whether accepted or rejected (and why).
- `extract_channel_metadata()`: logs when flat extraction is incomplete and non-flat is retried; logs final result.
- `add_channel()`: logs form values, want_performer/want_studio booleans, and post-sync IDs.
- `_scrape_and_resync_performer()`: logs entry, scrape result, and application.
- `sync_channel_performer()` / `sync_channel_studio()`: logs START/DONE with all relevant state at each step boundary.
- `_url_to_data_uri()`: logs download attempt with cookies status, and response status/size/content-type.
- `create_performer_with_metadata()` / `create_studio_with_metadata()`: logs fields being sent.

## Changes Made

### Files Modified

- **`app/downloader.py`** — Fixed non-flat fallback to trigger on missing name (not just missing thumbnail). Added INFO logging to `_extract_channel_name()` and `extract_channel_metadata()`.
- **`app/stash_client.py`** — Added `json`, `http.cookiejar` imports. Added `_load_cookies_from_file()`. Enhanced `_url_to_data_uri()` with cookies/headers params and logging. Added `cookies_file`/`image_request_headers` to `StashClient.__init__()`. Added `StashClient.from_settings()` classmethod. Added `download_image_data_uri()` public convenience method. Updated all internal `_url_to_data_uri` calls. Added creation logging.
- **`app/performer_sync.py`** — Removed direct `_url_to_data_uri` import; uses `stash.download_image_data_uri()` instead. Added detailed step-by-step INFO logging to `sync_channel_performer()`.
- **`app/studio_sync.py`** — Same changes as performer_sync. Added detailed logging to `sync_channel_studio()`.
- **`app/routes/channels.py`** — Updated all `StashClient()` calls to `StashClient.from_settings()`. Added logging to `add_channel()` and `_scrape_and_resync_performer()`.
- **`app/routes/videos.py`** — Updated `StashClient()` calls to `StashClient.from_settings()`.
- **`app/scheduler.py`** — Updated `StashClient()` calls to `StashClient.from_settings()`.
- **`app/routes/settings.py`** — Updated `StashClient()` call to `StashClient.from_settings()`.
- **`app/routes/health.py`** — Updated `StashClient()` call to `StashClient.from_settings()`.

## Observations

- The three bugs were actually one root cause (missing non-flat fallback for name) that cascaded through the entire performer/studio creation pipeline. The `is_placeholder_name()` guard was working correctly — it was just receiving the wrong input.
- The image download issue was a separate but compounding problem: even when creation succeeded, cookie-protected thumbnail URLs would fail silently.
- The `from_settings()` factory pattern is cleaner than threading individual settings through every call site.
