# Recipe: Troubleshooting

Common issues and how to resolve them.

---

## yt-dlp Issues

### "HTTP Error 403: Forbidden" during download
**Cause**: The site is rate-limiting or requires authentication.
**Fix**:
1. Increase `YTDL_DOWNLOAD_DELAY_SECONDS` (try 10-30 seconds).
2. Provide a cookies.txt file:
   - Export cookies from your browser using a browser extension (e.g., "Get cookies.txt LOCALLY").
   - Mount it: `volumes: ["./cookies.txt:/app/cookies.txt:ro"]`.
   - Set `YTDL_COOKIES_FILE=/app/cookies.txt`.

### "Video unavailable"
**Cause**: Video was deleted, made private, or is geo-blocked.
**Fix**: This is expected. The video will be marked as `failed` with this error message. No action needed unless it's a widespread issue.

### "Unable to extract" or extractor errors
**Cause**: yt-dlp's extractor for that site is broken (sites change their page structure).
**Fix**: Update yt-dlp to the latest version. Rebuild the Docker image:
```bash
docker compose build --no-cache
docker compose up -d
```

### Channel scan returns 0 videos
**Cause**: The channel URL format may not be recognized by yt-dlp, or the channel is empty.
**Fix**:
1. Test the URL directly: `docker compose exec ytdl-stash python -c "import yt_dlp; print(yt_dlp.YoutubeDL({'extract_flat': True}).extract_info('THE_URL', download=False))"`.
2. Check if yt-dlp supports that site: `yt-dlp --list-extractors | grep sitename`.
3. Try the direct channel/model page URL, not a search or playlist URL.

---

## Stash Integration Issues

### "Stash connection test failed"
**Cause**: The app cannot reach Stash at the configured URL.
**Fix**:
1. Verify `YTDL_STASH_URL` is correct.
2. If using Docker Desktop: `http://host.docker.internal:9999` should work.
3. If using Docker on Linux: use the host's LAN IP (e.g., `http://192.168.1.100:9999`) or add `extra_hosts: ["host.docker.internal:host-gateway"]` to docker-compose.yml.
4. If Stash has authentication enabled, set `YTDL_STASH_API_KEY`.

### Scene not found after download (stuck in "importing")
**Cause**: Stash did not pick up the file, or the oshash does not match.
**Fix**:
1. Verify the download directory is shared between both containers. The file must be visible to Stash at a path it is configured to scan.
2. **Folder mapping**: If ytdl-stash writes to `/downloads` but Stash has that volume at a different path (e.g. `/data/downloads`), set `YTDL_STASH_DOWNLOAD_DIR=/data/downloads` so the app sends the path Stash expects when triggering a scan.
3. Check Stash's library configuration -- the download path must be inside a Stash library path.
4. The polling timeout is 30 seconds. If Stash is slow (large library), this may not be enough. Check the Stash task queue.
5. Verify oshash: the hash is computed immediately after download. If yt-dlp runs a post-processor that modifies the file (e.g., remuxing), the hash will change. Ensure post-processing is complete before oshash computation.

### Performers or studio not created in Stash
**Cause**: The performer/studio name from yt-dlp may not match Stash's expectations.
**Fix**:
1. Check the `metadata_json` field on the video record for the raw yt-dlp data.
2. Performer extraction is best-effort; different sites provide different metadata fields. See `docs/patterns/ytdlp.md` for the extraction logic.
3. You can manually edit the scene in Stash after sync.

---

## Database Issues

### "database is locked"
**Cause**: Multiple processes trying to write to SQLite simultaneously.
**Fix**: This should not happen in normal operation (single process). If it does:
1. Ensure only one instance of ytdl-stash is running.
2. Enable WAL mode: add `PRAGMA journal_mode=WAL` to `init_db()`.

### Schema changes after update
**Cause**: A new version added columns that don't exist in the old database.
**Fix**:
1. **Quick fix**: Delete `data/ytdl-stash.db` and restart (loses all data).
2. **Proper fix**: If Alembic is configured, run `alembic upgrade head`.
3. **Manual fix**: Apply ALTER TABLE statements manually via `sqlite3 data/ytdl-stash.db`.

---

## Docker Issues

### Container crashes on startup
**Cause**: Bad configuration or missing dependencies.
**Fix**:
1. Check logs: `docker compose logs ytdl-stash`.
2. Common causes: invalid env var values, missing volume paths.

### Container cannot access the internet
**Cause**: Docker network configuration.
**Fix**:
1. Check DNS: `docker compose exec ytdl-stash ping -c 1 google.com`.
2. Verify Docker's DNS settings.
3. Some corporate firewalls block Docker traffic.

### Downloads directory is empty / permissions error
**Cause**: Volume mount permissions.
**Fix**:
1. Ensure the host directory exists before starting: `mkdir -p /path/to/downloads`.
2. On Linux, check that the container's user (root by default) can write to the mounted directory.
3. Verify the volume mount in docker-compose.yml points to the correct path.

---

## Performance Issues

### Scans are slow
**Cause**: Large channels with thousands of videos; yt-dlp must paginate through all of them.
**Fix**: This is expected for large channels. The first scan will be slow; subsequent scans skip already-known videos via `site_video_id` matching in the database.

### High memory usage
**Cause**: yt-dlp info_dict for a full channel can be large (stored in `metadata_json`).
**Fix**: Memory usage is transient during downloads. If persistent, consider clearing `metadata_json` after syncing:
```python
video.metadata_json = None  # Clear after successful sync
```
