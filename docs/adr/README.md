# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for ytdl-stash. ADRs document significant architectural choices, why they were made, and what alternatives were considered.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [001](001-python-fastapi.md) | Use Python 3.12 with FastAPI | Accepted |
| [002](002-sqlite-async.md) | Use SQLite with async SQLAlchemy | Accepted |
| [003](003-ytdlp-as-library.md) | Use yt-dlp as a Python library, not CLI | Accepted |
| [004](004-oshash-scene-matching.md) | Use oshash for Stash scene matching | Accepted |
| [005](005-jinja2-htmx.md) | Use Jinja2 + HTMX instead of an SPA | Accepted |
| [006](006-pydantic-settings.md) | Use Pydantic BaseSettings for configuration | Accepted |
| [007](007-apscheduler.md) | Use APScheduler for periodic jobs | Accepted |
| [008](008-sequential-downloads.md) | Sequential downloads with rate limiting | Accepted |
| [009](009-docker-first.md) | Docker-first single-container deployment | Accepted |

## Format

Each ADR follows this template:

- **Status**: Accepted / Superseded / Deprecated
- **Context**: What problem or question prompted this decision?
- **Decision**: What did we decide?
- **Alternatives Considered**: What else was on the table?
- **Consequences**: What are the trade-offs?
