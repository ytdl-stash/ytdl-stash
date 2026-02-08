# Stash Job Sync and Smart Retry - February 8, 2026

## Overview

Replaced timeout-based polling with job-based completion for Stash operations, added smart retry (oshash/title lookup before re-download), a Redownload button, and fixed the `original_filename` path bug for subdirectory layouts.

## Implementation Approach

1. **Job-based completion**: `metadataScan` and `metadataGenerate` return job IDs. Added `find_job` and `wait_for_job` to poll until terminal state instead of guessing with timeouts.
2. **Early scene lookup**: Before download, try `find_scene_by_oshash` then `find_scene_by_title` — handles retries where Stash finished importing after a previous timeout.
3. **Smart retry**: Retry sets `downloaded` (not `pending`) when oshash/filename exists so the pipeline retries import-only without re-downloading.
4. **Redownload button**: Explicit action to force fresh download when file was moved or corrupted.
5. **Organized after generate**: Reordered `run_scrape_and_generate` so generate runs first and waits for completion; organized is set last. Removes need for `stash_organized_settle_seconds`.
6. **Original filename fix**: Store `os.path.relpath(filepath, output_dir)` instead of basename so file-existence check works for subdirectory layouts.

## Changes Made

### Files Modified

- **app/stash_client.py** — Added `_FIND_JOB_QUERY`, `find_job`, `wait_for_job`, `find_scene_by_title`. `trigger_scan` now returns job ID. Deprecated `wait_for_scene`.
- **app/pipeline.py** — Early scene lookup at top of `process_single_download`. Replaced `wait_for_scene` with `wait_for_job` + single oshash/title lookup. Extracted `_apply_metadata_and_sync` helper. Reordered `run_scrape_and_generate`: generate (with wait) before organized; removed settle delay. Added `was_import_retry` and fail path for downloaded-with-no-file. Updated `process_pending_downloads` to also pick up `downloaded` videos without `stash_scene_id`.
- **app/routes/videos.py** — Retry sets `downloaded` when oshash/filename exists. New `redownload_video` route. Resync waits for generate job.
- **app/scheduler.py** — Download processor and Retry All Failed pick up both pending and downloaded (no scene). Retry All Failed uses per-row logic (downloaded vs pending).
- **app/downloader.py** — `original_filename` stores `os.path.relpath(filepath, output_dir)`.
- **app/templates/components/_video_actions.html** — Redownload button, updated Retry tooltip.
- **docs/data-flow.md**, **docs/recipes/troubleshooting.md**, **docs/patterns/stash-graphql.md**, **docs/architecture/README.md** — Updated for new flow.

## Trade-offs

- `stash_organized_settle_seconds` is no longer used; setting retained for backwards compat but can be deprecated.
- Retry All Failed is now per-row instead of bulk; slightly slower for many failed videos.
- Generate waits for completion, so first-sync and resync take longer when Stash is busy but are deterministic.
