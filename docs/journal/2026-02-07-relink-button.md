# Re-link Button - Feb 7, 2026

## Overview

Added a **Re-link** button to the performer (channel) detail and list views. It clears the channel's Stash performer and studio links, then re-runs the URL-based lookup. This addresses the case where the user has added the channel URL to a different performer/studio in Stash (or merged entities) and wants the channel to point to the new match.

## Implementation Approach

- New route `POST /performers/{channel_id}/relink` clears `stash_performer_id`, `stash_studio_id`, `stash_performer_data`, and `stash_studio_data`, then runs `sync_channel_performer` and `sync_channel_studio`. Because IDs are now empty, the sync logic runs `find_or_create_*_by_url`, which looks up by channel URL in Stash.
- Re-link button shown only when the channel has at least one Stash link (performer or studio). Uses `hx-confirm` since re-linking changes which Stash entities the channel points to.
- Button styled as `btn-ghost` to distinguish from primary Sync actions.

## Changes Made

### Files Modified

- `app/routes/performers.py` — add `performer_relink` route
- `app/templates/performers/_detail_card.html` — add Re-link form (with confirmation)
- `app/templates/performers/_card.html` — add Re-link form (with confirmation)
