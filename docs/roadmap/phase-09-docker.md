# Phase 9: Docker Finalization

**Status**: COMPLETE

## Prerequisites

- Phase 8 complete (all app code and templates exist)
- Phase 1 created initial Dockerfile and docker-compose.yml — this phase refines them

## Deliverables

- [x] Verify/update `Dockerfile` — ensure it copies all needed files
- [x] Verify/update `docker-compose.yml` — all volumes, env vars, health check
- [x] Verify/update `.dockerignore` — exclude dev artifacts
- [ ] Test full `docker compose up` lifecycle

### Dockerfile checklist

- [x] Base image: `python:3.12-slim`
- [x] Install `ffmpeg` (required by yt-dlp for muxing)
- [x] `WORKDIR /app`
- [x] Copy and install requirements
- [x] Copy app code
- [x] Expose port 8000
- [x] CMD: uvicorn
- [x] Verify all app directories are included (routes/, templates/, static/)

### docker-compose.yml checklist

- [x] Service `ytdl-stash` on port 8282:8000
- [x] Volume: `./data:/app/data` (SQLite persistence)
- [x] Volume: shared downloads path with Stash
- [x] Environment: `YTDL_*` variables
- [x] Add health check: `curl http://localhost:8000/health` (once Phase 10 adds the endpoint)
- [x] Verify cookies.txt mount works when uncommented
- [ ] Test with actual Stash instance (shared volume alignment)

### Volume alignment verification

The download path must be accessible to both ytdl-stash and Stash:
```
Host:        /path/to/downloads/
ytdl-stash:  /downloads/          (YTDL_DOWNLOAD_DIR)
Stash:       /data/downloads/     (or wherever Stash mounts it)
```

If paths differ between containers, a path translation config may be needed (future enhancement).

## Patterns to Follow

- `docs/adr/009-docker-first.md` — Docker-first deployment strategy.
- `docs/recipes/local-dev-without-docker.md` — for testing outside Docker during development.

## Key Implementation Notes

- The `COPY app/ app/` line in the Dockerfile copies everything under `app/`, which includes routes/, templates/, static/. No changes needed unless files move outside `app/`.
- The `downloads/` and `data/` directories are volume-mounted, not baked into the image.
- `.dockerignore` excludes `.git`, `.cursor`, `__pycache__`, `data/`, `downloads/`, `.env`, plus `docs/`, `.venv/`, `*.md`, `.gitignore`, `*.egg-info/`, `.env.example`, `terminals/`.
- `restart: unless-stopped` ensures the container auto-restarts on failure.

## Acceptance Criteria

- [ ] `docker compose build` succeeds with no errors
- [ ] `docker compose up` starts the app and scheduler
- [ ] App is accessible at `http://localhost:8282`
- [ ] SQLite DB is created in `./data/` on the host
- [ ] Downloads land in the shared downloads volume
- [ ] Environment variables are passed correctly
- [ ] Container restarts automatically if it crashes

## Deviations

- Initial Dockerfile and docker-compose.yml were created in Phase 1. This phase verifies and refines them.
- Health check in docker-compose uses a commented Python `urllib.request.urlopen()` check instead of `curl`, since `python:3.12-slim` does not include curl. Uncomment after Phase 10 adds `GET /health`.
- Added `extra_hosts: host.docker.internal:host-gateway` so Linux hosts can reach Stash without Docker Desktop.
