# Fix Renamer-on-Import Race & Stale File Path - June 8, 2026

## Overview

Files were occasionally getting "messed up" when ytdl-stash imported them into Stash — most visibly on **YoutubeDL-Material imports**. The cause was a Stash **renamer plugin** that moves/renames the file on **import** (not on `organized`, as the [Feb 8 generate-ordering fix](2026-02-08-fix-generate-organized-ordering.md) assumed). ytdl-stash's `trigger_scan → wait_for_job` only waits for the **scan** job; the renamer runs as a separate async post-hook, so the app proceeded to `update_scene`/`generate` while the file was still being moved, and never recorded the new path. `video.original_filename` was left permanently stale.

This invalidates an observation in the Feb 8 entry — *"the file stays at its original (scan-time) location, which is still valid."* With a renamer that fires on import, the file does **not** stay put.

## Root Cause

1. `trigger_scan([path])` → Stash imports the file and creates the scene.
2. The renamer plugin (a post-import hook) starts moving/renaming the file **asynchronously**.
3. `wait_for_job(scan_job_id)` returns when the **scan** finishes — it does not cover the renamer hook.
4. ytdl-stash immediately runs `update_scene` and queues `generate`, racing the in-flight move → "occasionally" corrupt/empty generation.
5. `video.original_filename` is never reconciled with Stash, so the DB path goes stale.

**Why imports specifically:** `map_file_to_video` stores `original_filename = file.path` (a **full path**) and no oshash, whereas downloads store a bare filename. Path consumers do `os.path.join(download_dir, original_filename)`; with an absolute import path that **escapes `download_dir`** (e.g. redownload's `os.remove`), and with no oshash, scene matching falls back to fuzzy title lookup.

## Implementation Approach

### Wait for the renamer to settle (the race fix)

Added `StashClient.wait_for_scene_path_stable(scene_id, settle, interval, attempts)`: after an initial `settle` head-start (reusing `stash_organized_settle_seconds`), it polls the scene until its primary `files[0].path` is unchanged across two reads, then returns it. `process_single_download` calls this **after** the scan finds the scene and **before** `_apply_metadata_and_sync`, so generate no longer races the move. A new `"Finalizing in Stash"` progress phase surfaces the wait in the UI.

### Reconcile the on-disk path from Stash

Once the path settles, set `video.original_filename = os.path.basename(settled_path)`. Done on both the import-scan path and the early-scene-lookup path. This keeps the DB consistent with the renamer and normalizes imports (full path → bare filename, matching downloads). Required adding `files { path }` to the `findScene` (by-id) query, which previously omitted it.

### Guard the redownload deletion

`redownload_video` now only `os.remove`s a file that genuinely resolves **under** `download_dir`, so an absolute/imported `original_filename` can no longer escape the join and delete an unintended file. The real Stash-managed file is still removed via `destroy_scene(delete_file=True)`.

## Changes Made

### Files Modified

- `app/stash_client.py` — Added `files { path }` to `_FIND_SCENE_BY_ID_QUERY`; added `scene_primary_path()` and `wait_for_scene_path_stable()`.
- `app/pipeline.py` — After the scan finds the scene, wait for path settle + reconcile `original_filename`; same reconcile on the early-scene-lookup path. Added the `"Finalizing in Stash"` phase.
- `app/routes/videos.py` — `redownload_video` guards `os.remove` against paths outside `download_dir`.
- `README.md` — Documented `YTDL_STASH_ORGANIZED_SETTLE_SECONDS` (was undocumented since v0.22.0); its description now covers the import-scan settle.
- `docs/data-flow.md` — Step 7 documents the settle + path reconciliation; corrected the "no race with file-move" notes in steps 10b/10c.

## Observations

- `stash_organized_settle_seconds` had been **orphaned**: defined since v0.22.0 but unused after generate was reordered to run before `organized` (the architecture doc literally marked it *"Deprecated. No longer used."*). This fix **re-activates** it for the import-scan settle instead of adding a new setting — so the docs that called it deprecated had to be corrected. Could be renamed/split to `YTDL_STASH_IMPORT_SETTLE_SECONDS` later if the shared semantics feel off.
- Whether a renamer fires on import vs `organized` is plugin-config-dependent, so the settle is written to tolerate both (and `0` disables the head-start for users with no renamer).
- oshash is content-based, so `find_scene_by_oshash` still links correctly after a move; the damage was the stale path and the generate-during-move race, not the link itself.
