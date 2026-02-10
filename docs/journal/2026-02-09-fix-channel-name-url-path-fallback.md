# Fix Channel Name Scrape: URL Path Fallback - February 9, 2026

## Overview

Fixed a bug where adding a PornHub channel (and potentially other sites) produced the site domain name (e.g. "pornhub.com") instead of the actual channel/creator name. The root cause was that PornHub's `PornHubPagedVideoList` extractor returns `None` for all name-related fields (`channel`, `uploader`, `uploader_id`, `title`, `playlist_title`) at the playlist level, so every candidate was empty and the code fell through to the site-domain fallback.

## Implementation Approach

Three layered fixes, each addressing a different failure mode:

1. **URL path slug extraction** (`_extract_name_from_url`): New last-resort fallback in `_extract_channel_name` that parses the channel/creator name from the URL path. Recognizes common path prefixes like `/model/`, `/pornstar/`, `/channels/`, `/creators/`, etc. and extracts the slug that follows. Cleans up hyphens/underscores and title-cases all-lowercase slugs while preserving mixed-case originals (e.g. "hottiestwo" → "Hottiestwo", "HottiesTwo" stays as-is).

2. **Non-flat fallback with one video entry**: Changed `playlistend` from `0` to `1` in the non-flat metadata fallback. With `playlistend=0` no video entries were processed, so extractors that only populate `channel`/`uploader` from video-level metadata had nothing to work with. Processing one entry gives the extractor a chance to populate those fields.

3. **First video entry name extraction**: After the non-flat playlist-level extraction, if the name is still empty, the code now checks the first video entry's metadata for `channel`/`uploader` fields. Video-level metadata often has the real uploader name even when the playlist wrapper doesn't.

The fallback priority is now:
1. yt-dlp metadata fields from flat extraction (fastest)
2. yt-dlp metadata fields from non-flat extraction (playlist level)
3. yt-dlp metadata fields from the first video entry (video level)
4. URL path slug parsing (always available, no network call)
5. Site domain name (final fallback in the route, unchanged)

## Changes Made

### Files Created

- `docs/journal/2026-02-09-fix-channel-name-url-path-fallback.md` — This journal entry.

### Files Modified

- **`app/downloader.py`**:
  - Added `unquote` to `urllib.parse` imports (for URL-encoded slugs).
  - Added `_extract_name_from_url(info)` — parses channel name from URL path using a set of known path prefixes.
  - Updated `_extract_channel_name(info)` — calls `_extract_name_from_url` as a last resort before returning empty string.
  - Updated `extract_channel_metadata()` — changed non-flat fallback from `playlistend=0` to `playlistend=1`; entries are now consumed inside the `with ydl` context to avoid lazy-generator issues; added first-video-entry name extraction loop; updated docstring.

### Files Deleted

- None.

## Challenges Encountered

- PornHub's `PornHubPagedVideoList` extractor returns `None` for all name-related fields at the playlist level — confirmed via `yt-dlp --dump-single-json`. The only reliable source of the channel name is the URL path itself.
- yt-dlp entries can be lazy generators that must be consumed inside the `with YoutubeDL() as ydl` context manager. The non-flat entry consumption was moved inside the `with` block to prevent stale-generator issues.

## Observations

- The URL path fallback is site-agnostic: any site using `/<type>/<name>/...` patterns will benefit without needing site-specific code.
- The `playlistend=1` change adds a small amount of time to the fallback (one video metadata fetch) but only runs when flat extraction fails to find a name or thumbnail.

## Trade-offs

- URL slugs may not match the creator's preferred display name exactly (e.g. "hottiestwo" vs "HottiesTwo"). The title-casing heuristic helps but isn't perfect. Users can always edit the name in the add-channel wizard's Step 2.
- Processing one video entry in the non-flat fallback is slightly slower than `playlistend=0`, but the improved name accuracy is worth it.

## Testing Notes

- Add a PornHub model channel (e.g. `https://www.pornhub.com/model/hottiestwo`): confirm the preview shows the channel name from the URL slug instead of "pornhub.com".
- Add a channel where yt-dlp does populate `channel`/`uploader` correctly (e.g. YouTube): confirm the existing field-based extraction still takes priority over the URL fallback.
- Add a channel with a hyphenated URL slug: confirm hyphens are replaced with spaces and the name is title-cased.
