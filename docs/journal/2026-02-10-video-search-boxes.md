# Video Search Boxes - Feb 10, 2026

## Overview

Added search boxes to filter videos by title on the Videos page and on the Videos section of the Channel Detail page. The implementation mirrors the existing channels search pattern: debounced input, clear button, and server-side filtering.

## Implementation Approach

- Replicated the channels search UX from `channels/_list_content.html`: debounced `hx-trigger="input changed delay:300ms, search"`, standalone search input with clear "✕" button when active.
- Videos page: search input lives inside the filter form; form's `hx-trigger` changed to `change from:select` so typing in search does not double-fire with the form.
- Channel detail: search input in the Videos section header; channel videos are filtered in-memory by title substring (case-insensitive) since the list is already loaded via `selectinload`.

## Changes Made

### Files Modified

- **app/routes/videos.py** — Added `search` query param to `list_videos`; applies `Video.title.ilike(f"%{escaped}%", escape="\\")` filter to base and count stmts; passes `search` in context.
- **app/routes/channels.py** — Added `search` query param to `channel_videos`; filters in-memory video list by title; passes `search=""` from `channel_detail` for initial render.
- **app/templates/videos/list.html** — Added search input and clear button inside filter form; changed form `hx-trigger` to `change from:select`; clear button builds URL explicitly without search (channels pattern).
- **app/templates/channels/_detail_card.html** — Added search input and clear button in Videos section header row.
- **app/templates/channels/_channel_videos.html** — Added `hx-include="#channel-video-search"` to polling wrapper so 10s refresh preserves search; added "No videos matching" message when search is active and empty.

## Testing Notes

- Videos page: filter by channel/status, type in search, verify results update; click clear; verify pagination retains search.
- Channel detail: type in search, verify table filters; click clear; verify 10s polling keeps search when active.
