# Phase 12: YoutubeDL-Material Import

**Status**: COMPLETE

## Overview

Many users are migrating from **YoutubeDL-Material** (YTDLM) to ytdl-stash. YTDLM stores its data in a `local_db.json` file (a lowdb JSON database) that contains subscriptions, downloaded files, playlists, and more. This phase adds the ability to **upload and import** that JSON file so existing subscriptions and video history carry over without starting from scratch.

The canonical file location is a network share:
```
\\192.168.1.4\appdata\youtubedl-material\local_db.json
```

## Prerequisites

- Phase 2 (Database) — Channel + Video models
- Phase 7 (Routes) — existing route structure
- Phase 8 (Web UI) — templates and HTMX patterns

## Goals

1. **Upload `local_db.json`** via the web UI (file upload form on the Settings page).
2. **Parse and map** YTDLM subscriptions → ytdl-stash Channels and YTDLM files → ytdl-stash Videos.
3. **Deduplicate** against existing data — skip channels/videos that already exist (match by URL / `site_video_id`).
4. **Show import results** — summary of what was imported, skipped, and any errors.
5. **Dry-run mode** — preview what would be imported before committing.

---

## YTDLM `local_db.json` Structure

The file is a JSON object with top-level arrays as "tables":

```json
{
  "files": [...],
  "playlists": [...],
  "categories": [...],
  "subscriptions": [...],
  "downloads": {...},
  "users": [...],
  "roles": {...},
  "download_queue": [...],
  "tasks": [...],
  "notifications": [...],
  "archives": [...]
}
```

### `subscriptions` array (maps to `Channel`)

Each subscription object has:

| YTDLM Field | Type | Maps to | Notes |
|-------------|------|---------|-------|
| `id` | string (UUID) | — | Internal YTDLM ID, used to link files |
| `name` | string | `Channel.name` | Channel/uploader name |
| `url` | string | `Channel.url` | Channel page URL |
| `isPlaylist` | boolean | — | `true` = playlist, `false` = channel. Only import channels (or optionally playlists) |
| `type` | string | — | `"video"` or `"audio"` |
| `paused` | boolean | `Channel.enabled` | Inverted: `paused=true` → `enabled=false` |
| `maxQuality` | string | — | e.g. `"best"`, `"1080"` |
| `custom_output` | string | — | Custom filename template |
| `custom_args` | string | — | Extra yt-dlp args |
| `timerange` | string | — | Date filter (e.g. `"20230101"`) |
| `videos` | array | → Videos | Embedded video records (also appear in `files`) |
| `user_uid` | string | — | Multi-user mode owner |
| `downloading` | boolean | — | Runtime state, ignore |

### `files` array (maps to `Video`)

Each file object has:

| YTDLM Field | Type | Maps to | Notes |
|-------------|------|---------|-------|
| `uid` | string (UUID) | — | Internal YTDLM ID |
| `title` | string | `Video.title` | Video title |
| `url` | string | `Video.url` | Video page URL |
| `id` | string | `Video.site_video_id` | Site-specific video ID (e.g. YouTube video ID) |
| `uploader` | string | — | Uploader name (redundant with subscription) |
| `upload_date` | string | `Video.upload_date` | Format: `"YYYYMMDD"` or `"YYYY-MM-DD"` |
| `duration` | number | `Video.duration_seconds` | Duration in seconds |
| `thumbnail` | string | `Video.thumbnail_url` | Thumbnail URL |
| `path` | string | `Video.original_filename` | Local file path in YTDLM |
| `sub_id` | string | `Video.channel_id` | Links to subscription by YTDLM `id` |
| `size` | number | — | File size in bytes |
| `height` | number | — | Video height (resolution) |
| `abr` | number | — | Audio bitrate |
| `description` | string | — | Video description |
| `extractor` | string | → `Channel.site` | Site extractor key (e.g. `"Pornhub"`). Used as fallback for `Channel.site` detection — not stored on Video directly. |
| `isAudio` | boolean | — | Whether it's an audio-only download. Audio files are skipped. |

---

## Deliverables

### Import service module

- [x] Create `app/ytdlm_import.py` module with the core import logic
- [x] `parse_local_db(json_data: dict) -> YTDLMData` — validate and extract subscriptions + files from the raw JSON
- [x] `YTDLMData` Pydantic model to hold parsed subscriptions and files
- [x] `YTDLMSubscription` Pydantic model with fields: `id`, `name`, `url`, `is_playlist` (alias `isPlaylist`), `type`, `paused`
- [x] `YTDLMFile` Pydantic model with fields: `uid`, `title`, `url`, `video_id` (alias `id`), `uploader`, `upload_date`, `duration`, `thumbnail`, `path`, `sub_id`, `extractor`, `is_audio` (alias `isAudio`)
- [x] `map_subscription_to_channel(sub: YTDLMSubscription, files: list[YTDLMFile], settings: Settings) -> dict` — convert a YTDLM subscription to Channel field dict (`name`, `url`, `site`, `enabled`, `check_interval_hours`). Accepts associated files to extract `site` from the `extractor` field as a fallback when URL-based detection fails.
- [x] `map_file_to_video(file: YTDLMFile, channel_id: int) -> dict` — convert a YTDLM file to Video field dict (`channel_id`, `site_video_id`, `title`, `url`, `upload_date`, `duration_seconds`, `thumbnail_url`, `original_filename`, `status`, `metadata_json`)
- [x] `detect_site_from_url(url: str) -> str` — extract site name from URL (fallback to extractor field)
- [x] `normalize_upload_date(date_str: str) -> date | None` — handle both `"YYYYMMDD"` and `"YYYY-MM-DD"` formats

### Import execution

- [x] `run_import(db: AsyncSession, json_data: dict, settings: Settings, dry_run: bool = False) -> ImportResult` — main import orchestrator
- [x] `ImportResult` Pydantic model with fields: `channels_created`, `channels_skipped`, `videos_created`, `videos_skipped`, `errors: list[str]`, `warnings: list[str]`
- [x] Deduplication logic:
  - Channels: skip if a channel with the same `url` already exists
  - Videos: skip if a video with the same `site_video_id` already exists
- [x] Imported videos get `status = "imported"` (a new status indicating they came from YTDLM, not downloaded by ytdl-stash)
- [x] Wrap the entire import in a DB transaction — rollback on unrecoverable error
- [x] Handle subscription `videos` array: merge with top-level `files` array, deduplicate by `uid`. Embedded videos have the same schema as top-level `files` with `sub_id` set.
- [x] Handle orphan files (no `sub_id`): create a synthetic channel named "YTDLM Import (unlinked)" to parent orphan files that have no matching subscription. This avoids skipping data since `Video.channel_id` is NOT NULL.
- [x] Handle files with missing or empty `id` (site_video_id): skip these files and add a warning to `ImportResult`. `Video.site_video_id` is NOT NULL + UNIQUE, so files without an ID cannot be imported.
- [x] Handle audio-only content: skip `isAudio=true` files and `type="audio"` subscriptions by default. Log a warning with the count of skipped audio items.
- [x] Store original YTDLM file JSON in `Video.metadata_json` for traceability and debugging

### New video status: `imported`

- [x] Add `"imported"` to the status lifecycle documentation
- [x] Imported videos are treated as already-downloaded — they won't be re-downloaded
- [x] The UI should display `imported` with a distinct badge color (e.g. blue/purple)

### Route: import endpoint

- [x] `GET /settings/import` — render the import form (or add import section to existing settings page)
- [x] `POST /settings/import` — accept file upload, parse JSON, run import
  - Accept `multipart/form-data` with the JSON file
  - Optional `dry_run` query param (default `false`)
  - Return import results (channels created/skipped, videos created/skipped, errors)
- [x] File size limit: 50 MB (YTDLM `local_db.json` can be large with many files)
- [x] Validate the uploaded file is valid JSON and has expected top-level keys

### Templates: import UI

- [x] Add "Import" section to `app/templates/settings.html` (or create `app/templates/settings/import.html`)
  - File upload input for `local_db.json`
  - "Preview Import" button (dry run) and "Import" button
  - Checkbox: "Include playlists" (default: unchecked, only import channel subscriptions)
  - Checkbox: "Import paused subscriptions as disabled" (default: checked)
- [x] `app/templates/settings/_import_results.html` — HTMX partial showing import results
  - Summary: X channels created, Y skipped, Z videos created, W skipped
  - Expandable error/warning list if any
  - Link to channels page to see imported channels

---

## Patterns to Follow

- `docs/patterns/fastapi.md` — route structure, dependency injection, file uploads
- `docs/patterns/sqlalchemy-async.md` — bulk inserts, transactions
- `docs/patterns/htmx.md` — partial templates, swap patterns
- `docs/recipes/add-api-route.md` — new route endpoints

## Key Implementation Notes

- **File upload via FastAPI**: Use `UploadFile` from `fastapi` to handle the multipart form upload. Read the entire file into memory (capped at 50 MB) and parse as JSON.
- **Subscription→Channel mapping**: Use `url` as the primary key for deduplication. Extract `site` from the URL (e.g. `pornhub.com` → `"pornhub"`). Set `check_interval_hours` to `settings.default_check_interval_hours`. Set `enabled = not sub.paused`.
- **File→Video mapping**: Use `id` (YTDLM's site video ID) as `site_video_id`. Construct `url` from the file's `url` field. Parse `upload_date` from both `"YYYYMMDD"` and `"YYYY-MM-DD"` formats. Set `status = "imported"`. Store original YTDLM file JSON in `metadata_json` for traceability.
- **Embedded videos**: YTDLM subscriptions can have a `videos` array embedded in the subscription object. These are the same records that appear in the top-level `files` array (with `sub_id` set). The import must merge both sources and deduplicate by `uid`.
- **Site detection**: Parse the subscription URL to detect the site. Common patterns:
  - `pornhub.com/model/...` or `pornhub.com/channels/...` → `"pornhub"`
  - `xvideos.com/...` → `"xvideos"`
  - `youtube.com/...` → `"youtube"`
  - Fall back to the `extractor` field from associated files (not on the subscription itself), then to domain name extraction from the URL
- **Pydantic aliases**: `YTDLMSubscription.is_playlist` needs `Field(alias="isPlaylist")` and `YTDLMFile.video_id` needs `Field(alias="id")` since the raw JSON uses camelCase/reserved names. Use `model_config = {"populate_by_name": True}` to allow both alias and field name access.
- **Orphan files**: Files with no `sub_id` (downloaded outside a subscription in YTDLM) are parented under a synthetic "YTDLM Import (unlinked)" channel since `Video.channel_id` is NOT NULL.
- **Cross-channel duplicate videos**: `Video.site_video_id` has a UNIQUE constraint. If the same video appears in multiple subscriptions, only the first import wins. Subsequent duplicates are counted in `videos_skipped`. This is expected — the video is attributed to the first channel encountered.
- **Audio-only content**: Skipped by default. `type="audio"` subscriptions and `isAudio=true` files are not imported since ytdl-stash is video-focused. The import result `warnings` list reports how many were skipped.
- **Error handling**: Individual channel/video import failures should not abort the entire import. Log errors and continue. Return all errors in the `ImportResult`.
- **Dry-run mode**: Parse and map everything but don't commit to DB. Return the same `ImportResult` so the user can preview.
- Use `Depends(get_db)` and `Depends(get_settings)` in routes — never instantiate directly.
- HTMX partials prefixed with `_`, do NOT extend `base.html`.
- Pydantic v2 `model_config` dict, NOT inner `class Config`.

## Acceptance Criteria

- [x] User can upload a `local_db.json` file from the Settings page
- [x] Subscriptions are imported as Channels with correct `name`, `url`, `site`, `enabled` mapping
- [x] Files are imported as Videos with correct `title`, `url`, `site_video_id`, `upload_date`, `duration_seconds` mapping
- [x] Duplicate channels (same URL) are skipped, not duplicated
- [x] Duplicate videos (same `site_video_id`) are skipped, not duplicated
- [x] Imported videos have `status = "imported"` and are not re-downloaded
- [x] Dry-run mode shows a preview without committing changes
- [x] Import results summary is displayed after import completes
- [x] Errors during import are collected and displayed, not silently swallowed
- [x] Playlist subscriptions are optionally importable via checkbox
- [x] Large files (up to 50 MB) are handled without timeout

## Deviations

(none yet)
