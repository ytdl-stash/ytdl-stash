# Queue-Aware Renamer Settle & Verify-After-Generate - June 23, 2026

## Overview

Some videos imported but never got covers/sprites/previews — they sat `synced`
with `generate_triggered_at` NULL and no visible failure. Root cause is the
interaction between **Stash's single FIFO job queue** and a **renamer plugin**,
which the [June 8 renamer-race fix](2026-06-08-fix-import-renamer-race.md) only
partially closed.

The June 8 fix added `wait_for_scene_path_stable` so generate runs against the
file's post-rename location. But its stability check ("path unchanged across two
reads", ~6–11s budget) **cannot distinguish "moved and settled" from "hasn't
moved yet."** The renamer is itself an async/queued Stash hook, so when Stash's
job queue is backed up (often by work unrelated to this app — library scans,
generate-all, auto-tag, identify), the move fires long after the head-start. The
settle then returns the **pre-move path**, and generate races the move → the
silent "produces nothing" failure from the
[Feb 8 ordering fix](2026-02-08-fix-generate-organized-ordering.md) returns, plus
`original_filename` goes stale again.

## Root Cause

1. Stash runs all jobs in one sequential FIFO queue; the renamer hook is async.
2. Under a busy queue the renamer is delayed past `wait_for_scene_path_stable`'s
   short fixed window.
3. The window can't tell a not-yet-started move from a finished one, so it
   returns the original path and declares it stable.
4. Generate then runs against the soon-to-move file → empty/stale artifacts.
5. Generate is best-effort, so the video stays `synced` and the failure is
   silent; only `generate_triggered_at IS NULL` and a log warning reveal it.

## Implementation Approach

Gated behind a single opt-in flag so **default behavior is byte-for-byte
unchanged** for setups without a renamer.

### Queue-aware settle (`stash_client.wait_for_scene_path_stable`)

Added two params: `total_timeout` (bound the *whole* wait by wall-clock instead
of a fixed `attempts` count, so we keep polling for a slow move) and
`require_change` (insist on observing the path change at least once before
accepting it as stable — so a not-yet-started move can't masquerade as settled).
On timeout with no observed change it logs a warning and returns the last path
(best effort). `process_single_download`'s import settle now passes both, driven
by the new settings.

### Verify-after-generate (`pipeline.generate_for_scene`)

New shared helper that centralizes the generate call (previously duplicated in
4 places: the pipeline, backfill, regenerate, and 3 re-sync routes). When the
renamer flag is on, after the generate job finishes it re-reads the scene's
primary path; if it changed *during* the job, it settles and **regenerates
once**. With the flag off it's a plain trigger-and-wait — identical to before.

### Config

- `stash_import_settle_timeout_seconds` (default 600) — bound for the move wait.
- `stash_expect_renamer_on_import` (default **False**) — activates both the
  wait-for-change settle and verify-after-generate. Set True if a renamer runs
  on import.

## Changes Made

### Files Modified

- `app/config.py` — added `stash_import_settle_timeout_seconds` and
  `stash_expect_renamer_on_import`; clarified the (previously stale)
  `stash_organized_settle_seconds` comment.
- `app/stash_client.py` — `wait_for_scene_path_stable` gained `total_timeout`
  and `require_change` with wall-clock deadline logic.
- `app/pipeline.py` — added `generate_for_scene`; `run_scrape_and_generate` and
  `process_single_download` now use it / the new settle params.
- `app/routes/videos.py`, `app/routes/channels.py` — the 3 manual re-sync
  generate blocks now call the shared `generate_for_scene`.
- `README.md`, `docs/data-flow.md` (steps 7 & 10b) — documented the new vars and
  behavior.

## Verification

No test suite in the repo. Validated the real `wait_for_scene_path_stable` and
`generate_for_scene` with a throwaway async harness (fake scripted Stash client,
run under `.venv`): constant-path fast return; require_change waits for the move
and returns the new path; require_change + no move hits the timeout and warns;
no-renamer triggers generate exactly once; renamer + mid-generate move triggers
a single regenerate. All passed. Byte-compiled all five changed files.

## Observations / Trade-offs

- The fix is **opt-in**. Without `stash_expect_renamer_on_import`, a misconfigured
  no-renamer user would otherwise eat the full `total_timeout` per import — so the
  default stays fast and the renamer crowd opts in. This user runs a renamer, so
  they should set `YTDL_STASH_EXPECT_RENAMER_ON_IMPORT=true`.
- This addresses the renamer half of the "videos not generating" problem. The
  other half — plain queue starvation (generate waiting >30 min, or >5 min once
  running, under a large backlog) — is separate; raising `wait_for_job`'s
  `run_timeout`/`queue_timeout` would be a follow-up if logs show those.
- Recovery for already-affected videos: run **Backfill Scrape & Generate** (or
  **Regenerate All**) once Stash's queue is drained.
