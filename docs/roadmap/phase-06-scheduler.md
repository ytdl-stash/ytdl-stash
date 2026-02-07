# Phase 6: Scheduler

**Status**: COMPLETE

## Prerequisites

- Phase 2 complete (models for channel queries)
- Phase 5 complete (pipeline functions to call)

## Deliverables

- [x] `app/scheduler.py` — APScheduler setup with two jobs
- [x] Update `app/main.py` — start/stop scheduler in lifespan

### Scheduler setup

- Use `APScheduler` `AsyncIOScheduler`
- Two jobs:

**Job 1: `channel_checker`** (interval: 60 seconds)
- Query all enabled channels where `last_checked_at` is stale or NULL
- For each due channel, call `process_channel_scan()`
- `max_instances=1` to prevent overlap

**Job 2: `download_processor`** (interval: 30 seconds)
- Call `process_pending_downloads()`
- `max_instances=1` to prevent overlapping job runs (download concurrency is controlled separately)

### Lifespan integration

```python
# In app/main.py lifespan:
# Startup:
scheduler.start()

# Shutdown:
scheduler.shutdown(wait=False)
```

## Patterns to Follow

- `docs/adr/007-apscheduler.md` — why APScheduler, async compatibility.
- `docs/patterns/fastapi.md` — lifespan pattern for start/stop.
- `docs/data-flow.md` — steps 2 and 4 describe the scheduler's role.

## Key Implementation Notes

- `max_instances=1` on both jobs prevents overlapping runs.
- The channel checker runs every 60s but only scans channels that are actually due (based on their `check_interval_hours`).
- The download processor picks up pending videos per cycle (up to `YTDL_MAX_CONCURRENT_DOWNLOADS`). The delay throttle is handled inside the pipeline/scheduler download processing, not the scheduler interval.
- Scheduler must be gracefully shut down on app exit to avoid orphaned jobs.

## Acceptance Criteria

- [x] `AsyncIOScheduler` starts on app startup
- [x] `channel_checker` runs every 60s and scans due channels
- [x] `download_processor` runs every 30s and processes pending videos (up to configured concurrency)
- [x] Both jobs have `max_instances=1`
- [x] Scheduler shuts down cleanly on app shutdown
- [x] `app/main.py` lifespan updated with scheduler start/stop
- [x] No scheduler runs until the app starts (not at import time)

## Deviations

(none yet)
