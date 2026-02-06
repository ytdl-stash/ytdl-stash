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
