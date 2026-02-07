# Phase 11: Performer Sync & Browser

**Status**: COMPLETE

## Overview

When a user adds a channel (subscription), the app should **automatically link or create the corresponding performer in Stash**. Channels in ytdl-stash map 1:1 to a performer's page on a tube site, so we can use the channel URL as the performer's URL in Stash. This phase also adds a **Performer Browser** page that shows all performers discovered across our tube site channels and whether we're currently watching (subscribed to) them or not.

## Prerequisites

- Phase 4 (Stash Client) — GraphQL performer queries and mutations
- Phase 7 (Routes) — channel CRUD routes
- Phase 8 (Web UI) — templates and HTMX patterns

## Goals

1. **Auto-link performer on channel add**: When a channel is added, look up the performer in Stash by URL. If found, link it. If not, create it in Stash with metadata from the tube site, then link it.
2. **Performer Browser UI**: A new page listing all performers from our tube site channels, showing subscription status (watched/not watched) and linking to their Stash profile.

---

## Deliverables

### Model changes

- [x] Add `stash_performer_id` (`String(50)`, nullable) to `Channel` model — stores the linked Stash performer ID
- [x] Add `performer_image_url` (`String(2048)`, nullable) to `Channel` model — cache the performer's avatar/thumbnail from the tube site
- [x] Add Alembic migration or `init_db` update to handle the new columns

### Stash Client: performer URL lookup

- [x] Add `_FIND_PERFORMER_BY_URL_QUERY` — GraphQL query using `PerformerFilterType` with `url` filter (`StringCriterionInput` with `INCLUDES` modifier) to find performers by URL
- [x] Add `find_performer_by_url(url: str) -> dict | None` method — returns performer `{id, name, url}` or `None`
- [x] Add `_PERFORMER_CREATE_WITH_META_MUTATION` — enhanced performer creation that accepts `name`, `urls`, and `image` fields
- [x] Add `create_performer_with_metadata(name: str, urls: list[str], image_url: str | None = None) -> str` method — creates a performer with full metadata, returns performer ID
- [x] Add `find_or_create_performer_by_url(name: str, url: str, image_url: str | None = None) -> str` method — finds by URL first, falls back to name match, then creates with metadata

### Pipeline: auto-link on channel add

- [x] Create `app/performer_sync.py` module with `sync_channel_performer(channel, db, stash, settings)` function
- [x] Flow:
  1. If `channel.stash_performer_id` is already set, skip (already linked)
  2. Call `stash.find_performer_by_url(channel.url)` — check if a performer with this channel URL exists in Stash
  3. If found: set `channel.stash_performer_id = performer["id"]`
  4. If not found: extract performer metadata (name from `channel.name`, URL from `channel.url`, image from `channel.performer_image_url`)
  5. Call `stash.create_performer_with_metadata(name, urls=[channel.url], image_url=image_url)`
  6. Set `channel.stash_performer_id = new_performer_id`
  7. Commit to DB
- [x] Call `sync_channel_performer()` from the channel creation route (`POST /channels`) after inserting the channel
- [x] Add a "Re-sync performer" action on existing channels that re-runs the link logic (useful if performer was manually deleted from Stash)

### Metadata extraction enhancement

- [x] During channel scan (or channel add), use yt-dlp to extract the channel page metadata to get performer avatar/thumbnail
- [x] Store the avatar URL in `channel.performer_image_url` so it can be passed to Stash on performer creation
- [x] Extract any additional metadata yt-dlp provides about the channel (bio/description if available) for future use

### Routes: Performer Browser

- [x] `GET /performers` — list all performers across all channels
  - Query all `Channel` rows, group by performer identity
  - For each performer show: name, site(s), channel URL(s), video count, enabled status (watched/not watched), Stash link status
  - Support filtering: "watched" (enabled channels), "not watched" (disabled channels), "all"
  - Support sorting: by name, by video count, by last checked
- [x] `GET /performers/{channel_id}` — detail view for a single performer
  - Show performer metadata (name, image, site, URL)
  - Show Stash link status and performer ID
  - List all videos from this channel with status breakdown
  - Quick actions: enable/disable watching, re-sync to Stash, open in Stash
- [x] `POST /performers/{channel_id}/sync-performer` — manually trigger performer sync only
- [x] `POST /performers/{channel_id}/sync-studio` — manually trigger studio sync only
- [x] `POST /performers/{channel_id}/sync` — manually trigger both performer and studio sync
- [x] `POST /performers/{channel_id}/toggle` — toggle the channel's `enabled` flag (start/stop watching)

### Templates: Performer Browser UI

- [x] `app/templates/performers/list.html` — performer grid/list view
  - Card-style layout with performer image (if available), name, site badge
  - Visual indicator for watched (green) vs not watched (gray)
  - Stash link indicator (linked icon or "not in Stash" label)
  - Filter bar: All / Watched / Not Watched
  - Click-through to detail view
- [x] `app/templates/performers/_card.html` — HTMX partial for a single performer card (for filtering)
- [x] `app/templates/performers/detail.html` — performer detail page
  - Performer info section with image, name, URL, site
  - Stash connection status with link to Stash performer page
  - Video table (reuse existing video table patterns)
  - Action buttons: Watch/Unwatch, Sync Performer, Sync Studio, Sync Both
- [x] Add "Performers" link to the navigation bar in `base.html`

---

## Patterns to Follow

- `docs/patterns/fastapi.md` — route structure, dependency injection
- `docs/patterns/sqlalchemy-async.md` — model changes, queries
- `docs/patterns/stash-graphql.md` — new GraphQL queries and mutations
- `docs/patterns/htmx.md` — partial templates, swap patterns
- `docs/recipes/add-database-field.md` — adding columns to models
- `docs/recipes/add-api-route.md` — new route module
- `docs/recipes/add-stash-query.md` — new GraphQL operations

## Key Implementation Notes

- **URL matching is the primary performer lookup strategy**. Stash performers have a `urls` field (list of strings). Searching by URL is more reliable than name matching because performer names can differ between tube sites and Stash (aliases, formatting, etc.).
- **Fall back to name match** if URL match fails. This handles the case where a performer already exists in Stash but without the tube site URL attached.
- **One channel = one performer**. Each channel in ytdl-stash represents a single performer's page. The Performer Browser groups channels by performer, but the underlying data model keeps the 1:1 channel-performer relationship.
- **Performer sync is non-blocking**. If Stash is unreachable when a channel is added, the channel is still created — performer sync can be retried later via the UI or the next scheduler run.
- **Use `asyncio.to_thread()`** for any yt-dlp calls to extract channel metadata (they are blocking).
- **HTMX partials prefixed with `_`**, do NOT extend `base.html`.
- Use `Depends(get_db)` and `Depends(get_settings)` in routes — never instantiate directly.

## Stash GraphQL Reference

### Find performer by URL
```graphql
query FindPerformers($filter: FindFilterType!, $performer_filter: PerformerFilterType!) {
    findPerformers(filter: $filter, performer_filter: $performer_filter) {
        performers {
            id
            name
            urls
            image_path
        }
    }
}
# Variables:
# performer_filter: { url: { value: "pornhub.com/model/someuser", modifier: "INCLUDES" } }
```

### Create performer with metadata
```graphql
mutation PerformerCreate($input: PerformerCreateInput!) {
    performerCreate(input: $input) {
        id
    }
}
# Input: { name: "Performer Name", urls: ["https://..."], image: "https://..." }
```

## Acceptance Criteria

- [x] Adding a channel automatically creates/links a performer in Stash
- [x] The performer in Stash has the channel URL set
- [x] If the performer already exists in Stash (by URL), it is linked without creating a duplicate
- [x] `channel.stash_performer_id` is populated after successful sync
- [x] Performer Browser page lists all performers with correct watched/not-watched status
- [x] Performer detail page shows videos and Stash link status
- [x] Toggle watch/unwatch updates the channel's `enabled` flag via HTMX
- [x] Manual "Sync Performer", "Sync Studio", and "Sync Both" buttons work for re-linking performers and studios
- [x] Performer sync failures are graceful — channel is still usable, error is logged

## Deviations

(none yet)
