# Fix Generate Killed by Organized File-Move - February 8, 2026

## Overview

Preview/sprite generation stopped working after adding the organized flag. The root cause was a race condition: setting `organized=True` triggers a Stash file-move rule that relocates the video, but the generate job (queued asynchronously in Stash's sequential job queue) hadn't started yet. By the time generate ran, the file was no longer at the expected path.

A secondary issue: the backfill job couldn't recover affected videos because `generate_triggered_at` was already set (the mutation succeeded — it was the Stash-side job that failed silently).

## Root Cause

Stash's job manager processes jobs **one at a time** in a FIFO queue. The timeline was:

1. `trigger_generate` → queues a generate job in Stash (behind the still-running scan)
2. `update_scene(organized=True)` → **immediate** GraphQL mutation, not queued — triggers file-move rule
3. File gets relocated to its organized path
4. Generate job finally starts → file not found at original path → silently produces nothing

The backfill job was also inefficient: it always ran both scrape and generate even when only one timestamp was missing.

## Implementation Approach

### Ordering fix

Moved the `organized=True` call into `run_scrape_and_generate`, **between** the scrape step and the generate step. This ensures:

1. Scrape runs first (URL-based, doesn't need the file on disk)
2. Organized flag is set → file moves to final location
3. Generate is queued → file is already at its final path when the job runs

Added a `set_organized: bool = False` keyword parameter to `run_scrape_and_generate` so callers opt-in explicitly. Only the first-download path passes `True`; the backfill/resync paths leave it `False` since those scenes are already organized.

### Settle delay

Even with the correct ordering, the file-move triggered by `organized=True` may not complete instantly — Stash runs it as a post-hook. If the generate job starts before the move finishes, Stash silently produces nothing. Added a configurable `stash_organized_settle_seconds` (default 5) and an `asyncio.sleep()` between the organized step and the generate step. This gives Stash time to complete the file-move before the generate job is queued.

### Backfill improvements

- Added `skip_scrape` / `skip_generate` parameters to `run_scrape_and_generate` so the backfill only runs the step(s) that are actually missing per video.
- The backfill now checks which timestamp is null per-video and skips the already-completed step.

### Regenerate All job

Added a new "Regenerate All" job (`POST /jobs/regenerate_all/trigger`) that:
1. Resets `generate_triggered_at` to NULL on all synced videos
2. Walks through each one, triggering Stash generate (skips scrape)

This is a one-shot recovery tool for videos affected by the race condition bug.

## Changes Made

### Files Modified

- `app/config.py` — Added `stash_organized_settle_seconds: int = 5` to give Stash time to complete the file-move before generate.
- `app/pipeline.py` — Moved organized flag into `run_scrape_and_generate` (step 2, between scrape and generate). Added settle delay after organized, before generate. Removed the separate organized block from `process_single_download`. Added `set_organized`, `skip_scrape`, `skip_generate` parameters.
- `app/stash_client.py` — `trigger_generate` now returns the Stash job ID and logs it (minor improvement for debugging).
- `app/scheduler.py` — Backfill job now skips already-completed steps per video. Added new "Regenerate All" job.
- `docs/data-flow.md` — Added step 10b documenting the organized ordering. Removed stale organized reference from step 12. Added Regenerate All to jobs table.

## Observations

- Stash's `update_scene` mutations execute immediately (not queued), but `metadataGenerate` and `metadataScan` are queued jobs that run sequentially. This asymmetry is the root of the race.
- The file-move rule triggered by `organized=True` may run asynchronously on Stash's side, hence the need for a settle delay before queuing generate.
- The scrape step is URL-based and doesn't need the file on disk, so it's safe to run before the file-move.
- If the organized step fails, generate still fires and the file stays at its original (scan-time) location, which is still valid.
- `generate_triggered_at` only means "we sent the mutation" — not "Stash finished generating." There's no way to verify completion without polling Stash's job queue.
