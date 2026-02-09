"""Performer sync: bidirectional sync between channels and Stash performers.

Pull: Fetch full Stash performer data → store locally in channel.stash_performer_data.
Push: If our source (yt-dlp) has data Stash lacks (image, URL), send it to Stash.
"""

import logging
from typing import TYPE_CHECKING

from app.downloader import _DOMAIN_RE, async_extract_channel_metadata

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config import Settings
    from app.models import Channel
    from app.stash_client import StashClient

logger = logging.getLogger(__name__)


def is_placeholder_name(name: str) -> bool:
    """Return True if the channel name looks like a placeholder (bare domain, 'unknown', or empty)."""
    stripped = name.strip().lower()
    if not stripped or stripped == "unknown":
        return True
    return bool(_DOMAIN_RE.match(stripped))


async def _enrich_from_source(
    channel: "Channel",
    settings: "Settings",
) -> None:
    """Fill in channel name and thumbnail from yt-dlp when missing."""
    needs_name = is_placeholder_name(channel.name)
    needs_thumb = not channel.performer_image_url

    if not needs_name and not needs_thumb:
        return

    try:
        meta = await async_extract_channel_metadata(channel.url, settings)
        if needs_name and meta.get("name"):
            channel.name = meta["name"]
        if needs_thumb and meta.get("thumbnail"):
            channel.performer_image_url = meta["thumbnail"]
    except Exception as e:
        logger.debug(
            "Could not extract channel metadata for %s: %s",
            channel.url,
            e,
        )


async def _pull_from_stash(
    channel: "Channel",
    stash: "StashClient",
    stash_base_url: str,
) -> dict | None:
    """Fetch full performer data from Stash and store it locally.

    Returns the Stash performer dict (or None if not found).
    """
    if not channel.stash_performer_id:
        return None

    performer = await stash.get_performer(channel.stash_performer_id)
    if not performer:
        logger.warning(
            "Stash performer %s not found for channel %s",
            channel.stash_performer_id,
            channel.id,
        )
        return None

    # Store the full Stash performer record locally
    channel.stash_performer_data = performer

    # Overwrite channel name with the Stash performer name (Stash is authoritative).
    stash_name = (performer.get("name") or "").strip()
    if stash_name:
        channel.name = stash_name

    # Keep performer_image_url in sync — prefer Stash image if available.
    # image_path from Stash is a relative path like /performer/1/image?...
    # so we need to prepend the Stash base URL to make it usable.
    image_path = performer.get("image_path")
    if image_path:
        if image_path.startswith("http"):
            channel.performer_image_url = image_path
        else:
            channel.performer_image_url = f"{stash_base_url.rstrip('/')}{image_path}"

    logger.debug(
        "Pulled Stash performer data for channel %s (performer %s)",
        channel.id,
        channel.stash_performer_id,
    )
    return performer


async def _push_to_stash(
    channel: "Channel",
    stash: "StashClient",
    stash_performer: dict,
) -> None:
    """Push source data to Stash for any fields the Stash performer is missing.

    We only fill in gaps — never overwrite data the user set in Stash.
    """
    if not channel.stash_performer_id:
        return

    updates: dict = {}

    # URL: add our channel URL if Stash performer has no URLs
    stash_urls = stash_performer.get("urls") or []
    if channel.url and channel.url not in stash_urls:
        updates["urls"] = stash_urls + [channel.url]

    # Image: send our thumbnail if Stash performer has no image.
    # Stash returns image_path = None when no image is set.
    stash_image = stash_performer.get("image_path")
    if channel.performer_image_url and not stash_image:
        data_uri = await stash.download_image_data_uri(channel.performer_image_url)
        if data_uri:
            updates["image"] = data_uri

    # Name: if Stash performer has a placeholder/empty name and we have a real one
    stash_name = (stash_performer.get("name") or "").strip()
    if not stash_name and channel.name and not is_placeholder_name(channel.name):
        updates["name"] = channel.name

    if updates:
        logger.info(
            "Pushing source data to Stash performer %s: %s",
            channel.stash_performer_id,
            list(updates.keys()),
        )
        await stash.update_performer(channel.stash_performer_id, **updates)


async def sync_channel_performer(
    channel: "Channel",
    db: "AsyncSession",
    stash: "StashClient",
    settings: "Settings",
) -> None:
    """Bidirectional performer sync between a channel and Stash.

    1. Enrich channel from yt-dlp source (name, thumbnail) when missing.
    2. Find or create the Stash performer (by URL → name → create).
    3. Pull: Fetch full Stash performer data → store locally.
    4. Push: Send any source data Stash is missing (image, URL).

    On failure, logs a warning and does not raise.
    """
    try:
        logger.info(
            "sync_channel_performer START: channel=%s name=%r url=%s "
            "stash_performer_id=%s image_url=%s",
            channel.id, channel.name, channel.url,
            channel.stash_performer_id,
            "yes" if channel.performer_image_url else "no",
        )

        # --- Step 1: Enrich from yt-dlp source ---
        await _enrich_from_source(channel, settings)
        logger.info(
            "sync_channel_performer after enrich: channel=%s name=%r image_url=%s",
            channel.id, channel.name,
            "yes" if channel.performer_image_url else "no",
        )

        # --- Step 2: Find or create Stash performer ---
        if not channel.stash_performer_id:
            if is_placeholder_name(channel.name):
                logger.info(
                    "Skipping Stash performer creation for channel %s — "
                    "name %r looks like a site/domain placeholder. "
                    "It will be created once a real name is available.",
                    channel.id,
                    channel.name,
                )
                return
            performer_id = await stash.find_or_create_performer_by_url(
                name=channel.name,
                url=channel.url,
                image_url=channel.performer_image_url,
            )
            channel.stash_performer_id = performer_id
            logger.info(
                "sync_channel_performer: channel=%s → stash_performer_id=%s",
                channel.id, performer_id,
            )

        # --- Step 3: Pull full data from Stash → local ---
        stash_base_url = settings.stash_url
        stash_performer = await _pull_from_stash(channel, stash, stash_base_url)

        # --- Step 4: Push source data → Stash (fill gaps only) ---
        if stash_performer:
            await _push_to_stash(channel, stash, stash_performer)

            # Re-pull after push so local copy reflects the updates
            await _pull_from_stash(channel, stash, stash_base_url)

        logger.info(
            "sync_channel_performer DONE: channel=%s stash_performer_id=%s",
            channel.id, channel.stash_performer_id,
        )

    except Exception as e:
        logger.warning(
            "Performer sync failed for channel %s: %s",
            channel.id,
            e,
            exc_info=True,
        )
