# Phase 7: API Routes

**Status**: COMPLETE

## Prerequisites

- Phase 2 complete (Channel + Video models for CRUD)
- Phase 5 recommended (pipeline for check-now and retry actions, but routes can stub these initially)

## Deliverables

- [x] `app/routes/__init__.py` — empty package init
- [x] `app/routes/dashboard.py` — `GET /`
- [x] `app/routes/channels.py` — channels CRUD
- [x] `app/routes/videos.py` — videos listing, detail, retry
- [x] `app/routes/settings.py` — settings page, Stash connectivity test
- [x] Update `app/main.py` — register all routers in `create_app()`

### Dashboard routes

| Method | Path | Purpose | Template |
|--------|------|---------|----------|
| GET | `/` | Dashboard with stats | `dashboard.html` |

Stats: total channels, total videos, pending count, failed count, recent downloads.

### Channel routes

| Method | Path | Purpose | Template/Response |
|--------|------|---------|-------------------|
| GET | `/channels` | List all channels | `channels/list.html` |
| POST | `/channels` | Add new channel (form) | Redirect or HTMX partial |
| PUT | `/channels/{id}` | Update channel | HTMX partial `_row.html` |
| DELETE | `/channels/{id}` | Delete channel + videos | Empty/HTMX |
| POST | `/channels/{id}/check-now` | Trigger immediate scan | HTMX partial |

### Video routes

| Method | Path | Purpose | Template/Response |
|--------|------|---------|-------------------|
| GET | `/videos` | List with filtering | `videos/list.html` or `_table_body.html` |
| GET | `/videos/{id}` | Video detail | `videos/detail.html` |
| POST | `/videos/{id}/retry` | Retry failed download | HTMX partial |
| DELETE | `/videos/{id}` | Remove video record | Empty/HTMX |

### Settings routes

| Method | Path | Purpose | Template/Response |
|--------|------|---------|-------------------|
| GET | `/settings` | Settings page | `settings.html` |
| POST | `/settings/test-stash` | Test Stash connectivity | HTMX partial |

## Patterns to Follow

- `docs/patterns/fastapi.md` — **READ THIS FIRST**. Router organization, dependency injection, template responses, HTMX partial detection, error handling.
- `docs/recipes/add-api-route.md` — step-by-step for adding routes and routers.
- `docs/patterns/sqlalchemy-async.md` — query patterns for list/get/create/update/delete.
- `docs/patterns/htmx.md` — detecting `HX-Request`, returning partials vs full pages.

## Key Implementation Notes

- Every route that renders a template MUST include `"request": request` in the context.
- Use `Depends(get_db)` for sessions, `Depends(get_settings)` for config — never instantiate directly.
- POST/PUT/DELETE routes: return HTMX partial if `HX-Request` header present, otherwise `RedirectResponse(status_code=303)`.
- Form data uses `Form(...)` for required fields (requires `python-multipart`).
- Channel `site` is derived from the URL (extract domain, strip "www.").
- Video list filtering: support query params `channel_id` and `status`.
- Import `templates` from `app.main` in each route module.

## Acceptance Criteria

- [x] All routers registered in `create_app()` via `include_router()`
- [x] Dashboard shows stats from DB queries
- [x] Channel CRUD works (add, list, update, delete)
- [x] "Check Now" triggers `process_channel_scan` (or stub if Phase 5 not done)
- [x] Video list supports filtering by channel and status
- [x] Video retry resets status to "pending"
- [x] Settings page shows config and Stash connectivity test
- [x] HTMX requests get partial HTML; full requests get complete pages
- [x] All routes use `Depends()` for DB and settings

## Deviations

(none yet)
