# Phase 1: Project Scaffold and Configuration

**Status**: COMPLETE

## Prerequisites

None — this is the first phase.

## Deliverables

- [x] `requirements.txt` — pinned dependencies
- [x] `Dockerfile` — Python 3.12-slim, ffmpeg, uvicorn
- [x] `docker-compose.yml` — service, volumes, env vars
- [x] `.dockerignore` — exclude .git, data/, downloads/, __pycache__
- [x] `app/__init__.py` — empty package init
- [x] `app/main.py` — FastAPI factory with lifespan, static/template mounts
- [x] `app/config.py` — Pydantic `BaseSettings` with `YTDL_*` env vars
- [x] `app/static/.gitkeep` — placeholder for static assets
- [x] `app/templates/.gitkeep` — placeholder for templates

## Patterns to Follow

- `docs/patterns/fastapi.md` — app factory, lifespan pattern
- `docs/adr/006-pydantic-settings.md` — use `model_config` dict, NOT `class Config`
- `docs/adr/009-docker-first.md` — Docker-first deployment

## Acceptance Criteria

- [x] `docker compose build` succeeds
- [x] `docker compose up` starts uvicorn on port 8282
- [x] `GET /docs` returns OpenAPI spec page
- [x] Settings read from `YTDL_*` env vars correctly
- [x] `app/config.py` uses `model_config = {"env_prefix": "YTDL_"}` (Pydantic v2)

## Deviations

- Plan snippet showed `class Config:` inside Settings; actual code correctly uses `model_config` dict per ADR-006.
- Added `pydantic-settings>=2.7` to requirements.txt (not in original plan list, but required for `BaseSettings` in Pydantic v2).
