# Jobs Page: Next Run, Schedule Column, and Reschedule UI - Feb 10, 2026

## Overview

Added a **Schedule** column and **Next Run** column to the Jobs page, fixed table column alignment, and allowed users to adjust job intervals from the UI via an inline form. Scheduler intervals for channel checker and download processor are now configurable via env vars and reschedulable at runtime.

## Implementation Approach

- Used APScheduler's `get_job(id).next_run_time` and trigger interval to drive Schedule/Next Run display.
- Introduced a registry-to-APScheduler ID map and helpers `get_job_schedule_info()` and `get_job_schedule_edit_value()` in `app/scheduler.py`.
- Added config settings `channel_check_interval_seconds` and `download_process_interval_seconds` (with min 10s) and wired `start_scheduler()` to use them.
- New `POST /jobs/{job_id}/reschedule` endpoint accepts form fields `seconds` or `hours` and calls `scheduler.reschedule_job()`; returns updated job row for HTMX.
- Jobs page context now passes `job_rows` (each with `job`, `schedule_display`, `next_run`, `schedule_edit_value`, `schedule_edit_unit`) so templates can show Schedule/Next Run and inline edit for scheduled jobs only.

## Changes Made

### Files Modified

- **app/config.py** — Added `channel_check_interval_seconds` and `download_process_interval_seconds` (Field, ge=10, le=86400).
- **app/scheduler.py** — Added `APSCHEDULER_ID_MAP`, `get_job_schedule_info()`, `get_job_schedule_edit_value()`, `reschedule_job()`; updated `start_scheduler()` to use new config.
- **app/routes/jobs.py** — Added `_job_rows_with_schedule()`, `POST /{job_id}/reschedule`; `jobs_page` and `jobs_status` now pass `job_rows`; trigger/stop/reschedule responses pass schedule and next_run (and edit value/unit) into `_job_row.html`.
- **app/templates/jobs/list.html** — Table now has Schedule and Next Run headers; added width classes and `table-fixed` for column alignment.
- **app/templates/jobs/_job_rows.html** — Iterates over `job_rows` and passes `job`, `schedule_display`, `next_run`, `schedule_edit_value`, `schedule_edit_unit` into `_job_row.html`.
- **app/templates/jobs/_job_row.html** — New Schedule and Next Run cells; Schedule shows interval and, for scheduled jobs, inline form (number input + Save) that POSTs to reschedule; Next Run shows formatted datetime or "—".
- **docs/architecture/README.md** — Documented `YTDL_CHANNEL_CHECK_INTERVAL_SECONDS` and `YTDL_DOWNLOAD_PROCESS_INTERVAL_SECONDS` in the config table.

### Files Created

- **docs/journal/2026-02-10-jobs-next-run-schedule-ui.md** — This entry.

## Trade-offs

- Reschedule changes take effect immediately but are not persisted; after app restart, intervals revert to env config. A future settings-persistence feature could store overrides.
- Next Run is shown as absolute datetime (no client-side "in 23s" countdown) to avoid JS and keep templates server-rendered.

## Testing Notes

- Load `/jobs` and confirm Schedule and Next Run columns and aligned headers.
- For Check All Channels / Process Downloads, change the number and click Save; row should refresh with new interval and next run time.
- For Check yt-dlp Updates, change hours and Save; same behavior.
- Manual-only jobs show "Manual" and "—" with no edit form.
