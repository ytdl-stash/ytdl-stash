# Add Channel Modal Wizard - Feb 7, 2026

## Overview

Replaced the inline "Add Channel" form on the Channels page with a three-step modal wizard: enter URL and scrape metadata, review and edit metadata plus settings, then see Stash performer/studio link results (or "will create on save") and save. Both performer and studio are synced to Stash when the channel is created.

## Implementation Approach

- **Step 1**: Single URL input; "Scrape" POSTs to `/channels/preview`. Backend runs `async_extract_channel_metadata()`, returns Step 2 partial (or Step 1 with error message on failure).
- **Step 2**: Shows thumbnail, editable name, description, site badge, and interval/max age/min duration. "Link to Stash" POSTs to `/channels/preview/link` with all fields. Backend searches Stash for performer (by URL then name) and studio (by URL then name), returns Step 3 partial with match results or "will create on save".
- **Step 3**: Displays performer and studio match (or create message). "Save Channel" POSTs to `/channels` with all data; server accepts optional `thumbnail_url` to avoid re-scraping. On success, server returns new row and `HX-Trigger: closeAddChannelModal`; script on the page closes the dialog.
- Modal is a DaisyUI `<dialog>` included in the list page; only the inner `#add-channel-modal-body` is swapped by HTMX at each step. "Add Channel" button loads step 1 into the body then calls `showModal()`.

## Changes Made

### Files Created

- `app/templates/channels/_add_modal.html` — Dialog shell with modal-box and step 1 body included.
- `app/templates/channels/_add_step1.html` — URL input form (scrape).
- `app/templates/channels/_add_step2.html` — Metadata review + settings + "Link to Stash".
- `app/templates/channels/_add_step3.html` — Stash linking results + Back + "Save Channel".

### Files Modified

- `app/routes/channels.py` — Added `GET /add-modal`, `GET /add-modal/step2`, `POST /preview`, `POST /preview/link`; added optional `thumbnail_url` to `POST /channels` and studio sync on create; removed `GET /add`; response for HTMX add returns `HX-Trigger: closeAddChannelModal`.
- `app/templates/channels/list.html` — Replaced inline add form with "Add Channel" button and modal include; added script to listen for `closeAddChannelModal` and close the dialog.
- `docs/patterns/htmx.md` — Documented "Pattern: Modal Wizard (Add Channel)" and updated Template Organization (add modal/step partials, remove add.html).

### Files Deleted

- `app/templates/channels/add.html` — Replaced by the modal flow.

## Challenges Encountered

- Nested forms are invalid HTML; Step 3 uses two sibling forms (Back and Save) with separate submit buttons.
- Performer image in Step 3: Stash may return a relative `image_path`; template builds full URL using `stash_url` from context.

## Observations

- Preview/link step only searches Stash (find by URL/name); it does not create. Creation happens on "Save Channel" via existing `sync_channel_performer` and `sync_channel_studio`.
- Back from Step 3 to Step 2 uses `GET /add-modal/step2` with query params; description is not carried back to avoid long URLs.

## Trade-offs

- No standalone "add channel" page; modal is the only add path. Users without JS would need another entry point if we add one later.
- Re-scrape is skipped only when the modal sends `thumbnail_url`; direct POST to `/channels` (e.g. from a future API) still triggers scrape when name or thumbnail is missing.

## Next Steps (Future Considerations)

- Optional: persist channel description in DB if we want to show it in channel detail or Stash studio details.
- Optional: non-JS fallback (e.g. simple full-page form) for add channel.

## Testing Notes

- Manual: open Channels, click Add Channel, paste URL, Scrape → Step 2; edit name/settings, Link to Stash → Step 3; Save Channel → row appears, modal closes. Back from Step 2 and Step 3 returns to previous step with data. Scrape error shows Step 1 with message; Stash error on Step 3 shows warning but allows save.
