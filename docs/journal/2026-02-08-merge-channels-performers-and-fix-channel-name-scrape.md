# Merge Channels + Performers Page and Fix Channel Name Scraping - Feb 8, 2026

## Overview

Unified the redundant Channels (table) and Performers (cards) pages into a single **Channels** page at `/channels` using the card-based UI. The new page keeps all Stash sync (performer/studio), add-channel wizard, and channel parameters; channel settings (interval, max age, min duration) are editable on the channel detail page. Also fixed a bug where the initial channel scrape produced the site's display name (e.g. "Pornhub") instead of the actual channel name.

## Implementation Approach

- **Single router**: All channel list, detail, add, update, delete, sync, and check-now routes live in `app/routes/channels.py`. The old table-only routes (`/table`, `/{id}/row`, `/{id}/edit`) were removed.
- **Card UI as primary**: The list view is the former Performers card grid with filter (All / Watched / Not Watched) and sort (Name / Videos / Last checked). Add Channel button and modal, Bulk Edit, and Check All Now remain on the same page.
- **Detail page**: Channel detail shows metadata, Stash performer/studio sections, and a new **Channel Settings** collapsible section for name, check interval, max video age, min duration, Save, and Check Now.
- **Scrape fix**: Improved `_looks_like_site_name()` so values that are the site name (e.g. "Pornhub") are rejected when the extractor key is a variant (e.g. "PornHubUser") or when the value matches the URL's domain base.

## Changes Made

### Files Created

- `app/templates/channels/_list_content.html` — Filter/sort nav + channel card grid (from performers).
- `app/templates/channels/_card.html` — Single channel card with /channels URLs and channel-card- IDs.
- `app/templates/channels/_card_list.html` — Loop over channels including _card.html.
- `app/templates/channels/detail.html` — Channel detail page with back link to Channels.
- `app/templates/channels/_detail_card.html` — Detail card with Stash sections + Channel Settings (editable name, interval, max age, min duration, Check Now).

### Files Modified

- `app/routes/channels.py` — Replaced table-based list with card-based list (filter/sort); added channel detail, sync, relink, toggle; added `_channel_sync_response`; removed table/row/edit routes; bulk update now returns `_list_content.html`; add_channel HTMX returns `_card.html`; update_channel can return `_detail_card.html` when target is channel-detail-card.
- `app/templates/channels/list.html` — Now extends base, title "Channels"; Add Channel button, modal, Check All Now, Bulk Edit; content wrapper `#channels-content` with `_list_content.html`.
- `app/templates/channels/_bulk_edit.html` — Target changed to `#channels-content`; Cancel uses `hx-get="/channels"`; per-row Check Now/Delete removed (edit-only in bulk).
- `app/templates/channels/_add_step3.html` — Save form targets `#channel-grid`, swap `beforeend`; modal close handled by server HX-Trigger.
- `app/templates/base.html` — Removed Performers nav link (desktop and mobile).
- `app/main.py` — Removed `performers` router import and `app.include_router(performers.router)`.
- `app/downloader.py` — `_looks_like_site_name()`: added prefix match (extractor key starts with value), URL domain-base comparison from `webpage_url`/`original_url`/`url`; added `urllib.parse.urlparse` import. `_extract_channel_name()`: added `uploader_id` to candidate fields after `uploader`.

### Files Deleted

- `app/routes/performers.py`
- `app/templates/performers/list.html`, `_list_content.html`, `_card.html`, `_card_list.html`, `detail.html`, `_detail_card.html`
- `app/templates/channels/_table.html`, `_row.html`, `_row_edit.html`

## Channel Name Scraping Fix

yt-dlp often returns the site's display name (e.g. "Pornhub") in `channel` or `uploader` for channel URLs. The previous heuristic only treated a value as "site name" if it exactly matched the extractor key (e.g. "PornHub"); when the extractor was "PornHubUser", "Pornhub" was accepted as a channel name. Changes:

1. **Prefix match**: If the normalized extractor key starts with the normalized candidate (e.g. "pornhubuser" starts with "pornhub"), treat the candidate as a site name.
2. **URL domain base**: Parse the channel URL from the info dict and compare the second-level domain (e.g. "pornhub" from "pornhub.com") to the candidate; if they match, reject.
3. **uploader_id**: Some extractors put the real username in `uploader_id`; it is now considered when extracting the channel name.

## Documentation Updates

- `docs/architecture/README.md` — Routes and templates sections updated to single Channels page (no performers route/templates).
- `docs/data-flow.md` — No structural change; "Channels list page" and Check All Now reference remain correct.
- `docs/patterns/htmx.md` — Bulk edit Cancel and add-channel target updated to #channels-content / #channel-grid where relevant.
- `docs/patterns/ui.md` — Filter/sort example updated from Performers to Channels (#channels-content, channels/ templates).

## Testing Notes

- Channels list: filter/sort, Add Channel wizard (steps 1–3, save appends card and closes modal), Bulk Edit (Save returns card list, Cancel returns list), Check All Now.
- Channel detail: Channel Settings (edit name/interval/max age/min duration, Save; Check Now); Sync Performer, Sync Studio, Sync Both, Re-link, Delete; Stash sections and video table.
- Add a channel with a URL that previously scraped as the site name; confirm the displayed name is the real channel/user name after the fix.
