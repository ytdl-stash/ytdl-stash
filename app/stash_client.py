"""Async GraphQL client for the Stash API. Used by the pipeline and settings routes."""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GraphQL query/mutation strings (module-level for readability and reuse)
# ---------------------------------------------------------------------------

_SYSTEM_STATUS_QUERY = """
query {
    systemStatus {
        status
    }
}
"""

_METADATA_SCAN_MUTATION = """
mutation MetadataScan($input: ScanMetadataInput!) {
    metadataScan(input: $input)
}
"""

_FIND_SCENES_QUERY = """
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
            paths {
                screenshot
            }
        }
    }
}
"""

_FIND_SCENE_BY_ID_QUERY = """
query FindScene($id: ID!) {
    findScene(id: $id) {
        id
        title
        details
        date
        code
        urls
        paths {
            screenshot
        }
        studio {
            id
            name
        }
        performers {
            id
            name
        }
        tags {
            id
            name
        }
        cover
    }
}
"""

_FIND_PERFORMERS_QUERY = """
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
"""

_FIND_PERFORMER_BY_URL_QUERY = """
query FindPerformersByUrl($filter: FindFilterType!, $performer_filter: PerformerFilterType!) {
    findPerformers(filter: $filter, performer_filter: $performer_filter) {
        performers {
            id
            name
            urls
            image_path
        }
    }
}
"""

_PERFORMER_CREATE_MUTATION = """
mutation PerformerCreate($input: PerformerCreateInput!) {
    performerCreate(input: $input) {
        id
    }
}
"""

_PERFORMER_CREATE_WITH_META_MUTATION = """
mutation PerformerCreate($input: PerformerCreateInput!) {
    performerCreate(input: $input) {
        id
    }
}
"""

_FIND_PERFORMER_BY_ID_QUERY = """
query FindPerformer($id: ID!) {
    findPerformer(id: $id) {
        id
        name
        disambiguation
        urls
        gender
        birthdate
        ethnicity
        country
        eye_color
        hair_color
        height_cm
        weight
        measurements
        fake_tits
        career_length
        tattoos
        piercings
        alias_list
        details
        death_date
        image_path
        rating100
        scene_count
    }
}
"""

_PERFORMER_UPDATE_MUTATION = """
mutation PerformerUpdate($input: PerformerUpdateInput!) {
    performerUpdate(input: $input) {
        id
    }
}
"""

_FIND_STUDIOS_QUERY = """
query FindStudios($filter: FindFilterType!, $studio_filter: StudioFilterType!) {
    findStudios(filter: $filter, studio_filter: $studio_filter) {
        studios {
            id
            name
        }
    }
}
"""

_STUDIO_CREATE_MUTATION = """
mutation StudioCreate($input: StudioCreateInput!) {
    studioCreate(input: $input) {
        id
    }
}
"""

_SCENE_UPDATE_MUTATION = """
mutation SceneUpdate($input: SceneUpdateInput!) {
    sceneUpdate(input: $input) {
        id
    }
}
"""

_SCRAPE_SCENE_URL_QUERY = """
query ScrapeSceneURL($url: String!) {
    scrapeSceneURL(url: $url) {
        title
        code
        details
        url
        urls
        date
        image
        studio {
            stored_id
            name
        }
        tags {
            stored_id
            name
        }
        performers {
            stored_id
            name
        }
    }
}
"""

_METADATA_GENERATE_MUTATION = """
mutation MetadataGenerate($input: GenerateMetadataInput!) {
    metadataGenerate(input: $input)
}
"""

_FIND_TAGS_QUERY = """
query FindTags($filter: FindFilterType!, $tag_filter: TagFilterType!) {
    findTags(filter: $filter, tag_filter: $tag_filter) {
        tags {
            id
            name
        }
    }
}
"""

_TAG_CREATE_MUTATION = """
mutation TagCreate($input: TagCreateInput!) {
    tagCreate(input: $input) {
        id
    }
}
"""


def _normalize_performer_name(name: str) -> str:
    """Normalize whitespace in performer names for consistent lookups."""
    return " ".join(name.split())


# Process-global locks so that concurrent workers (each with their own StashClient)
# serialize find-or-create for the same performer/studio/tag name.
_performer_locks: dict[str, asyncio.Lock] = {}
_studio_locks: dict[str, asyncio.Lock] = {}
_tag_locks: dict[str, asyncio.Lock] = {}
# Protects "get or create" of the above locks so two tasks don't race and create two Lock instances.
_lock_dict_meta: asyncio.Lock = asyncio.Lock()


class StashClient:
    """Async client for Stash's GraphQL API. Uses ApiKey header and 30s timeout.

    Use as an async context manager to share a single httpx connection pool::

        async with StashClient(url, api_key) as client:
            await client.health_check()

    Or instantiate directly (a per-request client is created each time, less efficient).
    """

    def __init__(self, url: str, api_key: str = "") -> None:
        self.graphql_url = f"{url.rstrip('/')}/graphql"
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self.headers["ApiKey"] = api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "StashClient":
        self._client = httpx.AsyncClient(
            headers=self.headers, timeout=30.0
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Send a GraphQL request. Raises on HTTP or GraphQL errors. Returns data dict."""
        payload = {"query": query, "variables": variables or {}}
        try:
            if self._client:
                response = await self._client.post(self.graphql_url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        self.graphql_url,
                        json=payload,
                        headers=self.headers,
                    )
            response.raise_for_status()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Cannot connect to Stash at {self.graphql_url!r}. Is it running? {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise RuntimeError("Stash request timed out after 30s") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise RuntimeError(
                    "Stash authentication failed. Check YTDL_STASH_API_KEY"
                ) from e
            raise RuntimeError(f"Stash HTTP error: {e}") from e

        data = response.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    async def health_check(self) -> bool:
        """Verify connectivity to Stash. Returns True if system status is OK."""
        try:
            data = await self._query(_SYSTEM_STATUS_QUERY)
            ok = data["systemStatus"]["status"] == "OK"
            if not ok:
                logger.warning("Stash health check: status=%s", data["systemStatus"].get("status"))
            return ok
        except Exception as e:
            logger.debug("Stash health check failed: %s", e)
            return False

    async def trigger_scan(self, paths: list[str]) -> None:
        """Tell Stash to scan the given file paths. Disables cover/preview/sprite/phash generation."""
        logger.info("Stash: triggering scan for %d path(s): %s", len(paths), paths)
        variables = {
            "input": {
                "paths": paths,
                "scanGenerateCovers": False,
                "scanGeneratePreviews": False,
                "scanGenerateSprites": False,
                "scanGeneratePhashes": False,
            }
        }
        await self._query(_METADATA_SCAN_MUTATION, variables)

    async def find_scene_by_oshash(self, oshash: str) -> dict | None:
        """Find a scene by file oshash fingerprint. Returns scene dict or None."""
        variables = {
            "filter": {"per_page": 1},
            "scene_filter": {
                "oshash": {
                    "value": oshash,
                    "modifier": "EQUALS",
                }
            },
        }
        data = await self._query(_FIND_SCENES_QUERY, variables)
        scenes = data["findScenes"]["scenes"]
        return scenes[0] if scenes else None

    async def wait_for_scene(
        self, oshash: str, timeout: float = 30, interval: float = 2
    ) -> dict | None:
        """Poll Stash for a scene matching the oshash. Returns scene dict or None on timeout."""
        logger.info("Stash: waiting for scene with oshash=%s (timeout=%ss)", oshash, timeout)
        deadline = time.monotonic() + timeout
        poll_count = 0
        while time.monotonic() < deadline:
            scene = await self.find_scene_by_oshash(oshash)
            poll_count += 1
            if scene:
                logger.info("Stash: scene found after %d poll(s) for oshash=%s", poll_count, oshash)
                return scene
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(interval, remaining))
        logger.warning("Stash: scene not found after %d poll(s) for oshash=%s (timed out)", poll_count, oshash)
        return None

    async def find_scene_by_id(self, scene_id: str) -> dict | None:
        """Fetch a scene by Stash ID. Returns full scene dict or None."""
        data = await self._query(_FIND_SCENE_BY_ID_QUERY, {"id": scene_id})
        return data.get("findScene")

    def _performer_dict(self, p: dict) -> dict:
        """Build {id, name, urls, image_path} from GraphQL performer result."""
        return {
            "id": p["id"],
            "name": p.get("name") or "",
            "urls": p.get("urls") or [],
            "image_path": p.get("image_path"),
        }

    async def find_performer(self, name: str) -> dict | None:
        """Find a performer by exact name. Returns {id, name, urls, image_path} or None."""
        variables = {
            "filter": {"per_page": 1},
            "performer_filter": {
                "name": {
                    "value": name,
                    "modifier": "EQUALS",
                }
            },
        }
        data = await self._query(_FIND_PERFORMERS_QUERY, variables)
        performers = data["findPerformers"]["performers"]
        if not performers:
            return None
        return self._performer_dict(performers[0])

    async def find_performer_by_alias(self, name: str) -> dict | None:
        """Find a performer where name appears in alias_list. Returns {id, name, urls, image_path} or None."""
        variables = {
            "filter": {"per_page": 1},
            "performer_filter": {
                "aliases": {
                    "value": name,
                    "modifier": "EQUALS",
                }
            },
        }
        data = await self._query(_FIND_PERFORMERS_QUERY, variables)
        performers = data["findPerformers"]["performers"]
        if not performers:
            return None
        return self._performer_dict(performers[0])

    async def create_performer(self, name: str) -> str:
        """Create a performer. Returns the new performer's ID."""
        name = _normalize_performer_name(name)
        data = await self._query(_PERFORMER_CREATE_MUTATION, {"input": {"name": name}})
        return data["performerCreate"]["id"]

    async def find_or_create_performer(self, name: str) -> str:
        """Find performer by name or create. Returns performer ID."""
        name = _normalize_performer_name(name)
        key = name.lower()
        async with _lock_dict_meta:
            if key not in _performer_locks:
                _performer_locks[key] = asyncio.Lock()
            lock = _performer_locks[key]
        async with lock:
            p = await self.find_performer(name)
            if p:
                logger.debug("Stash: found existing performer '%s' (id=%s)", name, p["id"])
                return p["id"]
            p = await self.find_performer_by_alias(name)
            if p:
                logger.debug("Stash: found existing performer by alias '%s' (id=%s)", name, p["id"])
                return p["id"]
            logger.info("Stash: creating new performer '%s'", name)
            return await self.create_performer(name)

    async def find_performer_by_url(self, url: str) -> dict | None:
        """Find a performer by URL (INCLUDES match on performer urls). Returns {id, name, urls} or None."""
        variables = {
            "filter": {"per_page": 1},
            "performer_filter": {
                "url": {
                    "value": url,
                    "modifier": "INCLUDES",
                }
            },
        }
        data = await self._query(_FIND_PERFORMER_BY_URL_QUERY, variables)
        performers = data["findPerformers"]["performers"]
        if not performers:
            return None
        p = performers[0]
        return {"id": p["id"], "name": p.get("name") or "", "urls": p.get("urls") or []}

    async def create_performer_with_metadata(
        self,
        name: str,
        urls: list[str],
        image_url: str | None = None,
    ) -> str:
        """Create a performer with name, urls, and optional image URL. Returns the new performer's ID."""
        name = _normalize_performer_name(name)
        input_dict: dict = {"name": name, "urls": urls}
        if image_url:
            input_dict["image"] = image_url
        data = await self._query(_PERFORMER_CREATE_WITH_META_MUTATION, {"input": input_dict})
        return data["performerCreate"]["id"]

    async def _gap_fill_performer_url_image(
        self, performer: dict, url: str, image_url: str | None
    ) -> None:
        """Add URL and optionally image to an existing performer if missing."""
        updates: dict = {}
        stash_urls = performer.get("urls") or []
        if url and url not in stash_urls:
            updates["urls"] = stash_urls + [url]
        stash_image = performer.get("image_path")
        if image_url and not stash_image:
            updates["image"] = image_url
        if updates:
            logger.info(
                "Stash: gap-filling performer %s with %s",
                performer["id"],
                list(updates.keys()),
            )
            # update_performer expects **fields; need to call with id in input
            await self.update_performer(performer["id"], **updates)

    async def find_or_create_performer_by_url(
        self,
        name: str,
        url: str,
        image_url: str | None = None,
    ) -> str:
        """Find performer by URL first, then by name, then create with metadata. Returns performer ID."""
        name = _normalize_performer_name(name)
        key = name.lower()
        async with _lock_dict_meta:
            if key not in _performer_locks:
                _performer_locks[key] = asyncio.Lock()
            lock = _performer_locks[key]
        async with lock:
            by_url = await self.find_performer_by_url(url)
            if by_url:
                return by_url["id"]
            by_name = await self.find_performer(name)
            if by_name:
                await self._gap_fill_performer_url_image(by_name, url, image_url)
                return by_name["id"]
            by_alias = await self.find_performer_by_alias(name)
            if by_alias:
                await self._gap_fill_performer_url_image(by_alias, url, image_url)
                return by_alias["id"]
            return await self.create_performer_with_metadata(
                name=name,
                urls=[url],
                image_url=image_url,
            )

    async def get_performer(self, performer_id: str) -> dict | None:
        """Fetch full performer data by Stash ID. Returns the full performer dict or None."""
        data = await self._query(_FIND_PERFORMER_BY_ID_QUERY, {"id": performer_id})
        return data.get("findPerformer")

    async def update_performer(self, performer_id: str, **fields: object) -> None:
        """Update a Stash performer. Only non-None keyword args are sent.

        Supported fields match PerformerUpdateInput: name, disambiguation, urls,
        gender, birthdate, ethnicity, country, eye_color, hair_color, height_cm,
        weight, measurements, fake_tits, career_length, tattoos, piercings,
        alias_list, details, death_date, image (url string), rating100.
        """
        input_dict: dict = {"id": performer_id}
        for key, value in fields.items():
            if value is not None:
                input_dict[key] = value
        if len(input_dict) <= 1:
            return  # Nothing to update
        await self._query(_PERFORMER_UPDATE_MUTATION, {"input": input_dict})

    async def find_studio(self, name: str) -> str | None:
        """Find a studio by exact name. Returns studio ID or None."""
        variables = {
            "filter": {"per_page": 1},
            "studio_filter": {
                "name": {
                    "value": name,
                    "modifier": "EQUALS",
                }
            },
        }
        data = await self._query(_FIND_STUDIOS_QUERY, variables)
        studios = data["findStudios"]["studios"]
        return studios[0]["id"] if studios else None

    async def create_studio(self, name: str) -> str:
        """Create a studio. Returns the new studio's ID."""
        data = await self._query(_STUDIO_CREATE_MUTATION, {"input": {"name": name}})
        return data["studioCreate"]["id"]

    async def find_or_create_studio(self, name: str) -> str:
        """Find studio by name or create. Returns studio ID."""
        key = name.strip().lower()
        async with _lock_dict_meta:
            if key not in _studio_locks:
                _studio_locks[key] = asyncio.Lock()
            lock = _studio_locks[key]
        async with lock:
            studio_id = await self.find_studio(name)
            if studio_id:
                logger.debug("Stash: found existing studio '%s' (id=%s)", name, studio_id)
                return studio_id
            logger.info("Stash: creating new studio '%s'", name)
            return await self.create_studio(name)

    async def update_scene(
        self,
        scene_id: str,
        title: str | None = None,
        urls: list[str] | None = None,
        date: str | None = None,
        studio_id: str | None = None,
        performer_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        details: str | None = None,
        cover_image: str | None = None,
    ) -> None:
        """Update scene metadata. Only non-None fields are sent (Stash clears nulls)."""
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
        if tag_ids is not None:
            scene_input["tag_ids"] = tag_ids
        if details is not None:
            scene_input["details"] = details
        if cover_image is not None:
            scene_input["cover_image"] = cover_image
        await self._query(_SCENE_UPDATE_MUTATION, {"input": scene_input})

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def find_tag(self, name: str) -> str | None:
        """Find a tag by exact name. Returns tag ID or None."""
        variables = {
            "filter": {"per_page": 1},
            "tag_filter": {
                "name": {
                    "value": name,
                    "modifier": "EQUALS",
                }
            },
        }
        data = await self._query(_FIND_TAGS_QUERY, variables)
        tags = data["findTags"]["tags"]
        return tags[0]["id"] if tags else None

    async def create_tag(self, name: str) -> str:
        """Create a tag. Returns the new tag's ID."""
        data = await self._query(_TAG_CREATE_MUTATION, {"input": {"name": name}})
        return data["tagCreate"]["id"]

    async def find_or_create_tag(self, name: str) -> str:
        """Find tag by name or create. Returns tag ID."""
        key = name.strip().lower()
        async with _lock_dict_meta:
            if key not in _tag_locks:
                _tag_locks[key] = asyncio.Lock()
            lock = _tag_locks[key]
        async with lock:
            tag_id = await self.find_tag(name)
            if tag_id:
                logger.debug("Stash: found existing tag '%s' (id=%s)", name, tag_id)
                return tag_id
            logger.info("Stash: creating new tag '%s'", name)
            return await self.create_tag(name)

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------

    async def scrape_scene_url(self, url: str) -> dict | None:
        """Scrape scene metadata from a URL using Stash's configured scrapers.

        Returns the scraped scene dict or None if no scraper matched / no data returned.
        """
        try:
            data = await self._query(_SCRAPE_SCENE_URL_QUERY, {"url": url})
            scraped = data.get("scrapeSceneURL")
            if not scraped:
                logger.info("Stash: no scraper returned data for URL %s", url)
                return None
            logger.info("Stash: scraper returned data for URL %s", url)
            return scraped
        except RuntimeError as e:
            # GraphQL errors (e.g. no matching scraper) are non-fatal
            logger.warning("Stash: scrapeSceneURL failed for %s: %s", url, e)
            return None

    async def apply_scraped_scene(
        self,
        scene_id: str,
        scraped: dict,
        existing_performer_ids: list[str] | None = None,
        existing_studio_id: str | None = None,
    ) -> None:
        """Apply scraped metadata to an existing scene (gap-fill only).

        Only sets fields the scraper returned. Tags are resolved (find-or-create).
        Performers and studio from the scraper are only applied if we don't already
        have them set from yt-dlp.
        """
        update_kwargs: dict = {}

        # Details / description — we never set this from yt-dlp, so always apply
        if scraped.get("details"):
            update_kwargs["details"] = scraped["details"]

        # Cover image
        if scraped.get("image"):
            update_kwargs["cover_image"] = scraped["image"]

        # Tags — resolve each to a Stash tag ID
        scraped_tags = scraped.get("tags") or []
        if scraped_tags:
            tag_ids: list[str] = []
            for tag in scraped_tags:
                if tag.get("stored_id"):
                    tag_ids.append(tag["stored_id"])
                elif tag.get("name"):
                    tid = await self.find_or_create_tag(tag["name"])
                    tag_ids.append(tid)
            if tag_ids:
                update_kwargs["tag_ids"] = tag_ids

        # Performers — only add from scraper if we don't already have them
        if not existing_performer_ids:
            scraped_performers = scraped.get("performers") or []
            if scraped_performers:
                pids: list[str] = []
                for perf in scraped_performers:
                    if perf.get("stored_id"):
                        pids.append(perf["stored_id"])
                    elif perf.get("name"):
                        pid = await self.find_or_create_performer(perf["name"])
                        pids.append(pid)
                if pids:
                    update_kwargs["performer_ids"] = pids

        # Studio — only set from scraper if we don't already have one
        if not existing_studio_id:
            scraped_studio = scraped.get("studio")
            if scraped_studio:
                if scraped_studio.get("stored_id"):
                    update_kwargs["studio_id"] = scraped_studio["stored_id"]
                elif scraped_studio.get("name"):
                    sid = await self.find_or_create_studio(scraped_studio["name"])
                    update_kwargs["studio_id"] = sid

        if update_kwargs:
            logger.info(
                "Stash: applying scraped data to scene %s (fields: %s)",
                scene_id, list(update_kwargs.keys()),
            )
            await self.update_scene(scene_id=scene_id, **update_kwargs)
        else:
            logger.info("Stash: scraper returned no new data to apply for scene %s", scene_id)

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    async def trigger_generate(
        self,
        scene_ids: list[str],
        covers: bool = True,
        previews: bool = False,
        sprites: bool = False,
        phashes: bool = True,
    ) -> None:
        """Trigger Stash metadata generation for specific scenes."""
        logger.info(
            "Stash: triggering generate for %d scene(s) (covers=%s, previews=%s, sprites=%s, phashes=%s)",
            len(scene_ids), covers, previews, sprites, phashes,
        )
        variables = {
            "input": {
                "sceneIDs": scene_ids,
                "covers": covers,
                "previews": previews,
                "sprites": sprites,
                "phashes": phashes,
            }
        }
        await self._query(_METADATA_GENERATE_MUTATION, variables)
