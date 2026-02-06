# ADR-002: Use SQLite with Async SQLAlchemy

**Status**: Accepted

## Context

The app needs persistent storage for channels and videos. Requirements:
- Track hundreds to low thousands of records (channels + videos).
- Single-user application running in a container.
- No need for concurrent multi-process writes.
- Must survive container restarts.

## Decision

Use **SQLite** as the database, accessed through **SQLAlchemy 2.x** with the **aiosqlite** async driver.

Database file location: `{data_dir}/ytdl-stash.db` (default `/app/data/ytdl-stash.db`).

The `data/` directory is volume-mounted so the DB survives container rebuilds.

## Alternatives Considered

### PostgreSQL
- Production-grade, excellent concurrent access.
- Requires a separate container or external service.
- Overkill for a single-user app with low write volume.
- Rejected to keep the deployment as a single container.

### TinyDB / JSON file
- Zero-dependency, simple key-value store.
- No SQL, no migrations, no relational queries.
- Rejected because we need relational queries (videos belong to channels, filtering by status).

### Raw SQLite (no ORM)
- Direct `aiosqlite` usage with raw SQL.
- Simpler, fewer dependencies.
- Rejected because SQLAlchemy provides: model classes, relationship loading, migration support (Alembic), and query building -- all valuable as the schema grows.

## Consequences

**Positive:**
- Zero infrastructure beyond the app container.
- Single file DB, easy to backup (just copy `ytdl-stash.db`).
- SQLAlchemy models serve as living documentation of the schema.
- Alembic can be added later for schema migrations.
- Async driver (aiosqlite) prevents blocking the event loop.

**Negative:**
- SQLite does not support concurrent writes from multiple processes (not a problem for this single-process app).
- JSON column type has limited query support in SQLite (only used for `performers` list; we do not query into it).
- WAL mode should be enabled for better read concurrency during downloads:
  ```python
  # In database.py, after engine creation:
  # engine.execute("PRAGMA journal_mode=WAL")
  ```

## Key Implementation Details

- Engine creation uses `create_async_engine("sqlite+aiosqlite:///path/to/db")`
- Session factory uses `async_sessionmaker(engine, class_=AsyncSession)`
- `get_db` is an async generator dependency that yields sessions.
- `init_db()` calls `Base.metadata.create_all()` at startup via `run_sync`.
- The `data/` directory is created in the lifespan handler if it doesn't exist.
