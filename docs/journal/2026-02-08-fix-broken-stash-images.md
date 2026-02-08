# Fix Broken Studio/Performer Images in Stash - February 8, 2026

## Overview

Studio and performer images appeared broken in Stash after creation. The root cause was that raw image URLs (from yt-dlp thumbnails) were being passed directly to Stash's GraphQL `image` field. While Stash's schema says the field accepts "a URL or a base64 encoded data URL," in practice many source URLs (especially YouTube/platform CDN thumbnails) are ephemeral, require cookies, or are otherwise inaccessible to Stash's server-side fetcher, resulting in broken images.

## Implementation Approach

Added a helper function `_url_to_data_uri()` that downloads the image and converts it to a base64 data URI (`data:<mime>;base64,...`) before sending to Stash. This ensures the image data is always delivered inline, regardless of URL accessibility restrictions.

The fix applies to all code paths that send images to Stash:
- Studio creation (`create_studio_with_metadata`)
- Studio gap-fill updates (`_gap_fill_studio_url_image_details`)
- Studio push from sync (`studio_sync._push_to_stash`)
- Performer creation (`create_performer_with_metadata`)
- Performer gap-fill updates (`_gap_fill_performer_url_image`)
- Performer push from sync (`performer_sync._push_to_stash`)

## Changes Made

### Files Modified

- **`app/stash_client.py`** — Added `base64` import, `_url_to_data_uri()` helper function, and updated `create_studio_with_metadata`, `_gap_fill_studio_url_image_details`, `create_performer_with_metadata`, and `_gap_fill_performer_url_image` to convert image URLs to data URIs before sending.
- **`app/studio_sync.py`** — Updated `_push_to_stash` to convert image URL to data URI.
- **`app/performer_sync.py`** — Updated `_push_to_stash` to convert image URL to data URI.

## Trade-offs

- **Increased memory/bandwidth**: Images are now downloaded by ytdl-stash before being sent as base64 to Stash, roughly doubling the transfer. Mitigated by a 10 MB size cap.
- **Graceful degradation**: If the image download fails, the image is silently skipped (logged as warning) rather than sending a broken URL or failing the entire sync.
- **Separate HTTP client**: `_url_to_data_uri` creates its own short-lived httpx client (15s timeout, follows redirects) rather than reusing the Stash GraphQL client, since the image URL points to an external host.

## Testing Notes

Manual testing required: add a new channel and verify the studio/performer images display correctly in Stash after sync.
