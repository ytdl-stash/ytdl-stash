# ytdl-stash Architecture

## Overview

ytdl-stash is a containerized Python application that **monitors video channels**, **automatically downloads new videos** via yt-dlp, and **syncs them into Stash** (a self-hosted media organizer) with full metadata using its GraphQL API and oshash-based scene matching.

## System Context

```
+-------------------+       +-------------------+       +-------------------+
|   Video Sites     |       |    ytdl-stash     |       |      Stash        |
|  (PH, XV, etc.)   |<----->|   (this app)      |<----->|  (GraphQL API)    |
|                   |       |   port 8282       |       |  port 9999        |
+-------------------+       +-------------------+       +-------------------+
        ^                          |    |                        |
        |                          |    |                        |
   yt-dlp scans &            SQLite DB  Shared /downloads volume |
   downloads via               (data/)                           |
   extractors                                           Stash watches
                                                        /downloads too
```

## Component Diagram

| Component | File(s) | Responsibility |
|-----------|---------|----------------|
| **Version** | `app/__init__.py` | `get_version()` reads `VERSION` file baked into Docker image (falls back to `"dev"`) |
| **Config** | `app/config.py` | Pydantic BaseSettings, reads `YTDL_*` env vars |
| **App Entry** | `app/main.py` | FastAPI factory, lifespan, static/template mounts |
| **Database** | `app/database.py`, `app/models.py` | SQLAlchemy async engine, Channel + Video models |
| **Downloader** | `app/downloader.py` | yt-dlp wrapper: scan channels (with nested-entry flattening), download videos, compute oshash |
| **Stash Client** | `app/stash_client.py` | Async httpx GraphQL client for Stash API (find/create scenes, performers, studios, tags; scraping; generate). Use `StashClient.from_settings(settings)` to create instances — this propagates cookies and HTTP headers for image downloads. |
| **Pipeline** | `app/pipeline.py` | Orchestration: download -> oshash -> scan -> match -> tag -> scrape -> re-sync |
| **Scheduler** | `app/scheduler.py` | APScheduler periodic channel checks + download processing; job registry with status tracking and manual trigger support |
| **Performer Sync** | `app/performer_sync.py` | Bidirectional sync: pulls full Stash performer data locally, pushes source metadata (image, URL) to Stash when missing |
| **Studio Sync** | `app/studio_sync.py` | Links channels to Stash studios by URL: find by channel URL in studio urls, or create; gap-fill URL, image, details |
| **YTDLM Import** | `app/ytdlm_import.py` | Import channels and videos from YoutubeDL-Material `local_db.json` |
| **Logging** | `app/logging_config.py` | Centralized logging: console + rotating file + in-memory ring buffer for web UI |
| **Auth** | `app/auth.py`, `app/routes/auth.py` | Optional app password: PBKDF2 hash in `{data_dir}/auth.json`, session cookie; CLI `python -m app.auth set \| remove` |
| **Routes** | `app/routes/*.py` | FastAPI routers: dashboard, channels (list/detail/add/update/delete/sync), videos, jobs, logs, settings, auth (login/logout) |
| **Templates** | `app/templates/*.html` | Jinja2 + HTMX server-rendered UI |
| **Static** | `app/static/` | Custom CSS (HTMX indicators, a few app-specific rules) |

## Tech Stack Summary

| Layer | Technology | Why |
|-------|-----------|-----|
| Runtime | Python 3.12 | yt-dlp is a Python library; single language for everything |
| Web framework | FastAPI + Uvicorn | Async-native, automatic OpenAPI docs, dependency injection |
| Frontend | Jinja2 + HTMX + DaisyUI + Tailwind (CDN) | No build step, server-rendered, progressive enhancement; DaisyUI components (including tooltips for help text; see [docs/patterns/ui.md](../patterns/ui.md)) and Tailwind utilities for layout and styling. Tables use a responsive pattern (card-style rows on narrow viewports via `data-label` and `.table-responsive`); list/detail tables are wrapped in `overflow-x-auto` for fallback. |
| Database | SQLite + SQLAlchemy async + aiosqlite | Zero-config, single-file DB, async support via aiosqlite |
| Downloader | yt-dlp (Python import) | Industry standard, supports hundreds of sites |
| Scheduler | APScheduler 3.x | Lightweight, async-compatible, no external broker needed |
| HTTP client | httpx | Async HTTP client for Stash GraphQL calls |
| Container | Docker + docker-compose | Reproducible deployment, shared volumes with Stash |

## Build Roadmap

The project is built in 12 phases. Per-phase briefs live in `docs/roadmap/`:

| Phase | Status | Summary |
|-------|--------|---------|
| [Phase 1: Scaffold](../roadmap/phase-01-scaffold.md) | **COMPLETE** | Project skeleton, config, Docker |
| [Phase 2: Database](../roadmap/phase-02-database.md) | **COMPLETE** | SQLAlchemy models |
| [Phase 3: Downloader](../roadmap/phase-03-downloader.md) | **COMPLETE** | yt-dlp wrapper |
| [Phase 4: Stash Client](../roadmap/phase-04-stash-client.md) | **COMPLETE** | GraphQL client |
| [Phase 5: Pipeline](../roadmap/phase-05-pipeline.md) | **COMPLETE** | Orchestration |
| [Phase 6: Scheduler](../roadmap/phase-06-scheduler.md) | **COMPLETE** | Periodic jobs |
| [Phase 7: Routes](../roadmap/phase-07-routes.md) | **COMPLETE** | API endpoints |
| [Phase 8: UI](../roadmap/phase-08-ui.md) | **COMPLETE** | Templates |
| [Phase 9: Docker](../roadmap/phase-09-docker.md) | **COMPLETE** | Finalization |
| [Phase 10: Polish](../roadmap/phase-10-polish.md) | **COMPLETE** | Hardening |
| [Phase 11: Performer Sync](../roadmap/phase-11-performer-sync.md) | **COMPLETE** | Auto-link performers, Performer Browser |
| [Phase 12: YTDLM Import](../roadmap/phase-12-ytdlm-import.md) | **COMPLETE** | Import from YoutubeDL-Material `local_db.json` |

See `docs/roadmap/README.md` for the full index and dependency graph.

## Directory Structure (Target)

Files marked with a phase annotation are planned but not yet created.

```
ytdl-stash/
  .cursor/
    rules/
      documentation.mdc        # Rule: always read/write docs
  .dockerignore
  Dockerfile
  docker-compose.yml
  requirements.txt
  docs/
    architecture/
      README.md                 # This file
    adr/
      001-python-fastapi.md
      002-sqlite-async.md
      003-ytdlp-as-library.md
      004-oshash-scene-matching.md
      005-jinja2-htmx.md
      006-pydantic-settings.md
      007-apscheduler.md
      008-sequential-downloads.md
      009-docker-first.md
    patterns/
      fastapi.md
      sqlalchemy-async.md
      ytdlp.md
      stash-graphql.md
      htmx.md
    recipes/
      add-config-setting.md
      add-database-field.md
      add-api-route.md
      add-stash-query.md
      local-dev-without-docker.md
      troubleshooting.md
    data-flow.md
    glossary.md
    roadmap/
      README.md                 # Phase index and dependency graph
      phase-01-scaffold.md      # COMPLETE
      phase-02-database.md      # COMPLETE
      phase-03-downloader.md
      phase-04-stash-client.md
      phase-05-pipeline.md
      phase-06-scheduler.md
      phase-07-routes.md
      phase-08-ui.md
      phase-09-docker.md
      phase-10-polish.md
    journal/
      README.md                 # Development journal conventions + template
      2026-02-07-add-development-journal.md
  app/
    __init__.py
    main.py                     # FastAPI app factory + lifespan
    auth.py                     # Optional app password (hash, session, CLI)
    config.py                   # Pydantic BaseSettings
    database.py                 # Async engine, session, init_db (Phase 2)
    models.py                   # Channel, Video models (Phase 2)
    downloader.py               # yt-dlp wrapper (Phase 3)
    stash_client.py             # Stash GraphQL client (Phase 4)
    pipeline.py                 # Download-to-Stash orchestration (Phase 5)
    performer_sync.py           # Auto-link channel performers to Stash (Phase 11)
    studio_sync.py              # Auto-link channel studios to Stash (by URL)
    scheduler.py                # APScheduler setup (Phase 6)
    routes/
      __init__.py
      auth.py                   # GET/POST /login, GET /logout (when password set)
      dashboard.py              # GET /
      channels.py               # Channels: list (card grid), detail, add wizard, update, delete, sync, check-now (Phase 11)
      videos.py                 # Videos list (paginated)/detail/retry/redownload/resync/active_downloads panel
      health.py                 # GET /health (Phase 10)
      settings.py               # Settings + Stash connectivity test
    templates/
      base.html
      login.html                # Standalone login page (no nav)
      dashboard.html
      error.html                # User-friendly error page (Phase 10)
      channels/
        list.html               # Channels page: Add Channel, Bulk Edit, Check All Now, card grid
        _list_content.html      # HTMX partial: filter/sort nav + channel card grid
        _card.html              # HTMX partial: single channel card
        _card_list.html         # HTMX partial: loop of _card.html
        detail.html             # Channel detail page
        _detail_card.html       # HTMX partial: channel detail card (Stash sync + Channel Settings + videos)
        _channel_videos.html    # HTMX partial: channel video table (polls every 10s)
        _add_modal.html         # Add channel dialog shell + step 1
        _add_step1.html         # Step 1: URL input
        _add_step2.html         # Step 2: metadata review + settings
        _add_step3.html         # Step 3: Stash linking + save
        _bulk_edit.html         # HTMX partial: bulk edit form (name, interval, max age, min duration, enabled)
      videos/
        list.html
        detail.html
        _video_list.html       # HTMX partial: table + pagination
        _table_body.html       # HTMX partial: video table rows
        _table_body_performer.html  # Partial: video rows for channel detail (thumb, no channel column)
        _status_badge.html     # HTMX partial: status badge (tooltip shows error_message when present)
        _active_downloads.html # HTMX partial: active downloads panel (self-polls every 3s; optional poll_url for channel-scoped)
      settings.html
    static/
      style.css
  data/                         # SQLite DB + auth.json (volume-mounted, gitignored)
  downloads/                    # Video files (volume-mounted, gitignored)
```

## Application Version

The app version is derived from GitHub release tags (e.g. `v0.12.0`). During the Docker image build, the release workflow passes the git tag as the `APP_VERSION` build arg, which is written to a `VERSION` file inside the container. At runtime, `app.get_version()` reads this file and returns the version string. When running outside Docker (local dev), the function falls back to `"dev"`.

The version is displayed on the Settings page (`/settings`) as a badge next to the page title.

| Layer | Mechanism |
|-------|-----------|
| CI/CD | `.github/workflows/release.yml` passes `APP_VERSION=${{ github.ref_name }}` build arg |
| Docker | `Dockerfile` writes `$APP_VERSION` to `/app/VERSION` |
| Python | `app/__init__.py` exports `get_version()` which reads the `VERSION` file |
| UI | `settings.html` displays the version via the `app_version` template variable |

## Key Design Principles

1. **Single container**: The entire app runs in one Docker container. No message queues, no Redis, no external DB.
2. **Shared volume**: Downloads land in a directory that Stash also watches, enabling oshash-based scene matching.
3. **Configurable download concurrency**: Defaults to one video at a time with a configurable delay to avoid rate limiting; advanced users can opt into parallel downloads.
4. **Idempotent operations**: Videos are tracked by `site_video_id`; re-scanning a channel skips already-known videos.
5. **Graceful failure**: Each video has an independent status lifecycle. One failure does not block others.
6. **Server-rendered UI**: No JavaScript build step. Jinja2 templates enhanced with HTMX for interactivity.

## Configuration

All config is via environment variables prefixed with `YTDL_`:

The Settings page (`/settings`) displays the effective configuration **read at startup** (read-only). To change values like `YTDL_MAX_CONCURRENT_DOWNLOADS`, update your environment and restart the app.

| Variable | Default | Description |
|----------|---------|-------------|
| `YTDL_STASH_URL` | `http://localhost:9999` | Stash server URL |
| `YTDL_STASH_API_KEY` | `""` | Stash API key (if auth enabled) |
| `YTDL_DOWNLOAD_DIR` | `/downloads` | Where videos are saved |
| `YTDL_STASH_DOWNLOAD_DIR` | `None` | Path to downloads as Stash sees it (for path mapping) |
| `YTDL_DATA_DIR` | `/app/data` | Where SQLite DB lives |
| `YTDL_DEFAULT_CHECK_INTERVAL_HOURS` | `6` | Default hours between channel checks |
| `YTDL_MAX_CONCURRENT_DOWNLOADS` | `1` | Maximum number of videos to download/import in parallel |
| `YTDL_DOWNLOAD_DELAY_SECONDS` | `5` | Seconds to wait between downloads |
| `YTDL_COOKIES_FILE` | `None` | Optional path to cookies.txt |
| `YTDL_YTDLP_OUTPUT_TEMPLATE` | `%(uploader)s - %(title)s [%(id)s].%(ext)s` | yt-dlp filename template |
| `YTDL_YTDLP_FORMAT` | `None` | Optional yt-dlp format selector (e.g. `bestvideo+bestaudio/best`) |
| `YTDL_YTDLP_IMPERSONATE` | `None` | Optional yt-dlp impersonation target (varies by yt-dlp version) |
| `YTDL_YTDLP_USER_AGENT` | `None` | Optional override for `User-Agent` header |
| `YTDL_YTDLP_REFERER` | `None` | Optional override for `Referer` header |
| `YTDL_YTDLP_PROXY` | `None` | Optional proxy URL (e.g. `socks5://127.0.0.1:9050`) |
| `YTDL_YTDLP_SOCKET_TIMEOUT_SECONDS` | `None` | Optional socket/request timeout in seconds |
| `YTDL_YTDLP_RETRIES` | `3` | yt-dlp retry count for downloads |
| `YTDL_YTDLP_FRAGMENT_RETRIES` | `3` | yt-dlp fragment retry count (HLS/DASH) |
| `YTDL_YTDLP_HTTP_HEADERS_JSON` | `{}` | JSON object merged into yt-dlp `http_headers` |
| `YTDL_YTDLP_SCAN_OPTS_JSON` | `{}` | JSON object merged into yt-dlp options for channel scans / metadata extraction |
| `YTDL_YTDLP_DOWNLOAD_OPTS_JSON` | `{}` | JSON object merged into yt-dlp options for downloads |
| `YTDL_YTDLP_UPDATE_CHECK_INTERVAL_HOURS` | `24` | How often the scheduler checks GitHub nightly builds for a newer yt-dlp version |
| `YTDL_STASH_SCRAPE_AFTER_SYNC` | `True` | Run Stash URL scraper on the scene after sync (best-effort) |
| `YTDL_STASH_GENERATE_AFTER_SYNC` | `True` | Trigger Stash metadata generation (covers, previews, etc.) after sync |
| `YTDL_STASH_GENERATE_COVERS` | `True` | Generate cover images (only when generate is enabled) |
| `YTDL_STASH_GENERATE_PREVIEWS` | `True` | Generate video previews (only when generate is enabled) |
| `YTDL_STASH_GENERATE_SPRITES` | `True` | Generate sprite sheets (only when generate is enabled) |
| `YTDL_STASH_GENERATE_PHASHES` | `True` | Generate perceptual hashes (only when generate is enabled) |
| `YTDL_STASH_ORGANIZED_SETTLE_SECONDS` | `5` | **Deprecated.** No longer used; generate now runs before organized. Retained for backwards compat. |
| `YTDL_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Optional app password

Password protection is **off by default**. When enabled, all web UI routes (except `/health`, `/login`, `/logout`, and `/static`) require a session cookie set by entering the password on the login page.

- **Storage**: Password hash is stored in `{YTDL_DATA_DIR}/auth.json` (PBKDF2-SHA256). No database changes.
- **Session**: Cookie `ytdl_session` (HttpOnly, SameSite=Lax, 7-day expiry). Changing or removing the password invalidates all sessions.
- **CLI** (run inside the container, e.g. `docker compose exec ytdl-stash ...`):
  - `python -m app.auth set` — prompt for a new password (enables protection; overwrites existing password).
  - `python -m app.auth remove` — delete `auth.json` (disables protection).

Docker health checks (`GET /health`) are not protected so the container remains healthy.

## Logging

Logging is configured centrally in `app/logging_config.py` via the `setup_logging()` function, called once during FastAPI lifespan startup. Three handlers are attached to the root logger:

| Handler | Destination | Purpose |
|---------|------------|---------|
| **Console** | stdout | Standard container output, visible via `docker logs` |
| **RotatingFile** | `{data_dir}/ytdl-stash.log` | Persistent log file, 5 MB max with 3 backups |
| **MemoryRingBuffer** | In-memory deque (2000 entries) | Powers the `/logs` web UI viewer |

The web UI log viewer (`/logs`) supports:
- Filtering by minimum level (DEBUG / INFO / WARNING / ERROR)
- Free-text search across all log fields
- Configurable entry limit (50 / 200 / 500 / 1000)
- Auto-refresh every 5 seconds (toggleable)
- Clear in-memory buffer
- Download the persistent log file

Each module creates its own logger with `logging.getLogger(__name__)`. Noisy third-party loggers (httpx, httpcore, apscheduler, uvicorn.access) are quieted to WARNING level.

## Performer deduplication

To avoid creating duplicate performers in Stash when multiple code paths (channel sync, video pipeline, post-sync scrape) or concurrent downloads reference the same person, the app uses:

- **Per-name asyncio locks** (module-level in `stash_client.py`, shared by all `StashClient` instances): `find_or_create_performer`, `find_or_create_performer_by_url`, `find_or_create_studio`, and `find_or_create_tag` each use a lock keyed by normalized name so that concurrent workers (e.g. when `max_concurrent_downloads` > 1) serialize find-then-create for the same entity and only one create runs.
- **Name normalization**: Performer names are normalized (whitespace collapsed, trim) before lookup and create so that "Jane Doe" and "Jane  Doe" resolve to the same performer.
- **Alias fallback**: Before creating a performer, the client checks Stash for an existing performer whose `alias_list` contains the given name (`find_performer_by_alias`); if found, that performer ID is reused.
- **Channel cross-reference in pipeline**: The pipeline queries all channels and builds a case-insensitive name to channel lookup. If a performer name matches a known channel, it uses `find_or_create_performer_by_url(name, channel.url, channel.performer_image_url)` so the performer gets the channel URL and image in Stash. This applies to both primary performers (the video channel owner) and secondary performers (guests/co-stars who are also monitored channels).
- **URL/image gap-fill**: When `find_or_create_performer_by_url` finds an existing performer by name or alias (but not by URL), it gap-fills the performer in Stash: if the channel URL is not already in the performer URL list, it appends it; if the performer has no image and the channel provides `performer_image_url`, it pushes the image. This back-fills metadata for performers that were previously created with name only (e.g. by an earlier pipeline run or post-sync scraper).

## Datetime handling (TZDateTime)

All datetime columns use the custom `TZDateTime` type in `app/models.py`. SQLite does not store timezone info; `TZDateTime` ensures values are written in a consistent form and re-attached to UTC when read, so Python-side comparisons (e.g. in the channel checker) never raise "naive vs aware" errors. See ADR-010.

## Schema (Channel)

The `Channel` model includes: `id`, `name`, `url`, `site`, `enabled`, `check_interval_hours`, `last_checked_at`, `created_at`, `updated_at`, `stash_performer_id` (nullable, linked Stash performer ID), `performer_image_url` (nullable, cached avatar/thumbnail URL from the tube site), `stash_performer_data` (nullable JSON, full Stash performer record pulled during sync — gender, birthdate, ethnicity, measurements, bio, etc.), `stash_studio_id` (nullable, linked Stash studio ID), `stash_studio_data` (nullable JSON, full Stash studio record pulled during sync), `max_video_age_days` (nullable, only download videos uploaded within this many days), and `min_duration_seconds` (nullable, only download videos longer than this many seconds).

## Schema (Video)

The `Video` model includes: `id`, `channel_id`, `site_video_id`, `title`, `url`, `upload_date`, `performers`, `studio`, `duration_seconds`, `thumbnail_url`, `original_filename`, `oshash`, `status`, `error_message`, `stash_scene_id`, `metadata_json`, `created_at`, `updated_at`, **`downloaded_at`** (nullable, set once when status becomes `downloaded`), **`synced_at`** (nullable, set once when status becomes `synced`), **`scrape_attempted_at`** (nullable, set when post-sync scrape completes successfully), and **`generate_triggered_at`** (nullable, set when post-sync Stash generate completes successfully). The milestone timestamps `downloaded_at` and `synced_at` are used for the dashboard “videos downloaded by day” chart; the chart counts by the date of `COALESCE(downloaded_at, synced_at)` over the last 90 days. `scrape_attempted_at` and `generate_triggered_at` enable the Backfill Scrape & Generate job to find synced videos that haven't had scrape/generate run yet.

## Video Status Lifecycle

```
pending -> downloading -> downloaded -> importing -> synced
   |            |              |             |
   |            +---> skipped  +-------------+---> failed
   |            (too short)
   +------------+--------------+-------------+---> failed

   imported (set by YTDLM import; not re-downloaded)
```

- **pending**: Video discovered during channel scan, queued for download.
- **downloading**: Download in progress via yt-dlp.
- **skipped**: Video skipped because it did not meet filter criteria (e.g. duration shorter than `min_duration_seconds`, or older than `max_video_age_days`). `error_message` explains the reason. Can be retried.
- **downloaded**: File saved to disk, oshash computed. Also used for import-retry: Retry sets this when oshash/filename exists so the pipeline retries import without re-downloading.
- **importing**: Stash `metadataScan` triggered, waiting for scene to appear.
- **synced**: Scene found in Stash, metadata (title, performers, studio, date) applied.
- **imported**: Video imported from YoutubeDL-Material; not downloaded by ytdl-stash. Treated as already-downloaded (not re-downloaded).
- **failed**: Error at any stage. `error_message` column stores the reason. **Retry** (import-only when possible) or **Redownload** (force fresh download) from the UI.

**Stuck-video recovery**: On startup, `init_db()` resets any videos left in intermediate states (`downloading`, `importing`, `cancelling`). If the file exists on disk (`original_filename` set), the video is recovered to `downloaded` instead of `pending` to avoid re-downloading.
