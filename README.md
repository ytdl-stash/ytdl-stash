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

All application settings use environment variables with the `YTDL_` prefix. Every variable is **optional** — the app starts with sensible defaults and zero configuration required.

The **Settings** page (`/settings`) shows the effective configuration read at startup (read-only). To change a value, update your environment and restart the container/app.

### Docker Compose host variables

These are set on the **host** and interpolated by Docker Compose before the container starts. They are _not_ application settings — they control volume mounts and are mapped into `YTDL_*` vars inside the container.

| Variable | Type | Default | Required |
|----------|------|---------|----------|
| `DOWNLOAD_PATH` | `str` | `./downloads` | Optional |
| `STASH_URL` | `str` | `http://host.docker.internal:9999` | Optional |
| `STASH_API_KEY` | `str` | `""` | Optional |

### Core / Stash connection

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `YTDL_STASH_URL` | `str` | `http://localhost:9999` | Optional | Stash server URL |
| `YTDL_STASH_API_KEY` | `str` | `""` | Optional | Stash API key (if auth is enabled) |
| `YTDL_LOG_LEVEL` | `str` | `INFO` | Optional | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

### Paths

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `YTDL_DOWNLOAD_DIR` | `str` | `/downloads` | Optional | Where videos are saved (inside container) |
| `YTDL_STASH_DOWNLOAD_DIR` | `str \| None` | `None` | Optional | Path to downloads **as Stash sees it** (see [Folder mapping](#folder-mapping-stash-path)) |
| `YTDL_DATA_DIR` | `str` | `/app/data` | Optional | Where the SQLite database is stored |
| `YTDL_COOKIES_FILE` | `str \| None` | `None` | Optional | Path to a cookies file (e.g. `/app/cookies.txt`) |

### Scheduling & concurrency

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `YTDL_DEFAULT_CHECK_INTERVAL_HOURS` | `int` | `6` | Optional | Hours between automatic channel checks |
| `YTDL_MAX_CONCURRENT_DOWNLOADS` | `int` | `1` | Optional | Parallel download/import slots (min 1, max 16) |
| `YTDL_DOWNLOAD_DELAY_SECONDS` | `int` | `5` | Optional | Seconds to wait between successive downloads |

### yt-dlp options

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `YTDL_YTDLP_OUTPUT_TEMPLATE` | `str` | `%(uploader)s - %(title)s [%(id)s].%(ext)s` | Optional | yt-dlp filename template |
| `YTDL_YTDLP_FORMAT` | `str \| None` | `None` | Optional | yt-dlp format selector (e.g. `bestvideo+bestaudio/best`) |
| `YTDL_YTDLP_IMPERSONATE` | `str \| None` | `None` | Optional | Browser to impersonate (e.g. `chrome`) |
| `YTDL_YTDLP_USER_AGENT` | `str \| None` | `None` | Optional | Override User-Agent header |
| `YTDL_YTDLP_REFERER` | `str \| None` | `None` | Optional | Override Referer header |
| `YTDL_YTDLP_PROXY` | `str \| None` | `None` | Optional | Proxy URL (e.g. `socks5://127.0.0.1:9050`) |
| `YTDL_YTDLP_SOCKET_TIMEOUT_SECONDS` | `int \| None` | `None` | Optional | Socket/request timeout in seconds |
| `YTDL_YTDLP_RETRIES` | `int` | `3` | Optional | Download retry count |
| `YTDL_YTDLP_FRAGMENT_RETRIES` | `int` | `3` | Optional | HLS/DASH fragment retry count |

### yt-dlp advanced overrides (JSON)

These accept a JSON string and are merged into the yt-dlp options dict at call time.

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `YTDL_YTDLP_HTTP_HEADERS_JSON` | `str` (JSON) | `{}` | Optional | Extra HTTP headers merged into every yt-dlp request |
| `YTDL_YTDLP_SCAN_OPTS_JSON` | `str` (JSON) | `{}` | Optional | Extra yt-dlp options merged for channel scans |
| `YTDL_YTDLP_DOWNLOAD_OPTS_JSON` | `str` (JSON) | `{}` | Optional | Extra yt-dlp options merged for downloads |
| `YTDL_YTDLP_UPDATE_CHECK_INTERVAL_HOURS` | `int` | `24` | Optional | Hours between PyPI checks for a newer yt-dlp |

### Post-sync Stash actions

These control what happens **after** a scene is synced to Stash.

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `YTDL_STASH_SCRAPE_AFTER_SYNC` | `bool` | `true` | Optional | Run the Stash URL scraper on the scene after sync |
| `YTDL_STASH_GENERATE_AFTER_SYNC` | `bool` | `true` | Optional | Trigger Stash metadata generation after sync |
| `YTDL_STASH_GENERATE_COVERS` | `bool` | `true` | Optional | Generate cover images (when generate is enabled) |
| `YTDL_STASH_GENERATE_PREVIEWS` | `bool` | `true` | Optional | Generate video previews (when generate is enabled) |
| `YTDL_STASH_GENERATE_SPRITES` | `bool` | `true` | Optional | Generate sprite sheets (when generate is enabled) |
| `YTDL_STASH_GENERATE_PHASHES` | `bool` | `true` | Optional | Generate perceptual hashes (when generate is enabled) |

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
