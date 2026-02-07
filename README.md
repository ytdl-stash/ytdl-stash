# ytdl-stash

Monitor video channels, download new videos with [yt-dlp](https://github.com/yt-dlp/yt-dlp), and sync them into [Stash](https://github.com/stashapp/stash) with metadata (performers, studio, date) via its GraphQL API. Uses oshash for reliable scene matching.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- A running [Stash](https://github.com/stashapp/stash) instance (same host or reachable from the container)
- (Optional) Cookies file for sites that require login

## Quick start

### Option A: Pre-built image (recommended)

1. Create a `docker-compose.yml` (or grab the one from this repo) and set the `image`:

   ```yaml
   services:
     ytdl-stash:
       image: ghcr.io/<OWNER>/ytdl-stash:latest
       # ... see docker-compose.yml in this repo for full example
   ```

2. Start the stack:

   ```bash
   docker compose up -d
   ```

### Option B: Build from source

1. Clone the repo and start the stack:

   ```bash
   docker compose up -d
   ```

2. Open the UI at **http://localhost:8282**.

3. Add a channel (e.g. a creator page URL from a supported site). The app will:
   - Scan the channel periodically for new videos
   - Download pending videos (default one at a time; configurable concurrency)
   - Compute oshash and trigger a Stash scan
   - Match the scene and apply title, performers, studio, date

4. Ensure Stash can see the same download directory. By default the app writes to `/downloads` inside the container; mount the same path in Stash’s library (or use **Folder mapping** below).

## Configuration

All settings use environment variables with the `YTDL_` prefix. Example in `docker-compose.yml`:

The **Settings** page (`/settings`) shows the **effective configuration read at startup** (read-only). To change values like `YTDL_MAX_CONCURRENT_DOWNLOADS`, update your environment and restart the container/app.

| Variable | Default | Description |
|----------|---------|-------------|
| `YTDL_STASH_URL` | `http://localhost:9999` | Stash server URL |
| `YTDL_STASH_API_KEY` | `""` | Stash API key (if auth enabled) |
| `YTDL_DOWNLOAD_DIR` | `/downloads` | Where videos are saved (inside container) |
| `YTDL_STASH_DOWNLOAD_DIR` | — | Path to downloads **as Stash sees it** (see Folder mapping) |
| `YTDL_DATA_DIR` | `/app/data` | Where SQLite DB is stored |
| `YTDL_DEFAULT_CHECK_INTERVAL_HOURS` | `6` | Default hours between channel checks |
| `YTDL_MAX_CONCURRENT_DOWNLOADS` | `1` | Max number of videos to download/import in parallel |
| `YTDL_DOWNLOAD_DELAY_SECONDS` | `5` | Seconds between downloads |
| `YTDL_COOKIES_FILE` | — | Optional path to cookies file (e.g. `/app/cookies.txt`) |
| `YTDL_YTDLP_OUTPUT_TEMPLATE` | `%(uploader)s - %(title)s [%(id)s].%(ext)s` | yt-dlp filename template |
| `YTDL_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Folder mapping (Stash path)

If ytdl-stash and Stash use the **same** path for downloads (e.g. both have the volume at `/downloads`), leave `YTDL_STASH_DOWNLOAD_DIR` unset.

If Stash sees the same files under a **different** path (e.g. ytdl-stash writes to `/downloads` but Stash has the library at `/data/downloads`), set:

```yaml
YTDL_STASH_DOWNLOAD_DIR: /data/downloads
```

The app will translate file paths when telling Stash to scan, so Stash can find the files.

## Health check

- **GET /health** returns `{"status": "ok", "db": true, "stash": true/false}`.
- Docker Compose is configured to run this as a container healthcheck every 30s.

## Troubleshooting

See **[docs/recipes/troubleshooting.md](docs/recipes/troubleshooting.md)** for:

- yt-dlp: 403, “video unavailable”, extractor errors, channel scan returns 0
- Stash: connection failed, scene not found after download, folder mapping
- Database: locked, schema changes
- Docker: startup crashes, network, permissions

## Releases

Container images are automatically built and published to [GitHub Container Registry](https://ghcr.io) when a version tag is pushed:

```bash
git tag v1.0.0
git push origin v1.0.0
```

This produces multi-platform images (linux/amd64 + linux/arm64) tagged as:
- `ghcr.io/<OWNER>/ytdl-stash:1.0.0`
- `ghcr.io/<OWNER>/ytdl-stash:1.0`
- `ghcr.io/<OWNER>/ytdl-stash:1`
- `ghcr.io/<OWNER>/ytdl-stash:latest`

## Development

- Run without Docker: see [docs/recipes/local-dev-without-docker.md](docs/recipes/local-dev-without-docker.md).
- Architecture and patterns: [docs/architecture/README.md](docs/architecture/README.md), [docs/patterns/](docs/patterns/).
