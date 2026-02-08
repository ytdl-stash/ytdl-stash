# End-to-End Data Flow

This document describes how data flows through the entire ytdl-stash system, from channel discovery to a fully-tagged scene in Stash.

---

## High-Level Flow

```
User adds channel URL
       |
       v
  [Scheduler: channel_checker]
       |
       v
  yt-dlp scan_channel (flat extract)
       |
       v
  New videos inserted to DB (status=pending)
       |
       v
  [Scheduler: download_processor]
       |
       v
  yt-dlp download_video
       |
       v
  Compute oshash
       |
       v
  Trigger Stash metadataScan
       |
       v
  Poll Stash: find_scene_by_oshash
       |
       v
  Create/find performers and studio in Stash
       |
       v
  Update scene with metadata
       |
       v
  Video status = synced
       |
       v
  [Optional] Scrape scene URL via Stash scrapers
       |
       v
  [Optional] Trigger Stash generate (covers, phashes, etc.)
       |
       v
  Re-sync scene from Stash (verify scraper results)
```

---

## Detailed Step-by-Step

### 1. User Adds a Channel

**Trigger**: User submits a channel URL via the web UI (`POST /channels`).

**What happens**:
1. The route handler validates the URL.
2. The site is derived from the URL (e.g., `"pornhub"` from `pornhub.com`).
3. A `Channel` row is inserted into the database with `enabled=True` and `last_checked_at=None`.
4. The user is redirected to the channels list.

**Data created**:
- `channels` table row: `{name, url, site, enabled, check_interval_hours, last_checked_at=None}`

---

### 1b. Performer Sync on Channel Add

**Trigger**: Immediately after a channel is inserted in step 1 (non-blocking background task).

**What happens**:
1. Enrich from source: if the channel name is a placeholder (bare domain, "unknown", or empty), attempt to re-extract metadata from yt-dlp to get a real name and thumbnail.
2. Check if `channel.stash_performer_id` is already set. If so, skip to pull/push.
3. **Guard**: if the channel name is still a placeholder after enrichment (e.g. yt-dlp returned the site name instead of a real channel name), skip performer creation entirely. The performer will be created on a later sync once a real name is available.
4. Call `stash_client.find_performer_by_url(channel.url)` — searches Stash for a performer whose URL list includes this channel URL.
5. If found: link the existing performer by setting `channel.stash_performer_id = performer.id`.
6. If not found by URL: fall back to `stash_client.find_performer(channel.name)` — try matching by name.
7. If still not found: create a new performer in Stash with `name=channel.name`, `urls=[channel.url]`, and `image` (the thumbnail URL is downloaded and sent as a base64 data URI).
8. Set `channel.stash_performer_id` to the found or newly created performer ID.
9. Pull full performer data from Stash → store locally. The channel name is overwritten with the Stash performer name (Stash is authoritative for naming).
10. Push any source data Stash is missing (URL, image).
11. Commit to DB.

**Data flow**:
```
Channel just added (stash_performer_id=None)
    |
    v
  Enrich from yt-dlp (name, thumbnail) if missing
    |
    v
  Name still a placeholder (domain, "unknown", empty)?
   /    \
  Yes    No
  |       |
  v       v
Skip    Find performer by URL in Stash
(defer)   |
        Found?
         /    \
       Yes    No
        |       |
        v       v
      Link    Find performer by name in Stash
        |       |
        |     Found?
        |      /    \
        |    Yes     No
        |    |        |
        |    v        v
        |  Link     Create performer in Stash (name, urls, image)
        |    |        |
        |    |        v
        |    |      Link
        v    v        v
channel.stash_performer_id = performer.id
```

**Data updated**:
- `channels` table: `stash_performer_id` populated
- Stash: new performer created (if it didn't already exist)

**Error handling**: If Stash is unreachable, the channel is still usable — performer sync can be retried later via the Performer Browser UI or a scheduled job.

---

### 2. Scheduler Triggers Channel Check

**Trigger**: The `channel_checker` job runs every 60 seconds.

**What happens**:
1. Query all `Channel` rows where `enabled=True` AND (`last_checked_at IS NULL` OR `last_checked_at < now - check_interval_hours`).
2. For each due channel, run the scan pipeline (step 3).
3. Update `channel.last_checked_at = now`.

**Decision logic**:
```
For each enabled channel:
  IF last_checked_at is NULL (never checked):
    -> Run scan
  ELIF now - last_checked_at > check_interval_hours:
    -> Run scan
  ELSE:
    -> Skip (not due yet)
```

---

### 3. Channel Scan via yt-dlp

**Trigger**: Step 2 determines a channel is due for checking.

**What happens**:
1. Call `scan_channel(channel.url, settings.cookies_file)` via `asyncio.to_thread()`.
2. yt-dlp uses `extract_flat=True` to fetch the channel page and list all video entries.
3. Channel-level metadata (name, thumbnail) is extracted from the same yt-dlp response at no extra cost.
4. If the channel name is still a placeholder (bare domain like `"youtube.com"`, `"unknown"`, or empty — because initial metadata extraction failed), update it with the real channel name from yt-dlp. Similarly, back-fill the performer thumbnail if it was missing. Names that look like site/domain names (e.g. values matching the yt-dlp extractor key or bare domains) are filtered out during extraction.
5. For each entry returned by yt-dlp:
   a. Check if `site_video_id` already exists in the `videos` table.
   b. If YES: skip (already known).
   c. If NO: insert a new `Video` row with `status="pending"`.

**Data flow**:
```
yt-dlp flat extract -> {entries: [...], channel_meta: {name, thumbnail}}
                            |
                            v
                    Update channel name/thumbnail if still placeholder
                            |
                            v
                    For each video entry:
                      DB lookup: SELECT * FROM videos WHERE site_video_id = entry.id
                        |
                     Exists?
                      /     \
                    Yes      No
                    Skip     INSERT INTO videos (site_video_id, title, url, channel_id, status='pending', ...)
```

**Data created/updated**:
- `channels` table: `name` and `performer_image_url` updated if previously stuck on fallback values
- `videos` table rows: `{site_video_id, title, url, channel_id, upload_date, status="pending"}`

---

### 4. Download Processing

**Trigger**: The `download_processor` job runs every 30 seconds (with `max_instances=1` to prevent overlap).

**What happens**:
1. Query up to `settings.max_concurrent_downloads` `Video` row(s) with `status="pending"`, ordered by `created_at ASC` (FIFO).
2. If none found, exit.
3. For each picked video, set `video.status = "downloading"`.
4. **Pre-download metadata check**: If `channel.min_duration_seconds` or `channel.max_video_age_days` is set and the corresponding field (`duration_seconds` or `upload_date`) is unknown (flat scan often omits them), extract full metadata via `extract_video_info()` (no download). If duration is now known and below the threshold, or upload_date is known and older than max age, set `video.status = "skipped"` and skip the download entirely.
5. Call `download_video(video.url, settings.download_dir, settings.ytdlp_output_template, settings.cookies_file)` via `asyncio.to_thread()` (each download runs in a worker thread).
6. While downloading, yt-dlp `progress_hooks` update an **in-memory** progress store (percent/ETA/speed) so the web UI can render a progress bar.
7. If the user clicks **Stop** while a video is pending or in-flight:
   - `pending` videos are marked `status="cancelled"` immediately.
   - In-flight videos are marked `status="cancelling"` and a cooperative cancel flag is set; the yt-dlp hook aborts the download and the pipeline finalizes with `status="cancelled"`.
8. On success:
   a. Save `filepath`, `filename`, `performers`, `studio`, `duration`, `thumbnail_url`, `metadata_json` to the video row.
   b. **Post-download filter safety nets**: If `channel.min_duration_seconds` is set and the just-downloaded video is too short (duration was unknown until now), or if `channel.max_video_age_days` is set and the video is older than the threshold, delete the file and set `video.status = "skipped"`.
   c. Otherwise, set `video.status = "downloaded"`.
9. On failure:
   a. Set `video.status = "failed"`, `video.error_message = str(error)`.
10. Respect `settings.download_delay_seconds` as a throttle:
   - With `max_concurrent_downloads=1`, it is applied **between** downloads.
   - With `max_concurrent_downloads>1`, starts are **staggered** by this delay to reduce burstiness.

**Data flow**:
```
Video (status=pending)
    |
    v
status = downloading
    |
    v
min_duration or max_age set & (duration or upload_date) unknown?
   /     \
  Yes     No
  |        |
  v        |
extract_video_info() (metadata only, no download)
  |        |
  v        |
duration < min? OR upload_date older than max_age?
   /  \    |
  Yes  No  |
  |    |   |
  v    +---+
status=    |
skipped    v
        yt-dlp download_video() -> {filepath, ...}
            |
            v
        duration < min? OR upload_date older than max_age? (safety net)
           /     \
          Yes     No
          |        |
          v        v
        Delete   Save metadata to video row
        file     status = downloaded
        status=
        skipped
```

---

### 5. oshash Computation

**Trigger**: Immediately after a successful download (within the same pipeline run).

**What happens**:
1. Call `compute_oshash(video.filepath)`.
2. The function reads the first 64KB and last 64KB of the file, computes the OpenSubtitles hash.
3. Save `video.oshash = hash_value`.

**Data flow**:
```
Downloaded file on disk
    |
    v
Read first 64KB + last 64KB + file size
    |
    v
Compute hash -> 16-char hex string (e.g., "a1b2c3d4e5f67890")
    |
    v
video.oshash = "a1b2c3d4e5f67890"
```

---

### 6. Trigger Stash Metadata Scan

**Trigger**: Immediately after oshash computation (or after early scene lookup and file-existence check when retrying).

**What happens**:
1. Set `video.status = "importing"`.
2. Call `stash_client.trigger_scan(paths=[video.filepath])` — returns Stash job ID.
3. Call `stash_client.wait_for_job(scan_job_id)` — polls `findJob` until FINISHED/FAILED.
4. Look up scene by `find_scene_by_oshash`, fallback to `find_scene_by_title`.

**Important**: The file path passed to Stash must be the path **as Stash sees it**, not as ytdl-stash sees it. Since both containers mount the same host directory, the paths should align:
- ytdl-stash writes to: `/downloads/SomeUser - Video Title [abc123].mp4`
- Stash sees it at: `/data/downloads/SomeUser - Video Title [abc123].mp4` (or wherever the host directory is mounted in Stash)

If the mount points differ, a path translation may be needed.

**Early scene lookup** (before download): If `video.oshash` or `video.title` is set (e.g. from retry), try `find_scene_by_oshash` then `find_scene_by_title`. If found, skip download and scan, jump to metadata update.

---

### 7. Find Scene After Scan

**Trigger**: After `wait_for_job(scan_job_id)` completes (scan job FINISHED).

**What happens**:
1. Call `find_scene_by_oshash(video.oshash)`.
2. If not found, fallback to `find_scene_by_title(video.title)`.
3. If still not found, set `video.status = "failed"` with error message.

---

### 8. Create/Find Performers and Studio in Stash

**Trigger**: Scene found in step 7.

**What happens**:
1. Load `video.channel` (async relationship refresh) and query all channels to build a case-insensitive name → channel lookup.
2. For each performer name in `video.performers`:
   a. Normalize the name (whitespace collapsed).
   b. **If the performer name matches a known channel** (by normalized name): call `stash_client.find_or_create_performer_by_url(name, channel.url, channel.performer_image_url)`. This finds by URL first, then by name/alias; if found by name/alias but the performer lacks the channel URL, it gap-fills the URL and image.
   c. **Otherwise**: call `stash_client.find_or_create_performer(name)` (name-only lookup/create).
   d. Collect all performer IDs.
3. **Studio** (opt-in, channel-derived when linked):
   a. If `channel.stash_studio_id` is set (user has clicked "Sync Studio" on the performer), use it as the scene's studio ID.
   b. Otherwise, no studio is associated with the scene.

**Data flow**:
```
video.performers = ["Performer A", "Performer B"]
channels = [{name: "Performer A", url: "https://...", performer_image_url: "..."}, ...]
    |
    v
Build channel_by_name lookup (normalized name -> channel)
    |
    v
For "Performer A":
  Matches known channel?
    Yes -> find_or_create_performer_by_url(name, channel.url, channel.performer_image_url)
           -> find by URL? -> use existing ID
           -> find by name/alias? -> gap-fill URL/image if missing -> use ID
           -> not found? -> performerCreate(name, urls, image) -> new ID
    No  -> find_or_create_performer(name)
           -> find by name/alias? -> use existing ID
           -> not found? -> performerCreate(name) -> new ID
For "Performer B": (same)
    |
    v
Studio (from video.channel, opt-in):
  studio_id = channel.stash_studio_id if set, else None
    |
    v
performer_ids = ["1", "2"]
studio_id = "5"
```

---

### 9. Update Scene Metadata

**Trigger**: Performers and studio resolved in step 8.

**What happens**:
1. Call `stash_client.update_scene()` with:
   - `scene_id`: from step 7
   - `title`: `video.title`
   - `urls`: `[video.url]`
   - `date`: `video.upload_date` formatted as `"YYYY-MM-DD"`
   - `studio_id`: from step 8
   - `performer_ids`: from step 8
2. Set `video.stash_scene_id = scene.id`.
3. Set `video.status = "synced"`.

**Data flow**:
```
GraphQL: sceneUpdate(input: {
    id: "123",
    title: "Video Title",
    urls: ["https://site.com/video/abc123"],
    date: "2025-01-15",
    studio_id: "5",
    performer_ids: ["1", "2"]
})
    |
    v
video.stash_scene_id = "123"
video.status = "synced"
```

---

### 10. Post-Sync: Scrape Scene URL (Optional)

**Trigger**: Scene synced in step 9, and `YTDL_STASH_SCRAPE_AFTER_SYNC=true`.

**What happens**:
1. Call `stash_client.scrape_scene_url(video.url)` — uses Stash's configured scrapers to fetch metadata from the video's source URL.
2. If a scraper returns data, apply gap-fill fields to the scene:
   - `details` (description) — always applied since yt-dlp doesn't provide this.
   - `tags` — each tag is resolved via find-or-create, then added to the scene.
   - `cover_image` — the scraped cover image URL.
   - `performers` / `studio` — only applied if we didn't already set them from the channel sync.
3. If no scraper matches the URL, or the scraper returns empty data, the step is silently skipped.

**Error handling**: Best-effort. Failures are logged as warnings but do not change the video's `synced` status.

---

### 10b. Post-Sync: Trigger Generate (Optional)

**Trigger**: Scene synced in step 9, and `YTDL_STASH_GENERATE_AFTER_SYNC=true`. Runs **before** organized.

**What happens**:
1. Call `stash_client.trigger_generate(scene_ids=[scene.id])` — returns job ID.
2. Call `stash_client.wait_for_job(generate_job_id)` — polls until generate completes.
3. File is still at its original location during generate; no race with file-move.

**Error handling**: Best-effort. Failures are logged as warnings but do not change the video's `synced` status.

---

### 10c. Post-Sync: Mark Scene Organized (First Download Only)

**Trigger**: After generate completes (inside `run_scrape_and_generate` with `set_organized=True`).

**What happens**:
1. Call `stash_client.update_scene(scene_id, organized=True)`.
2. Any Stash file-move rule triggered by organized runs **after** generate is done.

**Why this ordering**: Generate runs first and waits for completion. Organized is set last. No settle delay needed.

---

### 11. Post-Sync: Re-sync Scene from Stash

**Trigger**: After scraping and/or generating (step 10/11), or manually via the "Re-sync" button on the Videos page.

**What happens (automatic — after scraper runs)**:
1. Call `stash_client.find_scene_by_id(video.stash_scene_id)` to fetch the latest scene data from Stash.
2. Log the scene's current state (title, performer count, tag count) to confirm scraper results were applied.
3. Thumbnails are served dynamically from Stash at `{stash_url}/scene/{id}/screenshot`, so no local field update is needed.

**What happens (manual — via "Re-sync from Stash" button)**:
1. `POST /videos/{id}/resync` verifies the scene exists in Stash.
2. Re-scrapes the video URL via Stash's scrapers and applies any new/updated metadata.
3. Triggers Stash generate and waits for job completion (if enabled in settings).
4. The Stash screenshot thumbnail in the UI automatically reflects any cover image changes.

**Manual re-sync always scrapes** regardless of the `YTDL_STASH_SCRAPE_AFTER_SYNC` setting, since it's an explicit user action.

**Error handling**: Best-effort. Failures are logged as warnings but do not change the video's `synced` status.

---

## Thumbnails

The videos page displays scene thumbnails from Stash for synced videos. The thumbnail URL is constructed as `{stash_url}/scene/{scene_id}/screenshot` (with `?apikey={key}` appended when Stash API authentication is enabled). This URL always serves the latest cover/screenshot from Stash. For videos that are not yet synced, the yt-dlp thumbnail URL (if available) is shown as a fallback.

---

## Error Handling Summary

| Step | Error | Handling |
|------|-------|----------|
| 3 - Scan | yt-dlp extraction fails | Log error, skip channel this cycle |
| 4 - Download | Video shorter than `min_duration_seconds` or older than `max_video_age_days` | `status=skipped`, `error_message` explains threshold. File deleted if already downloaded. Retryable. |
| 4 - Download | yt-dlp download fails | `status=failed`, `error_message` saved |
| 4 - Download | user stop requested | `status=cancelled`, `error_message="Cancelled by user"` |
| 5 - oshash | File read error | `status=failed`, `error_message` saved |
| 6 - Stash scan | Stash unreachable | `status=failed`, `error_message` saved |
| 7 - Find scene | Scene not found after scan job | `status=failed`, retry possible |
| 8 - Performers | Stash API error | `status=failed`, `error_message` saved |
| 9 - Update | Stash API error | `status=failed`, `error_message` saved |
| 10 - Scrape | No scraper / scrape error | Logged as warning, video stays `synced`; `scrape_attempted_at` not set so Backfill job can retry |
| 10b - Generate | Stash API error | Logged as warning, video stays `synced`; `generate_triggered_at` not set so Backfill job can retry |
| 11 - Re-sync | Stash scene not found | Logged as warning (auto), or HTTP 404 (manual) |

All failures in steps 1–9 result in `status=failed` with the error message saved to the database. The user can **Retry** (import-only when possible) or **Redownload** (force fresh download). Steps 10–10c are best-effort and never change the video status.

---

## Retry and Redownload

**Retry** (import-only when possible):
1. `POST /videos/{id}/retry` sets `video.status = "downloaded"` when oshash/filename exists, else `"pending"`.
2. The download processor picks up both `pending` and `downloaded` (no scene) videos.
3. Pipeline runs early scene lookup; if found, skips download. Otherwise uses file-existence fast-path or downloads.

**Redownload** (force fresh download):
1. `POST /videos/{id}/redownload` clears `original_filename`, `oshash`, `stash_scene_id`; sets `status = "pending"`.
2. Deletes existing file if present. Pipeline downloads from scratch.

---

## Jobs Page & Manual Triggers

The **Jobs** page (`/jobs`) provides a central control panel for all background operations. Each job shows its current status (idle/running), last run time, and duration. The page auto-refreshes via HTMX polling every 3 seconds.

If a job is running, a **Stop** button is available:
- For most jobs, this cancels the running task (best-effort).
- For **Process Downloads**, Stop requests cooperative cancellation of any active video downloads (so the yt-dlp worker thread(s) can abort cleanly).

### Available Jobs

| Job | Endpoint | Description |
|-----|----------|-------------|
| **Check All Channels** | `POST /jobs/check_all_channels/trigger` | Scans all enabled channels that are due for new videos (same as the scheduled `channel_checker`). |
| **Process Downloads** | `POST /jobs/process_downloads/trigger` | Downloads and imports pending or downloaded (import-retry) videos in the queue (up to configured concurrency, same as the scheduled `download_processor`). |
| **Retry All Failed** | `POST /jobs/retry_all_failed/trigger` | Resets failed videos: to `downloaded` (import-only) when oshash/filename exists, else to `pending`. |
| **Backfill Scrape & Generate** | `POST /jobs/backfill_scrape_generate/trigger` | Runs scrape and generate for synced videos missing `scrape_attempted_at` or `generate_triggered_at` (bulk backfill or retry for failed post-sync steps). Only runs the step(s) that are actually missing per video. |
| **Regenerate All** | `POST /jobs/regenerate_all/trigger` | Resets `generate_triggered_at` on all synced videos and re-triggers Stash generate for each (skips scrape). Useful after a bug that caused generate to silently fail. |

### Contextual Triggers

In addition to the Jobs page, trigger buttons appear in context:

- **"Check All Now"** button on the **Channels** list page — triggers the Check All Channels job.
- **"Retry All Failed"** button on the **Videos** list page — triggers the Retry All Failed job.
- **"Re-sync"** / **"Re-sync from Stash"** button on the **Videos** list and detail pages — re-scrapes and re-generates a synced scene (only shown for videos with a `stash_scene_id`).
- **"Retry"** and **"Redownload"** buttons on failed/cancelled/skipped videos — Retry tries import-only when possible; Redownload forces a fresh download.

### Job Tracking

Jobs are tracked via a `JobInfo` registry in `app/scheduler.py`. Each entry stores:
- `running` flag (protected by an `asyncio.Lock` to prevent concurrent execution)
- `last_run_at` timestamp
- `last_duration_seconds`

When a job is triggered (manually or by the scheduler), it acquires the lock, sets `running=True`, runs, and then records timing data. If the lock is already held, the trigger is skipped to prevent overlap.
