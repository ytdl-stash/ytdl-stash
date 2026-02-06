"""Performer sync: link channels to Stash performers (find or create by URL)."""

import logging
from typing import TYPE_CHECKING

from app.downloader import async_extract_channel_metadata

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config import Settings
    from app.models import Channel
    from app.stash_client import StashClient

logger = logging.getLogger(__name__)


async def sync_channel_performer(
    channel: "Channel",
    db: "AsyncSession",
    stash: "StashClient",
    settings: "Settings",
) -> None:
    """Link channel to a Stash performer: find by URL, else by name, else create with metadata.
    If channel.stash_performer_id is already set, skip. On failure log and do not raise.
    """
    try:
        if channel.stash_performer_id:
            return

        if not channel.performer_image_url:
            try:
                meta = await async_extract_channel_metadata(
                    channel.url, settings.cookies_file
                )
                if meta.get("thumbnail"):
                    channel.performer_image_url = meta["thumbnail"]
            except Exception as e:
                logger.debug(
                    "Could not extract channel metadata for %s: %s",
                    channel.url,
                    e,
                )

        performer_id = await stash.find_or_create_performer_by_url(
            name=channel.name,
            url=channel.url,
            image_url=channel.performer_image_url,
        )
        channel.stash_performer_id = performer_id
    except Exception as e:
        logger.warning(
            "Performer sync failed for channel %s: %s",
            channel.id,
            e,
            exc_info=True,
        )
