# Scrape & Generate Tracking + Backfill - Feb 7, 2026

## Overview

Not every synced video received scrape and generate because: (1) settings could disable them; (2) exceptions were swallowed (best-effort); (3) historical videos may have been synced before these features or when settings were off. There was no tracking or retry path.

## Implementation Approach

- **Option B (track + retry):** Add `scrape_attempted_at` and `generate_triggered_at` columns to `Video`. Set them when scrape/generate succeed in the pipeline and resync route. A background job finds synced videos missing timestamps and retries.
- **Option C (bulk backfill):** The same job acts as bulk backfill: on first run, all existing synced videos have null timestamps, so they are all processed. Subsequent runs only process videos that failed or are newly synced without timestamps.

## Changes Made

### Files Modified

- **app/models.py** — Added `scrape_attempted_at` and `generate_triggered_at` (TZDateTime, nullable) to `Video`.
- **app/database.py** — Added migration entries for the new columns.
- **app/pipeline.py** — Extracted `run_scrape_and_generate()` helper; pipeline post-sync now calls it and sets timestamps on success. Refactored inline scrape/generate/resync into the helper.
- **app/routes/videos.py** — Resync route now sets `video.scrape_attempted_at` and `video.generate_triggered_at` when scrape/generate complete successfully.
- **app/scheduler.py** — Added "Backfill Scrape & Generate" job (`backfill_scrape_generate`) that queries synced videos with null timestamps, processes up to 50 per run with 2s delay between each, and calls `run_scrape_and_generate`. Job is manual-trigger only (not scheduled).
- **docs/architecture/README.md** — Documented new Video columns.
- **docs/data-flow.md** — Updated error-handling table and Available Jobs table.

## Observations

- Timestamps are only set when the step completes without exception; failures leave them null so the backfill job can retry.
- The backfill job uses resync-style apply (no `existing_performer_ids`/`existing_studio_id`) so the scraper can fully overwrite.
- Jobs page iterates over `job_registry.values()`, so the new job appears automatically.
