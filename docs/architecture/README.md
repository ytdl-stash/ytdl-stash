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
| **Config** | `app/config.py` | Pydantic BaseSettings, reads `YTDL_*` env vars |
| **App Entry** | `app/main.py` | FastAPI factory, lifespan, static/template mounts |
| **Database** | `app/database.py`, `app/models.py` | SQLAlchemy async engine, Channel + Video models |
| **Downloader** | `app/downloader.py` | yt-dlp wrapper: scan channels, download videos, compute oshash |
| **Stash Client** | `app/stash_client.py` | Async httpx GraphQL client for Stash API |
| **Pipeline** | `app/pipeline.py` | Orchestration: download -> oshash -> scan -> match -> tag |
| **Scheduler** | `app/scheduler.py` | APScheduler periodic channel checks + download processing |
| **Performer Sync** | `app/performer_sync.py` | Auto-link channel performers to Stash on channel add |
| **YTDLM Import** | `app/ytdlm_import.py` | Import channels and videos from YoutubeDL-Material `local_db.json` |
| **Routes** | `app/routes/*.py` | FastAPI routers: dashboard, channels CRUD, videos, performers, settings |
| **Templates** | `app/templates/*.html` | Jinja2 + HTMX server-rendered UI |
| **Static** | `app/static/` | CSS overrides |

## Tech Stack Summary

| Layer | Technology | Why |
|-------|-----------|-----|
| Runtime | Python 3.12 | yt-dlp is a Python library; single language for everything |
| Web framework | FastAPI + Uvicorn | Async-native, automatic OpenAPI docs, dependency injection |
| Frontend | Jinja2 + HTMX | No build step, server-rendered, progressive enhancement |
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
  app/
    __init__.py
    main.py                     # FastAPI app factory + lifespan
    config.py                   # Pydantic BaseSettings
    database.py                 # Async engine, session, init_db (Phase 2)
    models.py                   # Channel, Video models (Phase 2)
    downloader.py               # yt-dlp wrapper (Phase 3)
    stash_client.py             # Stash GraphQL client (Phase 4)
    pipeline.py                 # Download-to-Stash orchestration (Phase 5)
    performer_sync.py           # Auto-link channel performers to Stash (Phase 11)
    scheduler.py                # APScheduler setup (Phase 6)
    routes/
      __init__.py
      dashboard.py              # GET /
      channels.py               # Channels CRUD
      videos.py                 # Videos list/detail/retry
      health.py                 # GET /health (Phase 10)
      performers.py             # Performer Browser + detail (Phase 11)
      settings.py               # Settings + Stash connectivity test
    templates/
      base.html
      dashboard.html
      error.html                # User-friendly error page (Phase 10)
      channels/
        list.html
        add.html
        _row.html               # HTMX partial: single channel row
      videos/
        list.html
        detail.html
        _table_body.html        # HTMX partial: video table rows
        _status_badge.html      # HTMX partial: status badge
      performers/
        list.html               # Performer Browser grid/list
        detail.html             # Performer detail with videos
        _card.html              # HTMX partial: single performer card
      settings.html
    static/
      style.css
  data/                         # SQLite DB (volume-mounted, gitignored)
  downloads/                    # Video files (volume-mounted, gitignored)
```

## Key Design Principles

1. **Single container**: The entire app runs in one Docker container. No message queues, no Redis, no external DB.
2. **Shared volume**: Downloads land in a directory that Stash also watches, enabling oshash-based scene matching.
3. **Sequential downloads**: One video at a time with configurable delay to avoid rate limiting by source sites.
4. **Idempotent operations**: Videos are tracked by `site_video_id`; re-scanning a channel skips already-known videos.
5. **Graceful failure**: Each video has an independent status lifecycle. One failure does not block others.
6. **Server-rendered UI**: No JavaScript build step. Jinja2 templates enhanced with HTMX for interactivity.

## Configuration

All config is via environment variables prefixed with `YTDL_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `YTDL_STASH_URL` | `http://localhost:9999` | Stash server URL |
| `YTDL_STASH_API_KEY` | `""` | Stash API key (if auth enabled) |
| `YTDL_DOWNLOAD_DIR` | `/downloads` | Where videos are saved |
| `YTDL_STASH_DOWNLOAD_DIR` | `None` | Path to downloads as Stash sees it (for path mapping) |
| `YTDL_DATA_DIR` | `/app/data` | Where SQLite DB lives |
| `YTDL_DEFAULT_CHECK_INTERVAL_HOURS` | `6` | Default hours between channel checks |
| `YTDL_DOWNLOAD_DELAY_SECONDS` | `5` | Seconds to wait between downloads |
| `YTDL_COOKIES_FILE` | `None` | Optional path to cookies.txt |
| `YTDL_YTDLP_OUTPUT_TEMPLATE` | `%(uploader)s - %(title)s [%(id)s].%(ext)s` | yt-dlp filename template |
| `YTDL_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Schema (Channel)

The `Channel` model includes: `id`, `name`, `url`, `site`, `enabled`, `check_interval_hours`, `last_checked_at`, `created_at`, `updated_at`, `stash_performer_id` (nullable, linked Stash performer ID), and `performer_image_url` (nullable, cached avatar/thumbnail URL from the tube site).

## Video Status Lifecycle

```
pending -> downloading -> downloaded -> importing -> synced
   |            |              |             |
   +------------+--------------+-------------+---> failed

   imported (set by YTDLM import; not re-downloaded)
```

- **pending**: Video discovered during channel scan, queued for download.
- **downloading**: Download in progress via yt-dlp.
- **downloaded**: File saved to disk, oshash computed.
- **importing**: Stash `metadataScan` triggered, waiting for scene to appear.
- **synced**: Scene found in Stash, metadata (title, performers, studio, date) applied.
- **imported**: Video imported from YoutubeDL-Material; not downloaded by ytdl-stash. Treated as already-downloaded (not re-downloaded).
- **failed**: Error at any stage. `error_message` column stores the reason. Can be retried.
