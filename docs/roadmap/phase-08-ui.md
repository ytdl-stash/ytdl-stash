# Phase 8: Web UI (Jinja2 + HTMX)

**Status**: COMPLETE

## Prerequisites

- Phase 7 complete (routes that render these templates)

## Deliverables

### Full-page templates (extend `base.html`)

- [x] `app/templates/base.html` — layout: nav, head (Pico CSS + HTMX CDN), content block
- [x] `app/templates/dashboard.html` — stats cards, recent activity
- [x] `app/templates/channels/list.html` — channel table with toggle, check-now, delete
- [x] `app/templates/channels/add.html` — add channel form (URL, name, interval)
- [x] `app/templates/videos/list.html` — video table with status badges, filtering
- [x] `app/templates/videos/detail.html` — full metadata view, Stash link, retry button
- [x] `app/templates/settings.html` — config display, Stash connection test

### HTMX partial templates (do NOT extend `base.html`)

- [x] `app/templates/channels/_row.html` — single channel table row
- [x] `app/templates/videos/_table_body.html` — video table body rows
- [x] `app/templates/videos/_status_badge.html` — status badge element

### Static assets

- [x] `app/static/style.css` — minimal custom overrides (Pico CSS does the heavy lifting)

### CSS framework

Use **Pico CSS** via CDN (no build step):
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
```

### HTMX CDN

```html
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
```

## Patterns to Follow

- `docs/patterns/htmx.md` — **READ THIS FIRST**. Toggle switch, action buttons, auto-refresh table, retry button, delete with confirm, form submission, partial template organization, Pico CSS usage.
- `docs/patterns/fastapi.md` — template response pattern, `"request": request` requirement.
- `docs/adr/005-jinja2-htmx.md` — why Jinja2 + HTMX, no JS framework.

## Key Implementation Notes

- `base.html` defines `{% block content %}` and `{% block title %}`. All full pages extend it.
- Partial templates (`_` prefix) are standalone HTML fragments — no `{% extends %}`, no `<html>`, no `<head>`.
- Pico CSS is classless: use semantic HTML (`<table>`, `<nav>`, `<article>`, `<button>`) and it looks good by default.
- HTMX indicator CSS: `.htmx-indicator { display: none; }` shown automatically during requests.
- Video list auto-refreshes via `hx-trigger="every 10s"` for live status updates.
- `hx-confirm` for destructive actions (delete channel, retry download).
- Status badges should use color-coded styles: green=synced, yellow=pending/downloading, red=failed, blue=importing.

## Acceptance Criteria

- [x] `base.html` loads Pico CSS and HTMX from CDN
- [x] Navigation bar links to Dashboard, Channels, Videos, Settings
- [x] Dashboard shows stat cards (total channels, videos, pending, failed)
- [x] Channel list has working enable/disable toggle, check-now button, delete button
- [x] Add channel form submits and appends the new row (HTMX on list; also dedicated /channels/add page)
- [x] Video list filters by channel and status
- [x] Video list auto-refreshes every 10 seconds
- [x] Video detail shows all metadata and a Stash scene link
- [x] Failed videos show retry button
- [x] Settings page shows Stash connection test result
- [x] Partial templates do NOT extend `base.html`
- [x] All templates include `{{ request }}` in their context (handled by routes)

## Deviations

- Add channel form is also inline on the list page (HTMX append); dedicated `channels/add.html` exists for GET /channels/add.
- Video list filter accepts empty string for "All" (route uses str and parses to int for channel_id).
- Delete video from detail page returns `HX-Redirect: /videos` so the client navigates after delete.
