# ytdl-stash Roadmap

Per-phase build plan. Each file is self-contained: read **one file** to know exactly what to build, which patterns to follow, and what's already done.

## How to Use This

When starting a phase:
1. Read `docs/roadmap/phase-XX-name.md` for that phase.
2. Read the **Patterns to follow** docs listed in the phase file.
3. Build the **Deliverables** checklist top-to-bottom.
4. After completing a deliverable, mark it `[x]` in the phase file.
5. Log any deviations from the original plan in the **Deviations** section.

## Phase Status Overview

| Phase | Name | Status | Key Deliverables |
|-------|------|--------|-----------------|
| 1 | [Scaffold](phase-01-scaffold.md) | **COMPLETE** | requirements.txt, Dockerfile, docker-compose, config.py, main.py |
| 2 | [Database](phase-02-database.md) | **COMPLETE** | database.py, models.py (Channel + Video) |
| 3 | [Downloader](phase-03-downloader.md) | **COMPLETE** | downloader.py (scan, download, oshash) |
| 4 | [Stash Client](phase-04-stash-client.md) | **COMPLETE** | stash_client.py (GraphQL client) |
| 5 | [Pipeline](phase-05-pipeline.md) | **COMPLETE** | pipeline.py (download-to-Stash orchestration) |
| 6 | [Scheduler](phase-06-scheduler.md) | **COMPLETE** | scheduler.py (APScheduler periodic jobs) |
| 7 | [Routes](phase-07-routes.md) | **COMPLETE** | app/routes/ (channels, videos, settings, dashboard) |
| 8 | [Web UI](phase-08-ui.md) | **COMPLETE** | Jinja2 + HTMX templates |
| 9 | [Docker](phase-09-docker.md) | **COMPLETE** | Dockerfile + docker-compose finalization |
| 10 | [Polish](phase-10-polish.md) | **COMPLETE** | Logging, error handling, health check, folder mapping, README |
| 11 | [Performer Sync](phase-11-performer-sync.md) | **COMPLETE** | Auto-link performers to Stash, Performer Browser UI |
| 12 | [YTDLM Import](phase-12-ytdlm-import.md) | **COMPLETE** | Import subscriptions & videos from YoutubeDL-Material `local_db.json` |

## Dependency Graph

```
Phase 1 (Scaffold)
  └─> Phase 2 (Database)
        └─> Phase 3 (Downloader)
        │     └─> Phase 5 (Pipeline) <── Phase 4 (Stash Client)
        │           └─> Phase 6 (Scheduler)
        └─> Phase 7 (Routes) ──────────> Phase 8 (Web UI)
                                            └─> Phase 9 (Docker)
                                                  └─> Phase 10 (Polish)
  Phase 4 + Phase 7 + Phase 8 ─> Phase 11 (Performer Sync)
  Phase 2 + Phase 7 + Phase 8 ─> Phase 12 (YTDLM Import)
```

Phases 3 and 4 can be built in parallel. Phase 5 requires both.
Phases 7 and 8 require Phase 2 (models) but can start before Phase 5/6 with stub data.
Phase 11 requires Phases 4, 7, and 8 (Stash Client, Routes, UI). Can start before Phase 10.
Phase 12 requires Phases 2, 7, and 8 (Database, Routes, UI). Independent of Phases 11.
