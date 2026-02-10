# Queue-Aware Stash Job Timeout - February 9, 2026

## Overview

Fixed `wait_for_job` timing out when Stash's job queue is busy. The previous implementation used a single flat 300s timeout that started ticking the moment polling began — including time the job spent waiting in Stash's queue before it even started running. This caused spurious `RuntimeError: Stash job X timed out after 300s` failures when other Stash jobs were ahead in the queue.

## Implementation Approach

Replaced the single `timeout` parameter with two separate timeouts:

- **`queue_timeout`** (default 1800s / 30 min) — max wall-clock time while the job is queued (status is not yet `RUNNING` or terminal).
- **`run_timeout`** (default 300s / 5 min) — max wall-clock time once the job transitions to `RUNNING`.

The run timer only starts when the job's status first becomes `RUNNING`, so queue wait time never eats into the execution budget. This applies to all Stash jobs: scan jobs and generate jobs.

## Changes Made

### Files Modified

- **app/stash_client.py** — Rewrote `wait_for_job` with two-phase timeout logic. Tracks when job transitions to `RUNNING` and starts the run deadline at that point. Added debug log when the transition is detected.
- **docs/patterns/stash-graphql.md** — Updated `wait_for_job` signature and added note about queue-aware timeouts.
- **docs/data-flow.md** — Updated steps 6 and 10b to mention queue-aware timeout behavior.

## Observations

All 5 call sites (`pipeline.py` scan + generate, `routes/videos.py` resync-all + single resync, `routes/channels.py` channel resync) use the default parameters and benefit from this fix without any caller changes.

## Trade-offs

- The queue timeout is generous (30 min) to avoid false positives when Stash is processing a large backlog. If a job is truly stuck in the queue, it will take up to 30 min to detect. This is acceptable since the alternative (5 min flat timeout) was causing real failures.
