# Video status error tooltip and channel video section UX — Feb 8, 2026

## Overview

- Status badges for videos with an error (failed, cancelled, skipped) now show the stored `error_message` in a DaisyUI tooltip on hover.
- The channel detail page’s video section was aligned with the main Videos page: active downloads (channel-scoped), status legend, thumbnails in the table, and 10s polling for the video list.

## Implementation Approach

- **Tooltip**: In `_status_badge.html`, when `video.error_message` is set, the status span gets `tooltip tooltip-bottom` and `data-tip="{{ video.error_message|e }}"` in both inline and detail layouts.
- **Channel video section**: Reused existing partials (`_active_downloads.html`, `_status_legend.html`, `_table_body_performer.html`) and added channel-specific endpoints and one new partial (`_channel_videos.html`) for the pollable table.

## Changes Made

### Files Created

- `app/templates/channels/_channel_videos.html` — Wrapper div with `hx-get="/channels/{{ channel.id }}/videos"` and `hx-trigger="every 10s"` containing the video table (thead + `_table_body_performer.html`).
- `docs/journal/2026-02-08-video-status-tooltip-and-channel-videos-ux.md`

### Files Modified

- `app/templates/videos/_status_badge.html` — Conditional tooltip on status text when `video.error_message` is present (both block and inline modes); `data-tip` uses `video.error_message|e`.
- `app/templates/videos/_active_downloads.html` — `hx-get` uses `{{ poll_url or '/videos/active_downloads' }}` so channel detail can pass a channel-scoped poll URL.
- `app/templates/videos/_table_body_performer.html` — Added first column: Thumb (reusing `_video_thumbnail.html` with `size='md'`, `link_url` to video detail).
- `app/templates/channels/_detail_card.html` — Import `collapse` macro; set `poll_url` and add “Active downloads” collapse (including `_active_downloads.html`); include `_status_legend.html`; replace inline table with `{% include "channels/_channel_videos.html" %}`.
- `app/routes/channels.py` — `ACTIVE_DOWNLOAD_STATUSES`; `active_videos` computed and passed in `channel_detail` and all `_channel_sync_response` call paths (sync, toggle, update); `GET /{channel_id}/active_downloads` (returns `_active_downloads.html` with `poll_url` and channel-filtered `active_videos`); `GET /{channel_id}/videos` (returns `_channel_videos.html` for HTMX refresh).
- `docs/architecture/README.md` — Template list: `_table_body_performer.html` description (thumb, no channel column); `_channel_videos.html` under channels; `_active_downloads.html` note (optional `poll_url`); `_status_badge.html` note (error tooltip).

### Files Deleted

- None.

## Challenges Encountered

- Ensuring all HTMX responses that render `_detail_card.html` (sync, toggle, update) also pass `active_videos` so the active downloads panel stays correct after actions.
- Channel-scoped active downloads: reusing `_active_downloads.html` with an optional `poll_url` and adding `GET /channels/{id}/active_downloads` so the panel only shows and refreshes this channel’s active videos.

## Observations

- The video count in “Videos (N)” on channel detail is from the initial page render and does not update when the table is refreshed by polling; only the table body is swapped.

## Trade-offs

- `_table_body_performer.html` is used only on channel detail; adding the thumb column there keeps one shared partial and matches the Videos page layout (Thumb | Title | Status | Upload Date | Duration | Actions) without a Channel column.

## Next Steps (Future Considerations)

- None.

## Testing Notes

- Manually verify: on /videos and /channels/{id}, hover a failed/cancelled/skipped video’s status and confirm the error message appears in the tooltip.
- On /channels/{id}: confirm Active downloads (collapsed) and Status legend appear; table has thumbnails; after 10s the table refreshes without full page reload; active downloads panel (when open) refreshes every 3s with only that channel’s active videos.
