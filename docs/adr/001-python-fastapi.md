# ADR-001: Use Python 3.12 with FastAPI

**Status**: Accepted

## Context

We need a web framework for the ytdl-stash application. The app must:
- Serve a web UI for managing channels and videos.
- Run background tasks (channel scanning, downloading).
- Integrate with yt-dlp (a Python library) and Stash (via HTTP/GraphQL).
- Be straightforward to containerize.

## Decision

Use **Python 3.12** as the runtime and **FastAPI** as the web framework, served by **Uvicorn**.

## Alternatives Considered

### Flask
- Mature, well-documented, huge ecosystem.
- Lacks native async support (requires workarounds or Quart fork).
- No built-in dependency injection.
- No automatic OpenAPI generation.
- Rejected because async is essential for non-blocking GraphQL calls and download orchestration.

### Django
- Full-featured (ORM, admin, auth, migrations).
- Heavy for this use case -- we do not need its ORM (using SQLAlchemy) or admin.
- Async support is improving but still partial.
- Rejected as overkill for a single-purpose tool.

### Node.js / Go / Rust
- Would require shelling out to yt-dlp CLI instead of using it as a library.
- Adds inter-process communication complexity.
- Rejected because yt-dlp's Python API is a first-class citizen and the core of this app.

## Consequences

**Positive:**
- Single language for web server, downloader, and all integrations.
- FastAPI's dependency injection is perfect for DB sessions and settings.
- Automatic OpenAPI docs at `/docs` for free.
- Native async/await for Stash GraphQL calls via httpx.
- Lifespan context manager for clean startup/shutdown.

**Negative:**
- Python is slower than Go/Rust for CPU-bound work (not a concern here; all work is I/O-bound).
- FastAPI has a smaller ecosystem than Flask/Django (mitigated by Starlette compatibility).

## References

- [FastAPI docs](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
