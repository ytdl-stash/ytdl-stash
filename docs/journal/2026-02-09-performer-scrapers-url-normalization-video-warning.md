# Performer Scrapers, URL Normalization, and No-Videos Warning - February 9, 2026

## Overview

Three related improvements: (1) Stash-compatible performer scrapers for xHamster and xVideos so that adding a channel can enrich Stash performers via `scrapePerformerURL`; (2) channel URL normalization so PornHub model/pornstar URLs are rewritten to `/videos` and yt-dlp finds videos with `extract_flat=True`; (3) a "No videos found" warning in the add-channel wizard when the preview returns zero entries.

## Implementation Approach

- **Scrapers**: Added two YAML files in a new `scrapers/` directory that define only `performerByURL` (XPath-based). They are intended to be installed in the user's Stash scrapers directory alongside the community scene scrapers. No app code changes were required for scraping; the existing `scrape_performer_url` / `apply_scraped_performer` / `_scrape_and_resync_performer` flow already uses Stash's GraphQL API.
- **URL normalization**: Introduced `normalize_channel_url()` in the downloader to rewrite PornHub `/model/X`, `/pornstar/X`, `/channels/X`, `/users/X` URLs to `.../X/videos` before calling yt-dlp. Applied in the downloader at the start of `extract_channel_metadata()` and `scan_channel()`, and in the channels routes for preview, preview/link, and add_channel (with `.strip()` on add_channel) so the stored URL is normalized.
- **Video count warning**: `extract_channel_metadata()` now consumes the flat-extraction entries (inside the ydl context), flattens them, and returns a `video_count`. The add-channel preview passes `video_count` to Step 2; the template shows an alert when `video_count == 0` suggesting the user try appending `/videos` to the URL.

## Changes Made

### Files Created

- **`scrapers/Xhamster-Performer.yml`** — Performer-by-URL scraper for `xhamster.com/creators/` and `xhamster.com/pornstars/` (Name, Image, URLs, Gender, Country, Aliases, Details).
- **`scrapers/Xvideos-Performer.yml`** — Performer-by-URL scraper for xVideos channel URL patterns (pornstar-channels, model-channels, channels, amateur-channels; xvideos.com and xvideos2.com).
- **`scrapers/README.md`** — Installation instructions (copy to Stash scrapers dir or mount via Docker) and note on coexisting with community scrapers.

### Files Modified

- **`app/downloader.py`** — Added `normalize_channel_url()` (PornHub rewrite). Applied at top of `extract_channel_metadata()` and `scan_channel()`. In `extract_channel_metadata()`, after flat extraction, consume entries via `_flatten_entries()`, set `video_count = len(flat_entries)`, and include `video_count` in the return dict (and in the early return when `info` is None).
- **`app/routes/channels.py`** — Import `normalize_channel_url`. In `channel_preview`, `channel_preview_link`, and `add_channel`, normalize (and strip) the URL before use. In `channel_preview`, read `video_count` from meta and pass it to the Step 2 template.
- **`app/templates/channels/_add_step2.html`** — Warning banner when `video_count is defined and video_count == 0` with text suggesting appending `/videos` for profile pages.

### Files Deleted

- None.

## Challenges Encountered

- Entries from yt-dlp can be lazy generators; counting must happen inside the `with yt_dlp.YoutubeDL(opts) as ydl` block so the context is still alive when consuming entries.
- Stash performer Gender must be one of the allowed enum values (e.g. `female`, `male`); the xHamster scraper uses postProcess map to normalize.

## Observations

- The community Xhamster.yml and Xvideos.yml only provide scene scrapers; these new files are additive and do not replace them.
- PornHub's extractor internally redirects `/model/X` to `/model/X/videos`, but with `extract_flat=True` that redirect is not followed, so normalizing the URL in the app fixes the "no videos found" case without changing yt-dlp options.

## Trade-offs

- URL normalization is currently PornHub-only; other sites (e.g. xHamster, xVideos) can be added later with the same pattern.
- The "no videos found" warning is a safety net; with PornHub normalization many users will never see it for that site, but it helps for unsupported URL shapes or other sites.

## Next Steps (Future Considerations)

- Consider contributing `performerByURL` sections upstream to stashapp/CommunityScrapers so the main Xhamster.yml and Xvideos.yml include performer scraping.
- Add normalization rules for other sites if similar profile-vs-videos URL issues appear.

## Testing Notes

- Manually verify: paste a PornHub model URL without `/videos`, confirm preview shows metadata and non-zero video count after normalization.
- With scrapers installed in Stash, add an xHamster or xVideos channel and confirm performer is created and scraped data appears after save.
