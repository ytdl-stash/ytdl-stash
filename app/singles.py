"""Synthetic channel that owns single videos added by URL.

Videos require a channel (``Video.channel_id`` is NOT NULL), so one-off videos
are attached to a hidden sentinel channel instead.  The sentinel URL uses a
non-HTTP scheme on purpose: the channel checker only considers channels whose
URL starts with ``http://``/``https://`` (see ``scheduler.py``), so this channel
is never scanned.  ``enabled=False`` is a second line of defence.

Its ``min_duration_seconds`` / ``max_video_age_days`` stay NULL so the pipeline's
filter blocks no-op — an explicitly requested video is never skipped for being
too short or too old.  It also has no ``stash_studio_id``, so the Stash URL
scraper fills in the studio instead of inheriting a channel's.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel

logger = logging.getLogger(__name__)

SINGLES_CHANNEL_URL = "ytdl-stash://singles"
SINGLES_CHANNEL_NAME = "Single Videos"
SINGLES_CHANNEL_SITE = "single"

# Never scanned (non-HTTP URL + disabled), but the column is NOT NULL.
_SINGLES_CHECK_INTERVAL_HOURS = 8760

# Channel.url has no unique constraint, so serialise the select-then-insert:
# two concurrent adds would otherwise each create their own sentinel.
_create_lock = asyncio.Lock()


async def get_or_create_singles_channel(db: AsyncSession) -> Channel:
    """Return the sentinel channel that owns single videos, creating it on first use."""
    async with _create_lock:
        result = await db.execute(
            select(Channel).where(Channel.url == SINGLES_CHANNEL_URL)
        )
        channel = result.scalars().first()
        if channel is not None:
            return channel

        channel = Channel(
            name=SINGLES_CHANNEL_NAME,
            url=SINGLES_CHANNEL_URL,
            site=SINGLES_CHANNEL_SITE,
            enabled=False,
            check_interval_hours=_SINGLES_CHECK_INTERVAL_HOURS,
        )
        db.add(channel)
        await db.flush()
        logger.info("Created singles channel (id=%s)", channel.id)
        return channel


def is_singles_channel_url(url: str | None) -> bool:
    """True when a channel URL is the singles sentinel (used by templates)."""
    return url == SINGLES_CHANNEL_URL
