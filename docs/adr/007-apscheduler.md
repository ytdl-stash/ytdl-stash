# ADR-007: Use APScheduler for Periodic Jobs

**Status**: Accepted

## Context

The app needs to periodically:
1. Check each enabled channel for new videos (on a per-channel interval).
2. Process the download queue (download pending videos, with configurable concurrency).

This requires a scheduler that runs inside the same Python process as the FastAPI app.

## Decision

Use **APScheduler 3.x** (`AsyncIOScheduler`) for scheduling periodic tasks.

## Alternatives Considered

### Celery + Redis/RabbitMQ
- Industry standard for distributed task queues.
- Requires a separate broker (Redis or RabbitMQ) and worker process.
- Massive overkill for a single-container, single-user app.
- Rejected to keep the deployment simple.

### asyncio.create_task + sleep loop
- Pure stdlib, no dependency.
- Lacks: job persistence, error recovery, graceful shutdown, interval management.
- Fragile: if a task crashes, the loop dies.
- Rejected as too primitive.

### Huey / RQ
- Lightweight task queues.
- Still require a separate broker (Redis).
- Rejected for same reason as Celery.

### Cron (external)
- Run a script on a cron schedule.
- Requires the FastAPI app to be stateless or expose an internal trigger endpoint.
- Doesn't work well inside a single container.
- Rejected because the scheduler needs access to the DB session and pipeline functions.

## Consequences

**Positive:**
- Runs in-process on the same event loop as FastAPI.
- `AsyncIOScheduler` natively supports async job functions.
- Simple API: `scheduler.add_job(func, "interval", seconds=60)`.
- Starts and stops cleanly via FastAPI lifespan events.
- No external dependencies (no Redis, no broker).

**Negative:**
- Jobs are not persisted across restarts (acceptable: we use the DB as the source of truth for pending work).
- No distributed execution (not needed: single container).
- APScheduler 4.x is a rewrite with a different API; we pin to 3.x.

## Architecture

```
FastAPI lifespan startup
  |
  +-> scheduler.start()
        |
        +-> "channel_checker" job (every 60 seconds)
        |     Query all enabled channels where last_checked_at is stale.
        |     For each: scan channel, insert new videos as "pending".
        |     Update last_checked_at.
        |
        +-> "download_processor" job (every 30 seconds)
              Query pending videos with status="pending" (up to configured concurrency).
              Run the download-to-Stash pipeline.
              Apply download_delay_seconds as a throttle (between downloads or staggered starts).

FastAPI lifespan shutdown
  |
  +-> scheduler.shutdown(wait=True)
        Lets running jobs finish before exit.
```

## References

- [APScheduler 3.x docs](https://apscheduler.readthedocs.io/en/3.x/)
- [APScheduler AsyncIOScheduler](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html)
