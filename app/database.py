"""Async SQLAlchemy engine, session factory, and FastAPI dependency."""

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy import text
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
    ("max_video_age_days", "INTEGER"),
    ("min_duration_seconds", "INTEGER"),
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
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_channels_columns(conn)
        await _recover_stuck_videos(conn)
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


async def _recover_stuck_videos(conn) -> None:
    """Reset videos stuck in intermediate states back to pending on startup.

    If the server crashed mid-download or mid-import, videos may be left in
    'downloading' or 'importing' status with no running task to complete them.
    """
    _STUCK_STATUSES = ("downloading", "downloaded", "importing", "cancelling")
    result = await conn.execute(
        text(
            "UPDATE videos SET status = 'pending', error_message = NULL "
            "WHERE status IN (:s1, :s2, :s3, :s4)"
        ),
        {
            "s1": _STUCK_STATUSES[0],
            "s2": _STUCK_STATUSES[1],
            "s3": _STUCK_STATUSES[2],
            "s4": _STUCK_STATUSES[3],
        },
    )
    if result.rowcount:
        logger.info(
            "Recovered %d video(s) stuck in intermediate status back to pending",
            result.rowcount,
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
