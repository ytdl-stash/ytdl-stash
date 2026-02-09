# Fix: Channel Delete Doesn't Cancel Active Downloads - February 9, 2026

## Overview

Deleting a channel while one of its videos was mid-download left the download running in the background. The download would eventually fail with errors when it tried to access the deleted channel's data.

## Root Cause

`delete_channel()` performed a hard delete (with cascade to videos) but never told the download pipeline to stop. The `download_control` cancellation mechanism existed but was only wired up to the manual "Stop" button — not to channel deletion.

Additionally, `process_single_download()` assumed `video.channel` was always non-null after refresh, which could cause `AttributeError` if the channel row was already gone.

## Implementation Approach

Two-layer fix:

1. **Proactive cancellation** — Before deleting the channel, check `download_control.get_active_ids()` for any in-flight downloads belonging to this channel's videos, and call `request_cancel()` for each. The yt-dlp progress hook picks up the flag and raises `DownloadCancelled`.

2. **Defensive guard** — In `process_single_download()`, after refreshing the video's channel relationship, bail out early with a log warning if `channel is None`. This covers the race condition where a video is queued but the channel is deleted before it starts processing.

## Changes Made

### Files Modified

- **`app/routes/channels.py`** — Added `download_control` import; updated `delete_channel()` to cancel active downloads for the channel's videos before issuing the cascade delete.
- **`app/pipeline.py`** — Added `channel is None` guard at the top of `process_single_download()` to skip processing when the channel has been deleted.

## Observations

- The `finally` block in `process_single_download()` already cleans up `download_control` state, so the early return is safe.
- `_apply_metadata_and_sync()` already handled `channel is None` gracefully (line 284), so no change was needed there.
- The fix is efficient: only one extra DB query runs on delete, and only when there are active downloads.
