# Fix Studio/Performer Thumbnail Upload Issues - February 9, 2026

## Overview

Fixed three interrelated issues preventing proper thumbnail images from being uploaded to Stash studios (and performers). Studios were either getting no image, the wrong image (a video thumbnail instead of the channel avatar), or the image upload was silently skipped.

## Root Causes

### 1. Wrong thumbnail extracted from yt-dlp

`_extract_thumbnail()` used the top-level `thumbnail` field from yt-dlp info dicts, which for channel/playlist extractions often points to the **first video's thumbnail** rather than the **channel avatar/profile picture**. yt-dlp includes avatar-tagged entries (e.g. `avatar_uncropped-…`) in the `thumbnails` list, but the old code only used the list as a last-resort fallback and never checked for avatar entries.

### 2. Gap-fill skipped when studio/performer found by URL

`find_or_create_studio_by_url()` and `find_or_create_performer_by_url()` returned early when finding an entity by URL match, without running gap-fill logic. If a studio was previously created without an image (or with a broken one), subsequent syncs would find it by URL and skip the image upload entirely. The name-match path correctly ran gap-fill, but the URL-match path did not.

### 3. `image_path` check treated Stash placeholders as real images

All gap-fill and push logic checked `not stash_image` where `stash_image = entity.get("image_path")`. However, Stash returns `image_path` as a URL containing `default=true` for entities with no custom image (the auto-generated placeholder). Since this is a truthy string, the check always evaluated to `False`, and the image upload was silently skipped for entities with only a placeholder image.

## Implementation Approach

### Fix 1: Prefer avatar thumbnails

Rewrote `_extract_thumbnail()` to scan the `thumbnails` list for entries whose `id` contains `"avatar"` (case-insensitive). Picks the largest avatar (last in list, since yt-dlp sorts ascending by size). Falls back to the top-level `thumbnail` field, then the last thumbnails entry as a last resort.

### Fix 2: Gap-fill on URL match

Added `_gap_fill_*` calls to the URL-match branches of both `find_or_create_studio_by_url()` and `find_or_create_performer_by_url()`. The gap-fill functions are idempotent — they only update fields that are missing, so this is safe even if the entity already has all data.

### Fix 3: `_has_custom_image()` helper

Added a `_has_custom_image(image_path)` helper that returns `False` for `None` and for URLs containing `default=true`. Replaced all bare `not stash_image` checks with `not _has_custom_image(...)` across:
- `_gap_fill_performer_url_image()` in stash_client.py
- `_gap_fill_studio_url_image_details()` in stash_client.py
- `_push_to_stash()` in performer_sync.py
- `_push_to_stash()` in studio_sync.py
- `scrape_and_apply_performer_data()` in stash_client.py
- `_pull_performer_from_stash()` in performer_sync.py (for syncing image URL back to channel)

## Changes Made

### Files Modified

- **`app/downloader.py`** — Rewrote `_extract_thumbnail()` to prefer avatar-tagged thumbnails from yt-dlp's `thumbnails` list over the generic `thumbnail` field.
- **`app/stash_client.py`** — Added `_has_custom_image()` helper. Updated `_gap_fill_performer_url_image()` and `_gap_fill_studio_url_image_details()` to use it. Updated `find_or_create_performer_by_url()` and `find_or_create_studio_by_url()` to gap-fill when found by URL. Updated `scrape_and_apply_performer_data()` image check.
- **`app/performer_sync.py`** — Imported `_has_custom_image`. Updated `_push_to_stash()` and `_pull_performer_from_stash()` to use `_has_custom_image()` instead of bare truthiness checks.
- **`app/studio_sync.py`** — Imported `_has_custom_image`. Updated `_push_to_stash()` to use `_has_custom_image()`.

## Observations

- The three issues compounded: even if the right thumbnail was extracted, it wouldn't be uploaded because the URL-match path skipped gap-fill, and even if gap-fill ran, the placeholder check prevented the upload.
- The `_has_custom_image()` approach uses a substring check for `default=true`. This is a heuristic based on Stash's current behavior. If Stash changes how it signals placeholder images, this helper may need updating.
- The avatar thumbnail preference only helps extractors that tag thumbnails with `"avatar"` in the `id` field (YouTube does this). For other sites, the fallback to the top-level `thumbnail` field still applies.

## Testing Notes

Manual testing required: add a new channel (especially YouTube) and verify the studio image in Stash shows the channel avatar, not a video thumbnail. Also test re-syncing an existing channel whose studio has no custom image to verify gap-fill now uploads the thumbnail.
