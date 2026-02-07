"""SQLAlchemy models for Channel and Video."""

from datetime import UTC, date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TZDateTime(TypeDecorator):
    """A DateTime type that ensures UTC tzinfo survives a SQLite round-trip.

    SQLite stores datetimes as plain text and loses timezone information.
    This decorator re-attaches ``timezone.utc`` to any naive value coming
    back from the database so that Python-side comparisons against
    aware datetimes (e.g. ``datetime.now(UTC)``) never raise a
    ``TypeError``.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(
        self, value: datetime | None, dialect: object
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Channel(Base):
    """A monitored channel (model/user page) from a video site."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048))
    site: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    check_interval_hours: Mapped[int] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    stash_performer_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    performer_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    stash_performer_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    max_video_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    videos: Mapped[list["Video"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Video(Base):
    """A video discovered from a channel, with download and Stash sync status."""

    __tablename__ = "videos"
    __table_args__ = (
        Index("ix_videos_status", "status"),
        Index("ix_videos_channel_id", "channel_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    site_video_id: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(2048))
    upload_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    performers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    studio: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    oshash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stash_scene_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    channel: Mapped["Channel"] = relationship(back_populates="videos")
