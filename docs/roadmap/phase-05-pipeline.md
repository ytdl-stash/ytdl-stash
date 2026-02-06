# Phase 5: Download-to-Stash Pipeline

**Status**: COMPLETE

## Prerequisites

- Phase 2 complete (Channel + Video models)
- Phase 3 complete (downloader.py — scan, download, oshash)
- Phase 4 complete (stash_client.py — GraphQL methods)

## Deliverables

- [x] `app/pipeline.py` — orchestration functions

### Functions to implement

**`process_channel_scan(channel, db, settings) -> int`**
- Calls `async_scan_channel(channel.url, settings.cookies_file)`
- For each entry: check if `site_video_id` exists in DB
- Insert new videos with `status="pending"`
- Update `channel.last_checked_at`
- Returns count of new videos found

**`process_single_download(video, db, settings, stash_client) -> None`**
- Full lifecycle for one video: download -> oshash -> scan -> match -> tag
- Status transitions: `pending -> downloading -> downloaded -> importing -> synced`
- On any failure: `status = "failed"`, `error_message` saved

**`process_pending_downloads(db, settings, stash_client) -> None`**
- Query one video with `status="pending"` (FIFO by `created_at`)
- Call `process_single_download`
- Wait `settings.download_delay_seconds` before returning

### Pipeline flow (single video)

```
1. Set status = "downloading"
2. download_video() via asyncio.to_thread()
3. Save metadata (filepath, performers, studio, etc.)
4. Set status = "downloaded"
5. compute_oshash() via asyncio.to_thread()
6. Save oshash to DB
7. Set status = "importing"
8. stash_client.trigger_scan([filepath])
9. stash_client.wait_for_scene(oshash)
10. If no scene found -> status = "failed"
11. find_or_create performers and studio
12. stash_client.update_scene(scene_id, title, urls, date, studio_id, performer_ids)
13. Save stash_scene_id, set status = "synced"
```

## Patterns to Follow

- `docs/data-flow.md` — **READ THIS FIRST**. Complete step-by-step data flow with diagrams for every stage, error handling table, and retry flow.
- `docs/patterns/ytdlp.md` — async wrapping with `asyncio.to_thread()`.
- `docs/patterns/stash-graphql.md` — polling pattern for scene discovery.
- `docs/patterns/sqlalchemy-async.md` — query/update patterns for status transitions.
- `docs/adr/008-sequential-downloads.md` — one download at a time with delay.

## Key Implementation Notes

- Downloads are sequential — one video at a time, never parallel.
- Each video's status transitions independently. One failure does not block others.
- The file path sent to `trigger_scan` must match what Stash sees (shared volume mount).
- Upload date from yt-dlp (`YYYYMMDD`) must be formatted as `YYYY-MM-DD` for Stash.
- The pipeline should be resilient: wrap each major step in try/except, save error_message on failure.

## Acceptance Criteria

- [x] `process_channel_scan` discovers new videos and inserts them as `pending`
- [x] `process_channel_scan` skips videos already in DB (idempotent)
- [x] `process_single_download` transitions through all status states correctly
- [x] Failed downloads set `status="failed"` with a meaningful `error_message`
- [x] oshash is computed after download, before Stash scan
- [x] Scene polling respects the 30s timeout
- [x] Performers and studio are created in Stash if they don't exist
- [x] `stash_scene_id` is saved to the video row on success
- [x] Rate limiting delay is applied between downloads

## Deviations

(none yet)
