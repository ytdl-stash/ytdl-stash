# ADR-009: Docker-First Single-Container Deployment

**Status**: Accepted

## Context

ytdl-stash needs to:
- Run alongside Stash (which is also typically Docker-deployed).
- Share a download directory with Stash so both can access the same video files.
- Include ffmpeg (required by yt-dlp for some post-processing).
- Be easy to deploy for self-hosting users.

## Decision

Deploy as a **single Docker container** via docker-compose, sharing a volume with Stash for the downloads directory.

## Alternatives Considered

### Native Python install (pip/pipx)
- Users must install Python 3.12, ffmpeg, and manage dependencies themselves.
- Platform-specific issues (Windows vs Linux vs macOS).
- Rejected because Docker is already the norm for Stash users.

### Multi-container (app + worker + DB)
- Separate containers for the web server, background worker, and database.
- Standard for production applications.
- Massively overkill for a single-user self-hosted tool.
- Rejected to minimize deployment complexity.

### Snap / Flatpak / AppImage
- Desktop packaging formats.
- This is a server-side app, not a desktop app.
- Rejected as wrong distribution model.

## Consequences

**Positive:**
- Single `docker compose up` to start everything.
- ffmpeg is bundled in the image (installed via apt in the Dockerfile).
- Volume mounts make data persistence and Stash integration trivial.
- Environment variables (the Docker standard) are the only configuration mechanism.
- Users can add ytdl-stash to their existing Stash docker-compose.yml.

**Negative:**
- Users must have Docker installed (expected for Stash users).
- Development is slightly more complex (need to rebuild image for changes, or use a bind mount for live reload).
- SQLite in a container requires a volume mount for persistence.

## Docker Architecture

```
docker-compose.yml
  |
  +-- ytdl-stash (this app)
  |     port: 8282:8000
  |     volumes:
  |       - ./data:/app/data          (SQLite DB)
  |       - /path/to/downloads:/downloads  (shared with Stash)
  |     env: YTDL_STASH_URL, YTDL_STASH_API_KEY, ...
  |
  +-- stash (user's existing Stash instance)
        port: 9999:9999
        volumes:
          - /path/to/downloads:/data/downloads  (same physical path)
```

The critical piece is that **both containers mount the same host directory** for downloads. When ytdl-stash saves a file to `/downloads/video.mp4`, Stash can see it at its own mount point and scan it.

## Dockerfile Layering

```dockerfile
FROM python:3.12-slim               # Small base image (~150MB)
RUN apt-get install ffmpeg           # Required by yt-dlp
COPY requirements.txt && pip install # Dependencies layer (cached)
COPY app/                            # Application code (changes often)
CMD uvicorn app.main:app             # Single process
```

Layers are ordered for optimal Docker cache: dependencies change less often than app code, so they are installed first.

## Networking

- Default Stash URL: `http://host.docker.internal:9999` (works on Docker Desktop for Windows/Mac).
- For Linux hosts: users may need `--add-host=host.docker.internal:host-gateway` in docker-compose, or use the host's LAN IP, or use a shared Docker network.
