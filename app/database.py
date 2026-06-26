"""Async SQLAlchemy engine, session factory, and FastAPI dependency."""

import logging
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Columns to add to channels if missing (for existing DBs without Alembic)
_CHANNEL_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("stash_performer_id", "VARCHAR(50)"),
    ("performer_image_url", "VARCHAR(2048)"),
    ("stash_performer_data", "JSON"),
    ("stash_studio_id", "VARCHAR(50)"),
    ("stash_studio_data", "JSON"),
    ("max_video_age_days", "INTEGER"),
    ("min_duration_seconds", "INTEGER"),
]

# Columns to add to videos if missing (for existing DBs without Alembic)
_VIDEO_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("downloaded_at", "TEXT"),
    ("synced_at", "TEXT"),
    ("scrape_attempted_at", "TEXT"),
    ("generate_triggered_at", "TEXT"),
]


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


engine: AsyncEngine | None = None
async_session: async_sessionmaker[AsyncSession] | None = None


async def init_db(settings: "Settings") -> None:
    """Create async engine, session factory, and all tables. Call at app startup."""
    global engine, async_session
    import app.models  # noqa: F401 — ensures models are registered with Base

    db_path = f"{settings.data_dir}/ytdl-stash.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001, ANN202
        """Tune SQLite on every connection.

        ``busy_timeout`` makes a contended write *wait* (up to 30s) for the
        lock instead of instantly raising ``database is locked`` — the dominant
        DB error when many channel checks run concurrently on a slow data
        mount. WAL additionally lets readers and the writer proceed without
        blocking each other; some network/FUSE mounts can't host WAL's shared
        memory, so we verify it actually engaged before lowering ``synchronous``
        and never let a pragma failure break the connection.
        """
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=30000")
            # NB: the aiosqlite adapter's cursor.execute() returns None, so read
            # the result back with a separate fetchone() rather than chaining.
            cur.execute("PRAGMA journal_mode=WAL")
            mode = cur.fetchone()
            if mode and str(mode[0]).lower() == "wal":
                cur.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            logger.warning("Could not apply SQLite tuning pragmas", exc_info=True)
        finally:
            cur.close()

    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_channels_columns(conn)
        await _migrate_videos_columns(conn)
        await _recover_stuck_videos(conn, settings)
    logger.info("Database initialized at %s", db_path)


async def _migrate_channels_columns(conn) -> None:
    """Add missing columns to channels table for existing databases."""
    result = await conn.execute(text("PRAGMA table_info(channels)"))
    existing = {row[1] for row in result.fetchall()}
    for col_name, col_type in _CHANNEL_MIGRATION_COLUMNS:
        if col_name not in existing:
            await conn.execute(
                text(f"ALTER TABLE channels ADD COLUMN {col_name} {col_type}")
            )
            logger.info("Added column channels.%s", col_name)


async def _migrate_videos_columns(conn) -> None:
    """Add missing columns to videos table and backfill milestone timestamps."""
    result = await conn.execute(text("PRAGMA table_info(videos)"))
    existing = {row[1] for row in result.fetchall()}
    for col_name, col_type in _VIDEO_MIGRATION_COLUMNS:
        if col_name not in existing:
            await conn.execute(
                text(f"ALTER TABLE videos ADD COLUMN {col_name} {col_type}")
            )
            logger.info("Added column videos.%s", col_name)

    # Best-effort backfill: set synced_at/downloaded_at from updated_at for existing rows.
    r1 = await conn.execute(
        text(
            "UPDATE videos SET synced_at = updated_at "
            "WHERE status = 'synced' AND (synced_at IS NULL OR synced_at = '')"
        )
    )
    r2 = await conn.execute(
        text(
            "UPDATE videos SET downloaded_at = updated_at "
            "WHERE status IN ('downloaded', 'synced') AND (downloaded_at IS NULL OR downloaded_at = '')"
        )
    )
    if r1.rowcount or r2.rowcount:
        logger.info(
            "Backfilled video milestone timestamps (synced_at: %d, downloaded_at: %d)",
            r1.rowcount,
            r2.rowcount,
        )


async def _recover_stuck_videos(conn, settings: "Settings") -> None:
    """Reset videos stuck in intermediate states on startup.

    If the server crashed mid-download or mid-import, videos may be left in
    'downloading', 'importing', or 'cancelling' status with no running task
    to complete them.

    Smart recovery:
    - If ``original_filename`` is set and the file exists on disk, we skip
      straight to ``downloaded`` status instead of ``pending``, avoiding a
      wasteful re-download.
    - Otherwise, we reset to ``pending`` so the download processor retries.
    - ``downloaded`` status is never touched (file on disk, just needs
      manual retry to continue the import pipeline).
    """
    _STUCK_STATUSES = ("downloading", "importing", "cancelling")

    # Fetch stuck videos so we can check for files on disk per-row.
    rows = await conn.execute(
        text(
            "SELECT id, original_filename, status "
            "FROM videos WHERE status IN (:s1, :s2, :s3)"
        ),
        {
            "s1": _STUCK_STATUSES[0],
            "s2": _STUCK_STATUSES[1],
            "s3": _STUCK_STATUSES[2],
        },
    )
    stuck = rows.fetchall()
    if not stuck:
        return

    recovered_to_pending = 0
    recovered_to_downloaded = 0
    download_dir = settings.download_dir

    for row in stuck:
        video_id, original_filename, status = row[0], row[1], row[2]
        # Check if file already exists on disk
        if original_filename:
            filepath = os.path.join(download_dir, original_filename)
            if os.path.isfile(filepath):
                now = datetime.now(UTC).isoformat()
                await conn.execute(
                    text(
                        "UPDATE videos SET status = 'downloaded', error_message = NULL, "
                        "downloaded_at = COALESCE(downloaded_at, :now) "
                        "WHERE id = :vid"
                    ),
                    {"vid": video_id, "now": now},
                )
                recovered_to_downloaded += 1
                logger.info(
                    "Video %s: recovered to 'downloaded' (file exists: %s)",
                    video_id, filepath,
                )
                continue

        # File not found or no filename — reset to pending for re-download
        await conn.execute(
            text(
                "UPDATE videos SET status = 'pending', error_message = NULL "
                "WHERE id = :vid"
            ),
            {"vid": video_id},
        )
        recovered_to_pending += 1

    total = recovered_to_pending + recovered_to_downloaded
    if total:
        logger.info(
            "Recovered %d stuck video(s): %d to pending, %d to downloaded (file on disk)",
            total, recovered_to_pending, recovered_to_downloaded,
        )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async session and commits/rolls back automatically."""
    if async_session is None:
        raise RuntimeError("Database not initialized; init_db() must run at startup")
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
