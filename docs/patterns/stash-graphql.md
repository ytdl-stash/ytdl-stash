# Stash GraphQL Patterns

Reference patterns for how this project communicates with the Stash GraphQL API. Read this before modifying the Stash client module.

**Implementation note:** In `app/stash_client.py`, GraphQL query and mutation strings are defined as module-level constants (e.g. `_METADATA_SCAN_MUTATION`, `_FIND_SCENES_QUERY`) for readability and reuse; methods call `self._query(constant, variables)`.

---

## Client Setup

```python
# app/stash_client.py
import httpx

class StashClient:
    def __init__(self, url: str, api_key: str = ""):
        self.graphql_url = f"{url.rstrip('/')}/graphql"
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["ApiKey"] = api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "StashClient":
        self._client = httpx.AsyncClient(headers=self.headers, timeout=30.0)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        payload = {"query": query, "variables": variables or {}}
        if self._client:
            response = await self._client.post(self.graphql_url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.graphql_url, json=payload, headers=self.headers,
                )
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]
```

**Key points:**
- Stash uses the `ApiKey` header (not `Authorization: Bearer`).
- The GraphQL endpoint is always at `{stash_url}/graphql`.
- All methods are async (using httpx).
- Timeout is set to 30s to handle slow scan operations.
- Use as an async context manager (`async with StashClient(...) as c:`) to reuse connections across requests (especially important during polling).

---

## Trigger Metadata Scan

Tell Stash to scan specific file paths:

```python
async def trigger_scan(self, paths: list[str]) -> None:
    query = """
    mutation MetadataScan($input: ScanMetadataInput!) {
        metadataScan(input: $input)
    }
    """
    variables = {
        "input": {
            "paths": paths,
            "scanGenerateCovers": False,
            "scanGeneratePreviews": False,
            "scanGenerateSprites": False,
            "scanGeneratePhashes": False,
        }
    }
    await self._query(query, variables)
```

**Note**: We disable cover/preview/sprite/phash generation to speed up the scan. We only need the file fingerprinted (oshash + md5) and the scene created.

---

## Find Scene by oshash

Query Stash for a scene matching a specific file oshash fingerprint:

```python
async def find_scene_by_oshash(self, oshash: str) -> dict | None:
    query = """
    query FindScenes($filter: FindFilterType!, $scene_filter: SceneFilterType!) {
        findScenes(filter: $filter, scene_filter: $scene_filter) {
            scenes {
                id
                title
                files {
                    path
                    fingerprints {
                        type
                        value
                    }
                }
            }
        }
    }
    """
    variables = {
        "filter": {"per_page": 1},
        "scene_filter": {
            "oshash": {
                "value": oshash,
                "modifier": "EQUALS",
            }
        }
    }
    data = await self._query(query, variables)
    scenes = data["findScenes"]["scenes"]
    return scenes[0] if scenes else None
```

---

## Find Scene by Title

Query Stash for a scene by exact title (fallback when oshash lookup fails):

```python
async def find_scene_by_title(self, title: str) -> dict | None:
    variables = {
        "filter": {"per_page": 1},
        "scene_filter": {
            "title": {"value": title.strip(), "modifier": "EQUALS"}
        },
    }
    data = await self._query(_FIND_SCENES_QUERY, variables)
    scenes = data["findScenes"]["scenes"]
    return scenes[0] if scenes else None
```

---

## Find Job and Wait for Job

After `metadataScan` or `metadataGenerate`, poll job status until terminal state:

```python
_FIND_JOB_QUERY = """
query FindJob($input: FindJobInput!) {
    findJob(input: $input) {
        id
        status
        description
        progress
        error
    }
}
"""

async def find_job(self, job_id: str) -> dict | None:
    data = await self._query(_FIND_JOB_QUERY, {"input": {"id": job_id}})
    return data.get("findJob")

async def wait_for_job(self, job_id: str, poll_interval: float = 1.5, timeout: float = 300) -> dict:
    # Poll until status in {FINISHED, FAILED, CANCELLED, STOPPING}. Raises on failure/cancel.
    ...
```

**Rule**: Use `wait_for_job` after `trigger_scan` and `trigger_generate` instead of timeout-based polling.

---

## Find or Create Performer

```python
async def find_performer(self, name: str) -> str | None:
    query = """
    query FindPerformers($filter: FindFilterType!, $performer_filter: PerformerFilterType!) {
        findPerformers(filter: $filter, performer_filter: $performer_filter) {
            performers {
                id
                name
            }
        }
    }
    """
    variables = {
        "filter": {"per_page": 1},
        "performer_filter": {
            "name": {
                "value": name,
                "modifier": "EQUALS",
            }
        }
    }
    data = await self._query(query, variables)
    performers = data["findPerformers"]["performers"]
    return performers[0]["id"] if performers else None

async def create_performer(self, name: str) -> str:
    query = """
    mutation PerformerCreate($input: PerformerCreateInput!) {
        performerCreate(input: $input) {
            id
        }
    }
    """
    data = await self._query(query, {"input": {"name": name}})
    return data["performerCreate"]["id"]

async def find_or_create_performer(self, name: str) -> str:
    performer_id = await self.find_performer(name)
    if performer_id:
        return performer_id
    return await self.create_performer(name)
```

---

## Get Full Performer by ID

Fetch all performer metadata from Stash (used by bidirectional sync):

```python
async def get_performer(self, performer_id: str) -> dict | None:
    query = """
    query FindPerformer($id: ID!) {
        findPerformer(id: $id) {
            id name disambiguation urls gender birthdate ethnicity country
            eye_color hair_color height_cm weight measurements fake_tits
            career_length tattoos piercings alias_list details death_date
            image_path rating100 scene_count
        }
    }
    """
    data = await self._query(query, {"id": performer_id})
    return data.get("findPerformer")
```

---

## Update Performer

Push data to an existing Stash performer (fill-gaps pattern — only send non-None fields):

```python
async def update_performer(self, performer_id: str, **fields) -> None:
    query = """
    mutation PerformerUpdate($input: PerformerUpdateInput!) {
        performerUpdate(input: $input) { id }
    }
    """
    input_dict = {"id": performer_id}
    for key, value in fields.items():
        if value is not None:
            input_dict[key] = value
    if len(input_dict) <= 1:
        return
    await self._query(query, {"input": input_dict})
```

**Key fields for PerformerUpdateInput**: `name`, `disambiguation`, `urls`, `gender`, `birthdate`, `ethnicity`, `country`, `eye_color`, `hair_color`, `height_cm`, `weight`, `measurements`, `fake_tits`, `career_length`, `tattoos`, `piercings`, `alias_list`, `details`, `death_date`, `image` (URL string), `rating100`.

---

## Find or Create Studio

Name-only find-or-create (legacy, used by scrapers):

```python
async def find_studio(self, name: str) -> str | None:
    query = """
    query FindStudios($filter: FindFilterType!, $studio_filter: StudioFilterType!) {
        findStudios(filter: $filter, studio_filter: $studio_filter) {
            studios {
                id
                name
            }
        }
    }
    """
    variables = {
        "filter": {"per_page": 1},
        "studio_filter": {
            "name": {
                "value": name,
                "modifier": "EQUALS",
            }
        }
    }
    data = await self._query(query, variables)
    studios = data["findStudios"]["studios"]
    return studios[0]["id"] if studios else None

async def find_or_create_studio(self, name: str) -> str:
    studio_id = await self.find_studio(name)
    if studio_id:
        return studio_id
    return await self.create_studio(name)
```

### Find Studio by URL (channel sync)

Mirrors the performer URL lookup pattern. Use `StudioFilterType.url` with `INCLUDES` modifier:

```python
async def find_studio_by_url(self, url: str) -> dict | None:
    variables = {
        "filter": {"per_page": 1},
        "studio_filter": {
            "url": {
                "value": url,
                "modifier": "INCLUDES",
            }
        }
    }
    data = await self._query(_FIND_STUDIOS_BY_URL_QUERY, variables)
    studios = data["findStudios"]["studios"]
    return self._studio_dict(studios[0]) if studios else None
```

### Create Studio with Metadata

```python
async def create_studio_with_metadata(
    self,
    name: str,
    urls: list[str],
    image_url: str | None = None,
    details: str | None = None,
) -> str:
    input_dict = {"name": name, "urls": urls}
    if image_url:
        input_dict["image"] = image_url
    if details:
        input_dict["details"] = details
    data = await self._query(_STUDIO_CREATE_MUTATION, {"input": input_dict})
    return data["studioCreate"]["id"]
```

### Update Studio (gap-fill)

```python
async def update_studio(self, studio_id: str, **fields) -> None:
    input_dict = {"id": studio_id}
    for key, value in fields.items():
        if value is not None:
            input_dict[key] = value
    if len(input_dict) <= 1:
        return
    await self._query(_STUDIO_UPDATE_MUTATION, {"input": input_dict})
```

**Key fields for StudioUpdateInput**: `name`, `urls`, `parent_id`, `image` (URL string), `details`, `aliases`, `tag_ids`, `ignore_auto_tag`, `rating100`, `favorite`.

### Find-or-Create Studio by URL

Order: find by URL first, then by name (gap-fill if found), else create with metadata.

---

## Update Scene Metadata

Apply title, performers, studio, date, and URLs to a scene:

```python
async def update_scene(
    self,
    scene_id: str,
    title: str | None = None,
    urls: list[str] | None = None,
    date: str | None = None,       # "YYYY-MM-DD" format
    studio_id: str | None = None,
    performer_ids: list[str] | None = None,
) -> None:
    query = """
    mutation SceneUpdate($input: SceneUpdateInput!) {
        sceneUpdate(input: $input) {
            id
        }
    }
    """
    scene_input: dict = {"id": scene_id}
    if title is not None:
        scene_input["title"] = title
    if urls is not None:
        scene_input["urls"] = urls
    if date is not None:
        scene_input["date"] = date
    if studio_id is not None:
        scene_input["studio_id"] = studio_id
    if performer_ids is not None:
        scene_input["performer_ids"] = performer_ids

    await self._query(query, {"input": scene_input})
```

**Note**: Only include fields that have values. Stash will clear fields that are set to empty/null.

---

## Health Check

Verify connectivity to Stash:

```python
async def health_check(self) -> bool:
    try:
        query = "query { systemStatus { status } }"
        data = await self._query(query)
        return data["systemStatus"]["status"] == "OK"
    except Exception:
        return False
```

---

## Polling Pattern for Scene Discovery

After triggering a scan, the scene may not appear immediately. Poll with backoff:

```python
import asyncio
import time

async def wait_for_scene(self, oshash: str, timeout: float = 30, interval: float = 2) -> dict | None:
    """Poll Stash for a scene matching the oshash, with timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scene = await self.find_scene_by_oshash(oshash)
        if scene:
            return scene
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval, remaining))
    return None  # Timed out
```

**Rule**: Always use this polling pattern after `trigger_scan`. Never assume the scene exists immediately. Use `time.monotonic()` for wall-clock tracking so HTTP request time counts toward the timeout.

---

## Scrape Performer URL

Scrape performer metadata from a URL using Stash's configured scrapers:

```python
async def scrape_performer_url(self, url: str) -> dict | None:
    query = """
    query ScrapePerformerURL($url: String!) {
        scrapePerformerURL(url: $url) {
            name disambiguation urls gender birthdate ethnicity country
            eye_color hair_color height_cm weight measurements fake_tits
            career_length tattoos piercings details images
            tags { stored_id name }
        }
    }
    """
    data = await self._query(query, {"url": url})
    return data.get("scrapePerformerURL")
```

**Note**: Returns `None` if no scraper matches the URL. The `ScrapedPerformer` type has different field names/types than `Performer` / `PerformerUpdateInput`:
- Scraped `height` (String) → update `height_cm` (Int) — must rename and coerce
- Scraped `weight` (String) → update `weight` (Int) — must coerce
- Scraped `gender` (String) → update `gender` (GenderEnum string) — passes through directly

### Apply Scraped Performer (gap-fill)

After scraping, apply returned data to an existing performer without overwriting user-set fields:

```python
async def apply_scraped_performer(self, performer_id: str, scraped: dict) -> None:
    current = await self.get_performer(performer_id)
    if not current:
        return
    updates = {}
    for field in ("gender", "birthdate", "ethnicity", "country", ...):
        if scraped.get(field) and not current.get(field):
            updates[field] = scraped[field]
    # Numeric fields: coerce strings to int
    for num_field in ("height_cm", "weight"):
        if scraped.get(num_field) and not current.get(num_field):
            updates[num_field] = int(scraped[num_field])
    if updates:
        await self.update_performer(performer_id, **updates)
```

---

## Stash GraphQL Schema Notes

- **IDs are strings** in GraphQL, even though they are integers internally. Always pass them as `str`.
- **Dates** use `YYYY-MM-DD` format (ISO 8601 date only, no time).
- **Filter modifiers**: `EQUALS`, `NOT_EQUALS`, `INCLUDES`, `EXCLUDES`, `IS_NULL`, `NOT_NULL`, `MATCHES_REGEX`.
- **Pagination**: `FindFilterType` has `page`, `per_page`, `sort`, `direction`.
