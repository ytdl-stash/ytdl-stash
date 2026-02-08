# Bug review: recovery and list filters - Feb 7, 2026

## Overview

Codebase review for bugs across modified app code (database, downloader, main, models, pipeline, routes, scheduler). No test suite or linter errors; review was manual.

## Changes Made

### app/database.py

- **Stuck-video recovery**: Removed `"downloaded"` from `_STUCK_STATUSES` in `_recover_stuck_videos`. Previously, videos in status `downloaded` (file on disk, not yet importing) were reset to `pending` on every startup, causing the download processor to re-download and overwrite. Docstring updated to state we do not reset `downloaded`; only `downloading`, `importing`, and `cancelling` are recovered. Stuck `downloaded` videos can be retried manually.

### app/routes/videos.py

- **List filter**: Treat empty string as “no filter”: `status_clean = (status.strip() or None) if status else None` so that `?status=` or `?status=  ` does not filter by literal `""` (which would match no rows).

## Other observations (no code change)

- **Config**: `max_concurrent_downloads` is defined in `app/config.py`; scheduler’s `getattr(settings, "max_concurrent_downloads", 1)` is redundant but safe.
- **Session commit**: Routes that mutate models (e.g. resync_video, performer_toggle) rely on `get_db`’s post-yield `session.commit()`; order is correct.
- **performers toggle**: Toggle updates the in-memory channel; HTMX branch re-queries and gets the same identity-map object, so template sees the new value; commit happens at end of request. No bug.
- **extract_video_info**: Handles `info is None` and missing fields; no change needed.
- **Status badge**: All statuses used in pipeline/routes (pending, downloading, downloaded, importing, synced, failed, cancelled, skipped, cancelling, imported) are covered in `main.status_badge_class`.

## Testing Notes

- No automated tests run (no pytest in repo). Manual verification: linter clean on touched files.
