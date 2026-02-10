# Fix Imported Videos Being Re-Downloaded - February 10, 2026

## Overview

YTDLM-imported videos were being re-downloaded when their channel was scanned. The root cause was a `site_video_id` mismatch: YTDLM stored the video **title** as the `id` field, while yt-dlp returns the real platform video ID (e.g. `dQw4w9WgXcQ`). The channel scan's dedup check only compared `site_video_id`, so it treated every imported video as "new" and created duplicate `pending` records.

## Implementation Approach

Added a secondary dedup layer to the channel scan that matches by **URL** (preferred) or **title** (fallback, scoped to the same channel). When a match is found against an existing video whose `site_video_id` doesn't match, the scan **back-fills** the correct `site_video_id` (and URL if different) on the existing record instead of creating a new one. This:

1. Prevents re-downloading imported videos.
2. Fixes the imported video's `site_video_id` so future scans use the fast primary dedup path.
3. Preserves the video's `imported` status — it is not re-downloaded or re-processed.

## Changes Made

### Files Modified

- **`app/pipeline.py`** — `_process_channel_scan_locked()`:
  - Added query to load all videos for the current channel into URL and title lookup dicts.
  - Added secondary dedup check after the primary `site_video_id` check.
  - When matched, back-fills `site_video_id` and `url` on the existing record.
  - Added `backfilled` counter to the summary log line.

## Challenges Encountered

- **Title matching risk**: Two videos on the same channel could theoretically share a title, causing a false match. Mitigated by trying URL first (more specific) and only falling back to title. The risk is low because title matches are scoped to a single channel, and a false positive only causes a backfill (not data loss).

## Trade-offs

- **Loading all channel videos**: The secondary dedup loads all `Video` objects for the channel. For channels with thousands of imported videos this adds memory/query overhead, but it only runs once per scan and the data is small per row. Could be optimized to load only needed columns if performance becomes an issue.

## Testing Notes

- On next channel scan, imported videos should be recognized and back-filled (visible in logs as `back-filled site_video_id 'Old Title' -> 'abc123'`).
- After back-fill, subsequent scans will match via the primary `site_video_id` check and skip the secondary lookup entirely.
