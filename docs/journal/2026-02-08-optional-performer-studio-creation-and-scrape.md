# Optional Performer/Studio Creation + Auto-Scrape on Channel Add - February 8, 2026

## Overview

Modified the channel creation wizard (Step 3) so that performer and studio creation in Stash is opt-in via checkboxes rather than automatic. When a performer IS created, Stash's scraper infrastructure is immediately invoked to enrich the performer with metadata, followed by a re-sync to pull that data locally.

## Implementation Approach

- Added checkboxes to Step 3 of the add-channel modal. When Stash already has a matching performer/studio, a hidden input auto-sends `create_performer=1` / `create_studio=1`. When no match exists, checkboxes (defaulting to checked) let the user opt in or out.
- The `POST /channels` endpoint now accepts `create_performer` and `create_studio` form params. Sync is conditional on these flags.
- After performer sync (find-or-create), if a performer ID exists, the channel URL is sent to Stash's `scrapePerformerURL` query. Scraped metadata is applied as gap-fill, then a re-sync pulls the enriched data back to the local cache.
- Used `htmx:configRequest` event (not `submit`) to reliably inject checkbox values into the HTMX request params.

## Changes Made

### Files Modified

- **`app/templates/channels/_add_step3.html`** — Replaced static "No match — will create on save" text with checkboxes (default checked). Added JS to copy checkbox values into the save form's HTMX params via `htmx:configRequest`. Hidden inputs ensure sync always happens when an existing match is found.
- **`app/routes/channels.py`** — Added `create_performer` and `create_studio` form params to `add_channel()`. Performer/studio sync is now conditional. Added `_scrape_and_resync_performer()` helper that scrapes the channel URL via Stash, applies scraped data, and re-syncs.
- **`app/stash_client.py`** — Added `_SCRAPE_PERFORMER_URL_QUERY` GraphQL query constant. Added `scrape_performer_url()` method (mirrors `scrape_scene_url()` pattern). Added `apply_scraped_performer()` method that gap-fills performer fields from scraped data (strings, numerics with int coercion, URLs, images).

## Challenges Encountered

- Checkboxes live outside the save form (they're in the display section above). Since HTML checkboxes only submit their value when checked, and HTMX forms use `hx-post`, we needed a reliable way to inject checked values into the request. Used `htmx:configRequest` event which is guaranteed to fire before the HTMX request and provides direct access to `evt.detail.parameters`.
- Stash's `ScrapedPerformer` GraphQL type has different field names/types than `Performer` / `PerformerUpdateInput`. Key differences: scraped uses `height` (String) while update expects `height_cm` (Int); scraped uses `weight` (String) while update expects `weight` (Int); scraped uses `gender` as a plain String while update expects `GenderEnum`. The apply logic maps these correctly with `int()` coercion.

## Trade-offs

- **Synchronous scrape during save**: The performer scrape + re-sync happens inline during the channel save request. If Stash's scraper is slow, this could delay the modal close. Acceptable because channel creation is infrequent and the enriched data is immediately visible. Could be moved to a background task if latency becomes an issue.
- **Scraper availability**: `scrapePerformerURL` only returns data if the user has a performer scraper configured in Stash that matches the channel URL's domain. Many tube sites may not have performer scrapers. The code handles this gracefully (returns None, logs info).

## Next Steps (Future Considerations)

- Consider adding a "Scrape Performer" button on the channel detail page for manual re-scraping.
- Consider background task for scrape if users report slow channel creation.
