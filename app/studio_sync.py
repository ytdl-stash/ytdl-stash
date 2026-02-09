"""Studio sync: link channels to Stash studios by URL.

Find existing studio by channel URL in studio urls; create if none.
Pull full studio data locally; push gap-fills (URL, image, details) to Stash.
"""

import logging
from typing import TYPE_CHECKING

from app.downloader import async_extract_channel_metadata
from app.performer_sync import is_placeholder_name

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config import Settings
    from app.models import Channel
    from app.stash_client import StashClient

logger = logging.getLogger(__name__)


async def _enrich_channel_and_get_description(
    channel: "Channel",
    settings: "Settings",
) -> str | None:
    """Fill in channel name and thumbnail from yt-dlp when missing. Return description for studio details."""
    try:
        meta = await async_extract_channel_metadata(channel.url, settings)
        if is_placeholder_name(channel.name) and meta.get("name"):
            channel.name = meta["name"]
        if not channel.performer_image_url and meta.get("thumbnail"):
            channel.performer_image_url = meta["thumbnail"]
        desc = meta.get("description")
        return str(desc).strip() if desc else None
    except Exception as e:
        logger.debug(
            "Could not extract channel metadata for %s: %s",
            channel.url,
            e,
        )
        return None


async def _pull_studio_from_stash(
    channel: "Channel",
    stash: "StashClient",
) -> dict | None:
    """Fetch full studio data from Stash and store it locally.

    Returns the Stash studio dict (or None if not found).
    """
    if not channel.stash_studio_id:
        return None

    studio = await stash.get_studio(channel.stash_studio_id)
    if not studio:
        logger.warning(
            "Stash studio %s not found for channel %s",
            channel.stash_studio_id,
            channel.id,
        )
        return None

    channel.stash_studio_data = studio
    logger.debug(
        "Pulled Stash studio data for channel %s (studio %s)",
        channel.id,
        channel.stash_studio_id,
    )
    return studio


async def _push_to_stash(
    channel: "Channel",
    stash: "StashClient",
    stash_studio: dict,
    channel_description: str | None,
) -> None:
    """Push source data to Stash for any fields the Stash studio is missing.

    We only fill in gaps — never overwrite data the user set in Stash.
    """
    if not channel.stash_studio_id:
        return

    updates: dict = {}
    stash_urls = stash_studio.get("urls") or []
    if channel.url and channel.url not in stash_urls:
        updates["urls"] = stash_urls + [channel.url]

    stash_image = stash_studio.get("image_path")
    if channel.performer_image_url and not stash_image:
        data_uri = await stash.download_image_data_uri(channel.performer_image_url)
        if data_uri:
            updates["image"] = data_uri

    stash_details = (stash_studio.get("details") or "").strip()
    if channel_description and not stash_details:
        updates["details"] = channel_description

    if updates:
        logger.info(
            "Pushing source data to Stash studio %s: %s",
            channel.stash_studio_id,
            list(updates.keys()),
        )
        await stash.update_studio(channel.stash_studio_id, **updates)


async def sync_channel_studio(
    channel: "Channel",
    db: "AsyncSession",
    stash: "StashClient",
    settings: "Settings",
) -> None:
    """Bidirectional studio sync between a channel and Stash.

    1. Enrich channel from yt-dlp source (name, thumbnail) when missing; get description.
    2. Find or create the Stash studio (by URL → name → create).
    3. Pull: Fetch full Stash studio data → store locally.
    4. Push: Send any source data Stash is missing (URL, image, details).

    On failure, logs a warning and does not raise.
    """
    try:
        logger.info(
            "sync_channel_studio START: channel=%s name=%r url=%s "
            "stash_studio_id=%s image_url=%s",
            channel.id, channel.name, channel.url,
            channel.stash_studio_id,
            "yes" if channel.performer_image_url else "no",
        )

        # --- Step 1: Enrich from yt-dlp and get description ---
        channel_description = await _enrich_channel_and_get_description(
            channel, settings
        )
        logger.info(
            "sync_channel_studio after enrich: channel=%s name=%r image_url=%s description=%s",
            channel.id, channel.name,
            "yes" if channel.performer_image_url else "no",
            "yes" if channel_description else "no",
        )

        # --- Step 2: Find or create Stash studio ---
        if not channel.stash_studio_id:
            if is_placeholder_name(channel.name):
                logger.info(
                    "Skipping Stash studio creation for channel %s — "
                    "name %r looks like a site/domain placeholder. "
                    "It will be created once a real name is available.",
                    channel.id,
                    channel.name,
                )
                return
            studio_id = await stash.find_or_create_studio_by_url(
                name=channel.name,
                url=channel.url,
                image_url=channel.performer_image_url,
                details=channel_description,
            )
            channel.stash_studio_id = str(studio_id) if studio_id is not None else None
            logger.info(
                "sync_channel_studio: channel=%s → stash_studio_id=%s",
                channel.id, channel.stash_studio_id,
            )

        # --- Step 3: Pull full data from Stash → local ---
        stash_studio = await _pull_studio_from_stash(channel, stash)

        # --- Step 4: Push source data → Stash (fill gaps only) ---
        if stash_studio:
            await _push_to_stash(
                channel, stash, stash_studio, channel_description
            )
            await _pull_studio_from_stash(channel, stash)

        logger.info(
            "sync_channel_studio DONE: channel=%s stash_studio_id=%s",
            channel.id, channel.stash_studio_id,
        )

    except Exception as e:
        logger.warning(
            "Studio sync failed for channel %s: %s",
            channel.id,
            e,
            exc_info=True,
        )
