# Save Channel: Instant Feedback & Background Stash Sync - February 9, 2026

## Overview

Improved the "Save Channel" UX in the add-channel wizard. Previously, clicking Save Channel would block the UI with no feedback while Stash performer/studio sync ran synchronously. Now the button shows a spinner, disables during submission, and the modal closes almost immediately because the Stash sync runs in the background.

## Implementation Approach

Two-pronged approach:

1. **Spinner + disabled button** — Added `hx-indicator` and `hx-disabled-elt` to the Save Channel form, with an inline DaisyUI spinner inside the button. This gives immediate visual feedback that the click registered.

2. **Background Stash sync** — Moved the performer/studio sync (create, scrape, re-sync) out of the request handler and into an `asyncio.create_task` background task with its own DB session. The channel is committed to the database before spawning the task so it's visible in the new session.

## Changes Made

### Files Modified

- **`app/templates/channels/_add_step3.html`** — Added `hx-indicator="#save-channel-spinner"` and `hx-disabled-elt="find button"` to the save form. Added a `loading-spinner` span inside the button that appears during the HTMX request.

- **`app/routes/channels.py`** (`add_channel` route) — Explicit `await db.commit()` after flushing the new channel. Stash sync (performer creation, scraping, studio creation) now runs in a background `asyncio.Task` with its own `db_module.async_session()`, following the same pattern used by `check_now` and `resync_videos`. Updated docstring.

## Challenges Encountered

- **Race condition prevention**: The `get_db` dependency auto-commits after the route returns, but the background task needs the channel row to exist. Solved by adding an explicit `await db.commit()` before spawning the task.

## Trade-offs

- The channel card that appears in the grid won't show Stash performer/studio data until the background sync completes. This is acceptable because the card already renders correctly without that data, and the user can refresh or the data will appear on next page load.

## Testing Notes

- Add a channel with performer + studio creation enabled; modal should close almost instantly.
- Check server logs for `add_channel bg id=X` messages confirming background sync ran.
- Verify the spinner appears briefly in the Save Channel button during submission.
