# SQLAlchemy Async Patterns

Reference patterns for how this project uses SQLAlchemy 2.x with async SQLite. Read this before adding models, queries, or modifying the database layer.

---

## Engine and Session Setup

```python
# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

engine = create_async_engine(
    f"sqlite+aiosqlite:///{data_dir}/ytdl-stash.db",
    echo=False,  # Set True for SQL logging during development
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

**Key**: `expire_on_commit=False` prevents lazy-load errors when accessing model attributes after commit (SQLite + async does not support implicit lazy loading).

---

## Database Initialization

```python
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

Called from the FastAPI lifespan handler at startup. `run_sync` bridges the sync `create_all` into async context.

---

## Session Dependency

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

**Rule**: Routes get their session via `db: AsyncSession = Depends(get_db)`. The dependency auto-commits on success, auto-rolls-back on exception.

---

## Model Definition

All models inherit from `Base` and use SQLAlchemy 2.x `Mapped` type annotations.

```python
from sqlalchemy import String, Integer, Boolean, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import UTC, datetime

from app.models import TZDateTime  # custom TypeDecorator — see below

class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048))
    site: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    check_interval_hours: Mapped[int] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    videos: Mapped[list["Video"]] = relationship(back_populates="channel", cascade="all, delete-orphan")
```

**Rule**: Always use `Mapped[T]` + `mapped_column()` (SQLAlchemy 2.x style). Do NOT use the legacy `Column()` style.

**Rule**: Always use the `TZDateTime` custom type (defined in `app/models.py`) for datetime columns — never bare `DateTime(timezone=True)`. SQLite stores datetimes as plain text and strips timezone info on round-trip. `TZDateTime` is a `TypeDecorator` that wraps `DateTime(timezone=True)` and re-attaches `timezone.utc` to any naive value read back from the database. Without it, Python-side comparisons against `datetime.now(UTC)` raise `TypeError: can't compare offset-naive and offset-aware datetimes`.

---

## Common Query Patterns

### Select all:
```python
result = await db.execute(select(Channel))
channels = result.scalars().all()
```

### Select with filter:
```python
result = await db.execute(
    select(Video).where(Video.status == "pending").order_by(Video.created_at)
)
videos = result.scalars().all()
```

### Get by primary key:
```python
channel = await db.get(Channel, channel_id)
```

### Get first matching:
```python
result = await db.execute(
    select(Video).where(Video.site_video_id == site_id)
)
video = result.scalar_one_or_none()
```

### Insert:
```python
channel = Channel(name="Test", url="https://...", site="pornhub", check_interval_hours=6)
db.add(channel)
await db.flush()  # Generates the ID without committing
# channel.id is now available
```

### Update:
```python
channel = await db.get(Channel, channel_id)
channel.name = "New Name"
channel.updated_at = datetime.now(UTC)
# Commit happens automatically via get_db dependency
```

### Delete:
```python
channel = await db.get(Channel, channel_id)
await db.delete(channel)
```

### Count:
```python
result = await db.execute(select(func.count(Video.id)).where(Video.status == "failed"))
count = result.scalar()
```

---

## Relationship Loading

Async SQLAlchemy does NOT support implicit lazy loading. You must explicitly load relationships.

### Option 1: selectinload (preferred):
```python
result = await db.execute(
    select(Channel).options(selectinload(Channel.videos))
)
channel = result.scalar_one()
# channel.videos is now loaded
```

### Option 2: Separate query:
```python
channel = await db.get(Channel, channel_id)
result = await db.execute(select(Video).where(Video.channel_id == channel_id))
videos = result.scalars().all()
```

**Rule**: Never access `channel.videos` without explicitly loading it first. This will raise `MissingGreenlet` error in async mode.

---

## Indexes

Define indexes for frequently-queried columns:

```python
class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        Index("ix_videos_status", "status"),
        Index("ix_videos_channel_id", "channel_id"),
    )

    site_video_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
```

---

## JSON Columns

SQLite supports JSON via `sqlalchemy.JSON`. Used for the `performers` field (list of strings):

```python
performers: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

Read/write as Python lists:
```python
video.performers = ["Performer A", "Performer B"]
# Stored as JSON: ["Performer A", "Performer B"]
```

**Caveat**: JSON columns in SQLite have limited query support. Do not filter or search within JSON columns -- use them as opaque storage only.
