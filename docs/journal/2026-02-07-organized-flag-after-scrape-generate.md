# Organized flag set after scrape and generate - Feb 7, 2026

## Overview

The Stash scene "organized" flag was not reliably being set. It was previously set inside `_resync_scene_from_stash`, which could fail (e.g. `find_scene_by_id` errors) before reaching the organized update. We moved setting `organized=True` to a dedicated post-sync step that runs after scrape and generate have been run, so it no longer depends on resync succeeding.

## Implementation Approach

- Remove the organized update from `_resync_scene_from_stash` (resync remains for logging/verification of scraper results).
- Add a new post-sync step 4: call `stash.update_scene(scene_id=scene["id"], organized=True)` in a try/except after steps 1 (scrape), 2 (generate), and 3 (resync). Failures are logged as non-fatal.

## Changes Made

### Files Modified

- **app/pipeline.py**
  - `_resync_scene_from_stash`: removed the block that checked `scene.get("organized")` and called `update_scene(..., organized=True)`.
  - Post-sync: added step 4 to mark the scene as organized after scrape and generate have been run; uses same non-fatal try/except pattern as other post-sync steps.

## Observations

- Generate is fire-and-forget; we set organized after we have *triggered* scrape and generate, not after Stash has finished generating.
- If resync was failing (e.g. GraphQL/HTTP errors on `find_scene_by_id`), organized would never have been set before; it is now set regardless of resync success.
