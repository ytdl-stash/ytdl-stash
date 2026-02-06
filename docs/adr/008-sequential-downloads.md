# ADR-008: Sequential Downloads with Rate Limiting

**Status**: Accepted

## Context

When new videos are discovered for a channel, they are queued for download. We need to decide how to process this queue.

Source sites (adult video platforms) are aggressive about rate limiting and IP bans. Downloading too many videos too quickly triggers captchas, temporary bans, or permanent IP blocks.

## Decision

Process downloads **sequentially** (one at a time) with a configurable **delay between downloads** (`YTDL_DOWNLOAD_DELAY_SECONDS`, default 5 seconds).

## Alternatives Considered

### Parallel downloads (asyncio.gather / thread pool)
- Faster throughput.
- Much higher risk of rate limiting and IP bans.
- Complicates error handling and status tracking.
- Rejected because reliability trumps speed for a background automation tool.

### External download manager (aria2c, wget)
- Better resume support.
- yt-dlp already handles retries and resume internally.
- Adds process management complexity.
- Rejected because yt-dlp's built-in downloader is sufficient.

## Consequences

**Positive:**
- Minimal risk of rate limiting.
- Simple implementation: loop through pending videos, download one, wait, repeat.
- Easy to reason about: only one download is active at any time.
- If a download fails, the next one is unaffected.

**Negative:**
- Slow for large backlogs. 100 pending videos at 5s delay = ~8 minutes just in delay time, plus download time.
- Users with fast connections and multiple sites may want parallelism. Can be revisited later as a per-site concurrency setting.

## Implementation Notes

```python
async def process_downloads():
    settings = get_settings()
    while True:
        video = await get_next_pending_video()
        if not video:
            break
        await run_download_pipeline(video)
        await asyncio.sleep(settings.download_delay_seconds)
```

- The scheduler triggers `process_downloads()` every 30 seconds.
- If a previous run is still active, APScheduler's `max_instances=1` prevents overlap.
- The delay is applied **between** downloads, not before the first one.
- The delay is configurable via `YTDL_DOWNLOAD_DELAY_SECONDS` for users who want to tune it.
