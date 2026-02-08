# Video detail: download progress on its own line - Feb 7, 2026

## Overview

On the video detail page, the downloading status bar is now on its own line below the status badge and channel link, instead of sharing or replacing the status line.

## Implementation Approach

- Introduced a `progress_own_line` layout in `_status_badge.html`: when true, the partial renders a block-level wrapper with (1) a first row: status badge + channel link, (2) a second row: download progress bar when status is `downloading`.
- Detail page uses this layout via `{% with progress_own_line=true, channel=video.channel %}` and no longer duplicates the channel link outside the partial.
- HTMX polling from the detail page uses `?detail=1` so the status_badge endpoint returns the same layout and includes the channel for the first row.

## Changes Made

### Files Modified

- `app/templates/videos/_status_badge.html` — Added `{% if progress_own_line %}` branch: div wrapper, first row (badge + channel), second row (progress). Non-detail usage unchanged.
- `app/templates/videos/detail.html` — Wrapped status include in `{% with progress_own_line=true, channel=video.channel %}` and removed the separate channel link from the paragraph.
- `app/routes/videos.py` — `video_status_badge` now accepts optional `detail` query param; when set, loads video with `selectinload(Video.channel)` and passes `progress_own_line` and `channel` to the template.

### Files Created

- `docs/journal/2026-02-07-video-detail-download-status-own-line.md`

## Testing Notes

- Open a video detail page; confirm status and channel are on one line.
- With a video in `downloading` state, confirm the progress bar appears on a new line below.
- Confirm HTMX poll uses `?detail=1` and that after refresh the layout and channel persist.
- List/table views unchanged (still use inline status badge with optional progress inside).
