# Prevent Duplicate Downloads - February 8, 2026

## Overview

Added three layers of protection against wasteful re-downloads that could occur when the server restarts mid-pipeline or when videos get reset to `pending` status.

## Problem

Videos could be downloaded multiple times in these scenarios:

1. **Server crash mid-download/import** — startup recovery blindly reset all `downloading`/`importing`/`cancelling` videos to `pending`, causing a full re-download even if the file already existed on disk.
2. **No file-existence check in pipeline** — `process_single_download` would always call yt-dlp without checking whether the file was already on disk from a previous attempt.
3. **yt-dlp would overwrite existing files** — no `nooverwrites` option was set, so yt-dlp would happily re-download and overwrite files at the same output path.

## Implementation Approach

Three complementary layers of protection, each catching what the others miss:

1. **Smart startup recovery** (`database.py`) — before resetting a stuck video to `pending`, check if `original_filename` is set and the file exists on disk. If so, recover to `downloaded` status instead, allowing the import pipeline to resume without re-downloading.

2. **File-existence fast-path** (`pipeline.py`) — at the start of `process_single_download`, before calling yt-dlp, check if `video.original_filename` is set and the file exists. If so, skip the entire download phase (including pre/post-download filters) and jump directly to oshash computation and Stash import.

3. **yt-dlp `nooverwrites` safety net** (`downloader.py`) — set `nooverwrites: True` in the download options so even if yt-dlp is somehow reached for an existing file, it will skip the download rather than overwriting.

## Changes Made

### Files Modified

- **`app/database.py`** — Rewrote `_recover_stuck_videos()` to accept `settings` parameter and check for existing files on disk per-row. Videos with files on disk recover to `downloaded` instead of `pending`. Added `os` import.
- **`app/pipeline.py`** — Added file-existence fast-path at the top of `process_single_download()`. Restructured the download section so the download + filters are only executed when the file doesn't already exist. Unified the filepath variable (`existing_filepath`) used by oshash/scan/import steps regardless of whether the file was freshly downloaded or already on disk.
- **`app/downloader.py`** — Added `"nooverwrites": True` to `_build_download_opts()`.

## Trade-offs

- **`nooverwrites` prevents intentional re-downloads via yt-dlp** — if a user genuinely wants to re-download a corrupted file, they would need to delete the file from disk first and then retry. This is acceptable because: (a) the file-existence fast-path in the pipeline already skips the download when the file exists, so the user would need to delete the file regardless; (b) explicit user retry is the right UX for this edge case.
- **Startup recovery is now per-row instead of bulk UPDATE** — slightly slower on startup if many videos are stuck, but the file-existence check is worth it. In practice, stuck videos should be rare (only after crashes).
- **Files in non-standard locations won't be detected** — the file-existence check joins `download_dir` + `original_filename`. If the file was moved (e.g. by a Stash file-move rule after organized=True), the check won't find it. This is acceptable because moved files should already be in `synced` status, not stuck in intermediate states.

## Observations

- The `original_filename` field is only populated after a successful download, so the fast-path only activates for videos that previously completed the download phase. Brand-new `pending` videos without `original_filename` always go through the full download path.
- The three layers are complementary: startup recovery catches crashes, the pipeline fast-path catches retries/resets, and `nooverwrites` is a last-resort safety net in yt-dlp itself.
