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
1. Check if `channel.stash_performer_id` is already set. If so, skip.
2. Call `stash_client.find_performer_by_url(channel.url)` — searches Stash for a performer whose URL list includes this channel URL.
3. If found: link the existing performer by setting `channel.stash_performer_id = performer.id`.
4. If not found by URL: fall back to `stash_client.find_performer(channel.name)` — try matching by name.
5. If still not found: create a new performer in Stash with `name=channel.name`, `urls=[channel.url]`, and `image=channel.performer_image_url` (if available).
6. Set `channel.stash_performer_id` to the found or newly created performer ID.
7. Commit to DB.

**Data flow**:
```
Channel just added (stash_performer_id=None)
    |
    v
  Find performer by URL in Stash
    |
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
4. If the channel name is still the domain fallback (e.g. `"youtube.com"` because initial metadata extraction failed), update it with the real channel name from yt-dlp. Similarly, back-fill the performer thumbnail if it was missing.
5. For each entry returned by yt-dlp:
   a. Check if `site_video_id` already exists in the `videos` table.
   b. If YES: skip (already known).
   c. If NO: insert a new `Video` row with `status="pending"`.

**Data flow**:
```
yt-dlp flat extract -> {entries: [...], channel_meta: {name, thumbnail}}
                            |
                            v
                    Update channel name/thumbnail if still domain fallback
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
1. Query one `Video` row with `status="pending"`, ordered by `created_at ASC` (FIFO).
2. If none found, exit.
3. Set `video.status = "downloading"`.
4. Call `download_video(video.url, settings.download_dir, settings.ytdlp_output_template, settings.cookies_file)` via `asyncio.to_thread()`.
5. On success:
   a. Save `filepath`, `filename`, `performers`, `studio`, `duration`, `thumbnail_url`, `metadata_json` to the video row.
   b. Set `video.status = "downloaded"`.
6. On failure:
   a. Set `video.status = "failed"`, `video.error_message = str(error)`.
7. Wait `settings.download_delay_seconds` before the next iteration.

**Data flow**:
```
Video (status=pending)
    |
    v
status = downloading
    |
    v
yt-dlp download_video() -> {filepath, filename, title, upload_date, performers, studio, duration, thumbnail_url, metadata_json}
    |
    v
  Success?
   /     \
  No      Yes
  |        |
  v        v
status=   Save metadata to video row
failed    status = downloaded
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

**Trigger**: Immediately after oshash computation.

**What happens**:
1. Set `video.status = "importing"`.
2. Call `stash_client.trigger_scan(paths=[video.filepath])`.
3. This sends a GraphQL `metadataScan` mutation to Stash.
4. Stash begins scanning the file in the background (creates a scene, computes fingerprints).

**Important**: The file path passed to Stash must be the path **as Stash sees it**, not as ytdl-stash sees it. Since both containers mount the same host directory, the paths should align:
- ytdl-stash writes to: `/downloads/SomeUser - Video Title [abc123].mp4`
- Stash sees it at: `/data/downloads/SomeUser - Video Title [abc123].mp4` (or wherever the host directory is mounted in Stash)

If the mount points differ, a path translation may be needed.

---

### 7. Poll for Scene in Stash

**Trigger**: Immediately after triggering the scan.

**What happens**:
1. Call `stash_client.wait_for_scene(video.oshash, timeout=30, interval=2)`.
2. This polls `findScenes` with the oshash filter every 2 seconds for up to 30 seconds.
3. When a scene is found, return the scene dict (contains `scene.id`).
4. If timeout expires with no scene found, set `video.status = "failed"` with an error message.

**Data flow**:
```
Poll loop (every 2s, max 30s):
    |
    v
  GraphQL: findScenes(oshash = video.oshash)
    |
    v
  Found?
   /    \
  No     Yes
  |       |
  v       v
Wait 2s  Return scene {id, title, files}
then
retry
```

---

### 8. Create/Find Performers and Studio in Stash

**Trigger**: Scene found in step 7.

**What happens**:
1. For each performer name in `video.performers`:
   a. Call `stash_client.find_or_create_performer(name)`.
   b. If performer exists in Stash, get its ID.
   c. If not, create it and get the new ID.
   d. Collect all performer IDs.
2. If `video.studio` is set:
   a. Call `stash_client.find_or_create_studio(video.studio)`.
   b. Get the studio ID.

**Data flow**:
```
video.performers = ["Performer A", "Performer B"]
video.studio = "SomeStudio"
    |
    v
For "Performer A":
  findPerformers(name="Performer A") -> found? -> use existing ID
                                     -> not found? -> performerCreate -> new ID
For "Performer B":
  (same)
    |
    v
For "SomeStudio":
  findStudios(name="SomeStudio") -> found? -> use existing ID
                                 -> not found? -> studioCreate -> new ID
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

## Error Handling Summary

| Step | Error | Handling |
|------|-------|----------|
| 3 - Scan | yt-dlp extraction fails | Log error, skip channel this cycle |
| 4 - Download | yt-dlp download fails | `status=failed`, `error_message` saved |
| 5 - oshash | File read error | `status=failed`, `error_message` saved |
| 6 - Stash scan | Stash unreachable | `status=failed`, `error_message` saved |
| 7 - Poll | Timeout (scene not found) | `status=failed`, retry possible |
| 8 - Performers | Stash API error | `status=failed`, `error_message` saved |
| 9 - Update | Stash API error | `status=failed`, `error_message` saved |

All failures result in `status=failed` with the error message saved to the database. The user can retry from the UI, which resets the video to `status=pending`.

---

## Retry Flow

When a user clicks "Retry" on a failed video:
1. `POST /videos/{id}/retry` handler sets `video.status = "pending"` and clears `video.error_message`.
2. The `download_processor` job picks it up in its next cycle.
3. The pipeline runs from step 4 onward (or step 5/6 if the file was already downloaded).

---

## Jobs Page & Manual Triggers

The **Jobs** page (`/jobs`) provides a central control panel for all background operations. Each job shows its current status (idle/running), last run time, and duration. The page auto-refreshes via HTMX polling every 3 seconds.

### Available Jobs

| Job | Endpoint | Description |
|-----|----------|-------------|
| **Check All Channels** | `POST /jobs/check_all_channels/trigger` | Scans all enabled channels that are due for new videos (same as the scheduled `channel_checker`). |
| **Process Downloads** | `POST /jobs/process_downloads/trigger` | Downloads and imports the next pending video in the queue (same as the scheduled `download_processor`). |
| **Retry All Failed** | `POST /jobs/retry_all_failed/trigger` | Resets every `status=failed` video back to `status=pending` so the download processor retries them. |

### Contextual Triggers

In addition to the Jobs page, trigger buttons appear in context:

- **"Check All Now"** button on the **Channels** list page — triggers the Check All Channels job.
- **"Retry All Failed"** button on the **Videos** list page — triggers the Retry All Failed job.

### Job Tracking

Jobs are tracked via a `JobInfo` registry in `app/scheduler.py`. Each entry stores:
- `running` flag (protected by an `asyncio.Lock` to prevent concurrent execution)
- `last_run_at` timestamp
- `last_duration_seconds`

When a job is triggered (manually or by the scheduler), it acquires the lock, sets `running=True`, runs, and then records timing data. If the lock is already held, the trigger is skipped to prevent overlap.
