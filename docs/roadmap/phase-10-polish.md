# Phase 10: Polish and Hardening

**Status**: COMPLETE

## Prerequisites

- Phases 1–9 complete (all functional code exists)

## Deliverables

### Logging

- [x] Configure Python `logging` module in `app/main.py` (structured output, configurable level)
- [x] Add logging throughout: pipeline steps, scheduler jobs, Stash client calls, download progress
- [x] Log level configurable via `YTDL_LOG_LEVEL` env var (add to `config.py`)

### Error handling

- [x] Pipeline: wrap each step in try/except, save meaningful error_message to DB
- [x] Routes: user-friendly error pages/messages (not raw stack traces)
- [x] Stash client: handle connection refused, timeout, auth errors with clear messages
- [x] yt-dlp: catch `DownloadError`, geo-restriction, rate limiting errors

### Health check

- [x] Add `GET /health` endpoint returning `{"status": "ok", "db": true, "stash": true/false}`
- [x] DB health: verify async_session is initialized
- [x] Stash health: call `stash_client.health_check()`
- [x] Add Docker health check to `docker-compose.yml` (python urlopen, no curl in slim image)

### Graceful shutdown

- [x] Stop scheduler (wait for current jobs to complete or timeout)
- [x] Drain in-progress downloads (or mark as failed for retry)
- [x] Close database connections
- [x] Handle SIGTERM/SIGINT in the lifespan shutdown block

### Folder mapping (download location)

- [x] Add `YTDL_STASH_DOWNLOAD_DIR` for path translation when Stash sees downloads at a different path

### Database migrations (optional)

- [ ] Add Alembic for future DB schema migrations (recommended but not required for initial release)
- [ ] Generate initial migration from current models
- [ ] Document migration workflow in README

### Documentation

- [x] `README.md` — setup instructions, docker-compose example, configuration reference, screenshots
- [x] Verify all `docs/` files are up to date with actual implementation
- [x] Update `docs/roadmap/` phase files — mark Phase 10 as COMPLETE
- [x] Update `docs/architecture/README.md` directory structure if it changed

## Patterns to Follow

- `docs/patterns/fastapi.md` — lifespan shutdown pattern.
- `docs/recipes/add-config-setting.md` — for adding `YTDL_LOG_LEVEL`.
- `docs/recipes/troubleshooting.md` — document common issues and fixes.

## Key Implementation Notes

- Use `logging.getLogger(__name__)` in each module — don't pass loggers around.
- Log format should include timestamp, level, module, and message.
- Health check should be fast (< 2s) — don't run expensive operations.
- Graceful shutdown: set a reasonable timeout (e.g. 10s) then force-exit.
- README should be the primary entry point for new users — include prerequisites, quick start, config table, and troubleshooting.

## Acceptance Criteria

- [x] Logs appear in Docker container output (`docker compose logs`)
- [x] Failed operations have clear, actionable error messages in the DB
- [x] `/health` endpoint returns correct status for DB and Stash
- [x] Docker health check passes when app is running
- [x] App shuts down cleanly on `docker compose stop` (no orphaned downloads)
- [x] README has working quick-start instructions
- [x] All docs reflect the actual implementation

## Deviations

- Healthcheck uses `python -c "import urllib.request; ..."` instead of `curl` (curl not in python:3.12-slim).
- Alembic skipped for initial release; can be added later.
