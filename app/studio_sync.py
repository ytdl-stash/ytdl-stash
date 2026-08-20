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
) -> tuple[str | None, str | None]:
    """Fill in channel name and thumbnail from yt-dlp when missing.

    Returns (description, source_image_url).  The source_image_url is the
    original thumbnail URL from yt-dlp — it must NOT be taken from
    ``channel.performer_image_url`` because performer sync may have already
    overwritten that field with a Stash performer image path that requires
    API-key authentication to download.
    """
    try:
        meta = await async_extract_channel_metadata(channel.url, settings)
        if is_placeholder_name(channel.name) and meta.get("name"):
            channel.name = meta["name"]
        source_image_url = meta.get("thumbnail")
        if not channel.performer_image_url and source_image_url:
            channel.performer_image_url = source_image_url
        desc = meta.get("description")
        return (str(desc).strip() if desc else None, source_image_url)
    except Exception as e:
        logger.debug(
            "Could not extract channel metadata for %s: %s",
            channel.url,
            e,
        )
        return (None, None)


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
    source_image_url: str | None = None,
) -> None:
    """Push source data to Stash for any fields the Stash studio is missing.

    We only fill in gaps — never overwrite data the user set in Stash. Image
    candidates are tried in order: the yt-dlp source thumbnail first, then the
    channel's performer image (which the client can fetch from its own Stash
    host with the ApiKey).
    """
    if not channel.stash_studio_id:
        return

    image_candidates = [
        u for u in (source_image_url, channel.performer_image_url) if u
    ]
    await stash._gap_fill_studio_url_image_details(
        stash_studio, channel.url, image_candidates, channel_description
    )


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
        channel_description, source_image_url = await _enrich_channel_and_get_description(
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
                image_url=[
                    u
                    for u in (source_image_url, channel.performer_image_url)
                    if u
                ],
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
                channel, stash, stash_studio, channel_description, source_image_url
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
