# Phase 2: Database Models

**Status**: COMPLETE

## Prerequisites

- Phase 1 complete (app skeleton, config.py)

## Deliverables

- [x] `app/database.py` — async engine, sessionmaker, `Base`, `get_db` dependency, `init_db()`
- [x] `app/models.py` — `Channel` and `Video` models

### Channel model fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | `Mapped[int]` | Primary key |
| `name` | `Mapped[str]` | Display name |
| `url` | `Mapped[str]` | Channel/model/user page URL |
| `site` | `Mapped[str]` | Derived from URL (e.g. "pornhub") |
| `enabled` | `Mapped[bool]` | Default True |
| `check_interval_hours` | `Mapped[int]` | Hours between checks |
| `last_checked_at` | `Mapped[datetime \| None]` | Nullable |
| `created_at` | `Mapped[datetime]` | Default `datetime.utcnow` |
| `updated_at` | `Mapped[datetime]` | Default + onupdate `datetime.utcnow` |

Relationship: `videos` -> list of Video (cascade delete-orphan)

### Video model fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | `Mapped[int]` | Primary key |
| `channel_id` | `Mapped[int]` | FK to channels |
| `site_video_id` | `Mapped[str]` | Unique, indexed |
| `title` | `Mapped[str]` | |
| `url` | `Mapped[str]` | |
| `upload_date` | `Mapped[date \| None]` | |
| `performers` | `Mapped[list \| None]` | JSON column |
| `studio` | `Mapped[str \| None]` | |
| `duration_seconds` | `Mapped[int \| None]` | |
| `thumbnail_url` | `Mapped[str \| None]` | |
| `original_filename` | `Mapped[str \| None]` | Filename at download time |
| `oshash` | `Mapped[str \| None]` | Computed after download |
| `status` | `Mapped[str]` | pending/downloading/downloaded/importing/synced/failed |
| `error_message` | `Mapped[str \| None]` | |
| `stash_scene_id` | `Mapped[str \| None]` | Stash scene ID after sync |
| `metadata_json` | `Mapped[str \| None]` | Raw yt-dlp info_dict (Text column) |
| `created_at` | `Mapped[datetime]` | |
| `updated_at` | `Mapped[datetime]` | |

Indexes: `ix_videos_status` on `status`, `ix_videos_channel_id` on `channel_id`.
Relationship: `channel` -> Channel (back_populates)

## Patterns to Follow

- `docs/patterns/sqlalchemy-async.md` — **READ THIS FIRST**. Covers `Mapped[T]` + `mapped_column()`, relationships, indexes, JSON columns, query patterns.
- `docs/recipes/add-database-field.md` — column type reference table.
- `docs/adr/002-sqlite-async.md` — why SQLite + aiosqlite.

## Acceptance Criteria

- [x] `app/models.py` defines `Channel` and `Video` using `Mapped[T]` + `mapped_column()`
- [x] Both models inherit from `Base` (from `app/database.py`)
- [x] `Video.site_video_id` has a unique constraint and index
- [x] `Video.status` and `Video.channel_id` have indexes via `__table_args__`
- [x] Channel-Video relationship is bidirectional with `cascade="all, delete-orphan"`
- [x] `init_db()` successfully creates both tables (app starts without errors)
- [x] `database.py` imports `app.models` before `create_all` (already done)

## Deviations

- `database.py` imports `app.models` inside `init_db()` (lazy import to avoid circular dependency).
- `Video.performers` typed as `Mapped[list[str] | None]` (more specific than plan's `Mapped[list | None]`).
- String columns use explicit lengths (`String(255)`, `String(2048)`, `String(500)`) rather than unbounded `String`.
