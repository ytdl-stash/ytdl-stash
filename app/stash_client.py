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
        }
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            scene = await self.find_scene_by_oshash(oshash)
            if scene:
                return scene
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(interval, remaining))
        return None

    async def find_performer(self, name: str) -> str | None:
        """Find a performer by exact name. Returns performer ID or None."""
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
        return performers[0]["id"] if performers else None

    async def create_performer(self, name: str) -> str:
        """Create a performer. Returns the new performer's ID."""
        data = await self._query(_PERFORMER_CREATE_MUTATION, {"input": {"name": name}})
        return data["performerCreate"]["id"]

    async def find_or_create_performer(self, name: str) -> str:
        """Find performer by name or create. Returns performer ID."""
        performer_id = await self.find_performer(name)
        if performer_id:
            return performer_id
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
        input_dict: dict = {"name": name, "urls": urls}
        if image_url:
            input_dict["image"] = image_url
        data = await self._query(_PERFORMER_CREATE_WITH_META_MUTATION, {"input": input_dict})
        return data["performerCreate"]["id"]

    async def find_or_create_performer_by_url(
        self,
        name: str,
        url: str,
        image_url: str | None = None,
    ) -> str:
        """Find performer by URL first, then by name, then create with metadata. Returns performer ID."""
        by_url = await self.find_performer_by_url(url)
        if by_url:
            return by_url["id"]
        by_name = await self.find_performer(name)
        if by_name:
            return by_name
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
        studio_id = await self.find_studio(name)
        if studio_id:
            return studio_id
        return await self.create_studio(name)

    async def update_scene(
        self,
        scene_id: str,
        title: str | None = None,
        urls: list[str] | None = None,
        date: str | None = None,
        studio_id: str | None = None,
        performer_ids: list[str] | None = None,
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
        await self._query(_SCENE_UPDATE_MUTATION, {"input": scene_input})
