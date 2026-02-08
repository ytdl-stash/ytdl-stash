# Stash Performer Name Is Authoritative - February 8, 2026

## Overview

When syncing a channel's performer from Stash, the channel name is now overwritten with the performer name from Stash. This makes Stash the single source of truth for performer naming.

## Implementation Approach

Added a name-overwrite step to `_pull_from_stash()` in `performer_sync.py`. After pulling the full performer record from Stash, if the performer has a non-empty name, the channel's `name` field is set to it. This runs on every pull (initial and re-pull after push), so renaming a performer in Stash will propagate to the channel on the next sync.

## Changes Made

### Files Modified

- `app/performer_sync.py` — Added name overwrite in `_pull_from_stash()` (Stash performer name → `channel.name`).
- `docs/data-flow.md` — Updated step 9 in the Performer Sync section to document the name overwrite behavior.

## Trade-offs

- **Unconditional overwrite**: If a user manually renames a channel in the UI, the next performer sync will revert it to the Stash performer name. This is intentional — Stash is authoritative.
- **Push logic unaffected**: The push step only sends a name to Stash when the Stash performer has no name, so there is no feedback loop.
