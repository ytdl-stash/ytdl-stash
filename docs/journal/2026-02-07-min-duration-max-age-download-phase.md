# Min Duration / Max Age Download Phase Fix - Feb 7, 2026

## Overview

The downloader was not respecting `min_duration_seconds` or `max_video_age_days` channel settings for videos whose metadata was missing during the flat channel scan. Flat extract often omits `duration` and `upload_date`, so those videos bypassed the scan filters and were downloaded anyway. Max age had no download-phase check at all.

## Implementation Approach

1. **Pre-download metadata check**: Extract full metadata (no download) when either filter is set and the corresponding field is unknown. Populate both `duration_seconds` and `upload_date` from a single extraction call when needed.
2. **Pre-download filter application**: Apply both min duration and max age checks after metadata extraction (or if metadata was already available from scan).
3. **Post-download safety nets**: Add max age to the existing post-download safety net (alongside duration). If upload_date becomes known only after download and the video is too old, delete the file and mark skipped.

## Changes Made

### Files Modified

- `app/pipeline.py`: Consolidated pre-download metadata extraction for both filters; added pre-download max age check; added post-download max age safety net; refactored skip logic into `_skip_after_download()` helper.
- `docs/data-flow.md`: Updated download processing steps, data flow diagram, and error handling table to document max age pre/post checks.
- `docs/architecture/README.md`: Updated skipped status description to mention max age.

### Files Created

- `docs/journal/2026-02-07-min-duration-max-age-download-phase.md`

## Observations

- Flat extract behavior is site-dependent; some extractors return duration/upload_date, others do not. The download-phase checks ensure filters are enforced regardless.
- A single `extract_video_info()` call populates both duration and upload_date when either is needed, avoiding redundant metadata fetches.
