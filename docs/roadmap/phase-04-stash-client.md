# Phase 4: Stash GraphQL Client

**Status**: COMPLETE

## Prerequisites

- Phase 1 complete (config.py for `stash_url` and `stash_api_key`)

## Deliverables

- [x] `app/stash_client.py` — `StashClient` class with all methods

### StashClient class

Constructor: `__init__(self, url: str, api_key: str = "")`
- Sets `self.graphql_url` and `self.headers` (including `ApiKey` header)

Private method: `_query(self, query, variables) -> dict`
- Uses `httpx.AsyncClient` to POST to the GraphQL endpoint
- Handles errors, raises `RuntimeError` on GraphQL errors
- 30s timeout

### Public methods

| Method | Purpose | Returns |
|--------|---------|---------|
| `trigger_scan(paths)` | `metadataScan` mutation | None |
| `find_scene_by_oshash(oshash)` | Query scenes by fingerprint | `dict \| None` |
| `find_performer(name)` | Query performers by exact name | `str \| None` (ID) |
| `create_performer(name)` | `performerCreate` mutation | `str` (ID) |
| `find_or_create_performer(name)` | Combines find + create | `str` (ID) |
| `find_studio(name)` | Query studios by exact name | `str \| None` (ID) |
| `create_studio(name)` | `studioCreate` mutation | `str` (ID) |
| `find_or_create_studio(name)` | Combines find + create | `str` (ID) |
| `update_scene(scene_id, ...)` | `sceneUpdate` mutation | None |
| `wait_for_scene(oshash, timeout, interval)` | Poll for scene after scan | `dict \| None` |
| `health_check()` | `systemStatus` query | `bool` |

## Patterns to Follow

- `docs/patterns/stash-graphql.md` — **READ THIS FIRST**. Contains complete implementations for every method, exact GraphQL queries, polling pattern, and schema notes.
- `docs/adr/004-oshash-scene-matching.md` — why oshash-based matching.
- `docs/glossary.md` — definitions of oshash, scene, performer, studio.

## Key Implementation Notes

- Stash uses `ApiKey` header, NOT `Authorization: Bearer`.
- All Stash IDs are **strings** in GraphQL (even though internally integers).
- Dates use `YYYY-MM-DD` format.
- `trigger_scan` disables cover/preview/sprite/phash generation for speed.
- `wait_for_scene` polls every 2s for up to 30s — do not assume the scene exists immediately after scan.
- Filter modifier for exact match is `"EQUALS"`.

## Acceptance Criteria

- [x] `StashClient` instantiates with URL and optional API key
- [x] `_query()` handles HTTP errors and GraphQL errors cleanly
- [x] `trigger_scan()` sends correct `ScanMetadataInput` with generation flags off
- [x] `find_scene_by_oshash()` returns scene dict or None
- [x] `find_or_create_performer()` is idempotent (doesn't duplicate)
- [x] `find_or_create_studio()` is idempotent
- [x] `update_scene()` only includes non-None fields in the input
- [x] `wait_for_scene()` polls with interval and respects timeout
- [x] `health_check()` returns True/False without raising
- [x] All methods are async

## Deviations

(none yet)
