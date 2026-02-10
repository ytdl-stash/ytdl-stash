# Global Pause for Downloads & Channel Scans - February 10, 2026

## Overview

Added a global pause/resume mechanism for both downloads and channel scans. When downloads are paused, all active downloads are cancelled immediately (hard pause) and no new downloads start. When channel scans are paused, the scheduler skips channel checking. Both states persist across restarts via a new `app_state` DB table.

## Implementation Approach

- **In-memory + DB persistence**: `DownloadControl` holds boolean pause flags for fast scheduler checks. An `AppState` key-value table persists the flags so they survive restarts.
- **Hard pause for downloads**: When pausing, all active downloads are cooperatively cancelled via the existing `request_cancel` mechanism, and their status is set to "cancelling".
- **Separate controls**: Downloads and channel scans have independent pause/resume buttons, so the user can pause one without the other.
- **HTMX event propagation**: Pause/resume endpoints return an `HX-Trigger: pauseStateChanged` header. Both the global banner and the toggle buttons listen for this event to self-refresh, keeping all UI elements in sync.

## Changes Made

### Files Created

- `app/templates/components/_pause_toggle.html` — Reusable pause/resume toggle button partial (HTMX-driven, self-refreshes on `pauseStateChanged` event).
- `app/templates/components/_pause_banner.html` — Global warning banner shown on every page when downloads or scans are paused. Includes quick resume buttons. Self-refreshes via HTMX event.
- `docs/journal/2026-02-10-global-pause-downloads-channels.md` — This journal entry.

### Files Modified

- `app/models.py` — Added `AppState` model (key-value table for persistent app state).
- `app/download_control.py` — Added `_downloads_paused` / `_channels_paused` flags with getters/setters. Added `load_pause_state_from_db()` and `persist_pause_state()` helpers with upsert logic.
- `app/main.py` — Registered `is_downloads_paused` and `is_channels_paused` as Jinja2 globals. Calls `load_pause_state_from_db()` at startup.
- `app/scheduler.py` — Added early-return guards in `_do_process_downloads()` and `_do_check_all_channels()` when paused.
- `app/routes/jobs.py` — Added 6 new endpoints: `GET /pause-banner`, `GET /pause-toggle/{key}`, `POST /downloads/pause`, `POST /downloads/resume`, `POST /channels/pause`, `POST /channels/resume`. Added pause state to jobs page context.
- `app/routes/videos.py` — Added `downloads_paused` to videos page context.
- `app/templates/base.html` — Includes the global pause banner partial.
- `app/templates/jobs/list.html` — Added both pause toggle buttons (Downloads, Channel Scans) to the page header.
- `app/templates/videos/list.html` — Added the Downloads pause toggle button next to "Retry All Failed".

## Challenges Encountered

- **HTMX event propagation across components**: The banner (in `base.html`) and the toggle buttons (in page content) are separate HTMX-managed elements. Used the `pauseStateChanged` custom event via `HX-Trigger` response header so all components self-refresh when any one of them changes state.
- **Banner resume buttons**: Since the banner exists on every page, its resume buttons can't target page-specific toggle elements. Solved by using `hx-swap="none"` on banner buttons and relying on the event-driven refresh cycle.

## Trade-offs

- **In-memory + DB hybrid**: The scheduler checks in-memory flags (fast, no I/O), while persistence is only written on user action. A restart between setting the flag and the DB write could lose state, but this window is negligible.
- **Double toggle swap**: When clicking a toggle button, the POST response swaps it immediately AND the `pauseStateChanged` event triggers a second GET refresh. Both return identical HTML, so it's imperceptible but slightly wasteful. Kept for simplicity.
- **SQLite upsert**: Used SELECT + INSERT/UPDATE with IntegrityError fallback instead of SQLAlchemy dialect-specific upsert, for portability.

## Next Steps (Future Considerations)

- Could add a "Pause All" single button that pauses both downloads and channel scans at once.
- Could show pause state on the dashboard page summary cards.
- Could add a timed auto-resume (e.g., "pause for 1 hour").
