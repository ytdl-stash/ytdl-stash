# Bulk Video Actions (Re-sync All, Retry All Skipped) - February 8, 2026

## Overview

Added two bulk action buttons to the video detail page top bar:

1. **Re-sync All from Stash** — Re-scrapes metadata and optionally regenerates (covers, previews, sprites, phashes) for every video that has a `stash_scene_id`. Runs as a background task.
2. **Retry All Skipped** — Resets all `skipped` videos back to `pending` (or `downloaded` if the file exists on disk) so the pipeline re-evaluates them against the channel's current filter settings (`min_duration_seconds`, `max_video_age_days`). This is useful after relaxing filter thresholds so previously-excluded videos can be processed.

## Implementation Approach

- **Re-sync All**: Runs as a background `asyncio.Task` to avoid blocking the request. Each video is processed sequentially with its own DB session and StashClient connection.
- **Retry All Skipped**: A synchronous bulk DB update (no background task needed). Resets status and clears error messages; the existing scheduler picks up the re-queued videos automatically.

## Changes Made

### Files Modified

- **`app/routes/videos.py`**:
  - Added `POST /videos/resync_all` — background task for bulk re-sync of all synced videos.
  - Added `POST /videos/retry_all_skipped` — bulk reset of all skipped videos to pending/downloaded.
  - Both routes are placed before `/{video_id}` to avoid path conflicts.
- **`app/templates/videos/detail.html`** — Added both buttons in a flex container in the top bar (between the back link and the video card). Each has its own HTMX target container, confirmation dialog, loading spinner, and swaps in a success/warning message after completion.

## Trade-offs

- **Sequential re-sync**: Videos are re-synced one at a time to avoid overwhelming the Stash server. Slower but safer.
- **No progress tracking**: Background tasks log progress but there's no UI polling for completion. Users see a count message and check logs for details.
- **Retry is immediate**: All skipped videos are re-queued at once. If filters haven't actually changed, they'll just be re-skipped by the pipeline — harmless but wastes a bit of processing.
- **Button placement**: Both buttons live on the video detail page (per user request) as global actions regardless of which video is being viewed.
