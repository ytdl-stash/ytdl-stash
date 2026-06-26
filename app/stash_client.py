"""Async GraphQL client for the Stash API. Used by the pipeline and settings routes."""

import asyncio
import base64
import http.cookiejar
import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stash enum validation — scrapers may return values outside the valid set
# (e.g. "OTHER" for gender). We drop invalid values with a warning.
# ---------------------------------------------------------------------------
_VALID_GENDERS = {"MALE", "FEMALE", "TRANSGENDER_MALE", "TRANSGENDER_FEMALE", "INTERSEX", "NON_BINARY"}
_VALID_CIRCUMCISED = {"CUT", "UNCUT"}
_ENUM_VALIDATORS: dict[str, set[str]] = {
    "gender": _VALID_GENDERS,
    "circumcised": _VALID_CIRCUMCISED,
}

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

_JOB_QUEUE_QUERY = """
query {
    jobQueue {
        id
        status
        description
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
        files {
            path
        }
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
        organized
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
        circumcised
        penis_length
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

_FIND_STUDIOS_BY_URL_QUERY = """
query FindStudiosByUrl($filter: FindFilterType!, $studio_filter: StudioFilterType!) {
    findStudios(filter: $filter, studio_filter: $studio_filter) {
        studios {
            id
            name
            urls
            image_path
            details
            scene_count
        }
    }
}
"""

_FIND_STUDIO_BY_ID_QUERY = """
query FindStudio($id: ID!) {
    findStudio(id: $id) {
        id
        name
        urls
        image_path
        details
        scene_count
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

_STUDIO_UPDATE_MUTATION = """
mutation StudioUpdate($input: StudioUpdateInput!) {
    studioUpdate(input: $input) {
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

_SCENE_DESTROY_MUTATION = """
mutation SceneDestroy($input: SceneDestroyInput!) {
    sceneDestroy(input: $input)
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

_SCRAPE_PERFORMER_URL_QUERY = """
query ScrapePerformerURL($url: String!) {
    scrapePerformerURL(url: $url) {
        name
        disambiguation
        urls
        gender
        birthdate
        ethnicity
        country
        eye_color
        hair_color
        height
        weight
        measurements
        fake_tits
        career_length
        tattoos
        piercings
        circumcised
        penis_length
        details
        death_date
        images
        tags {
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


_IMAGE_CONTENT_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/svg+xml": "image/svg+xml",
}
# Max image download size (10 MB) — avoid fetching unexpectedly huge files.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _has_custom_image(image_path: str | None) -> bool:
    """Return True if the Stash ``image_path`` represents a real user-set image.

    Stash may return ``image_path`` as:
    - ``None`` — no image at all.
    - A URL containing ``default=true`` — the auto-generated placeholder.
    - A normal URL — a real custom image.

    We only consider the last case as "has a custom image" so gap-fill logic
    correctly uploads a thumbnail when the entity only has the default placeholder.
    """
    if not image_path:
        return False
    if "default=true" in image_path:
        return False
    return True


def _load_cookies_from_file(cookies_file: str) -> httpx.Cookies:
    """Load a Netscape/Mozilla cookies.txt file into an httpx-compatible cookie jar.

    Expects the standard Netscape cookies.txt format (first line must be
    ``# Netscape HTTP Cookie File`` or ``# HTTP Cookie File``).  If the file
    is missing, unreadable, or in the wrong format the function logs a warning
    and returns an empty jar so callers degrade to unauthenticated requests.
    """
    jar = http.cookiejar.MozillaCookieJar()
    try:
        jar.load(cookies_file, ignore_discard=True, ignore_expires=True)
    except Exception:
        logger.warning("Failed to load cookies from %s", cookies_file, exc_info=True)
        return httpx.Cookies()
    cookies = httpx.Cookies()
    for cookie in jar:
        cookies.set(cookie.name, cookie.value or "", domain=cookie.domain, path=cookie.path)
    count = len(list(jar))
    if count == 0:
        logger.warning(
            "Cookies file %s was loaded but contained 0 cookies — "
            "image downloads will be unauthenticated. Ensure the file uses "
            "Netscape/Mozilla cookies.txt format.",
            cookies_file,
        )
    else:
        logger.debug("Loaded %d cookies from %s", count, cookies_file)
    return cookies


async def _url_to_data_uri(
    url: str,
    *,
    cookies_file: str | None = None,
    headers: dict[str, str] | None = None,
) -> str | None:
    """Download an image URL and return a base64 data URI (``data:<mime>;base64,...``).

    Returns *None* on any failure so callers can fall back to sending nothing.
    If the URL is already a data URI it is returned as-is.

    Parameters
    ----------
    cookies_file:
        Optional path to a Netscape/Mozilla cookies.txt file.  Many sites
        require authentication to serve thumbnail images; passing the same
        cookies file used by yt-dlp allows us to download them.
    headers:
        Optional extra HTTP headers (e.g. User-Agent, Referer) to include
        in the image request.
    """
    if url.startswith("data:"):
        return url

    cookies = _load_cookies_from_file(cookies_file) if cookies_file else None
    logger.info("Downloading image for data URI: %s (cookies=%s)", url, "yes" if cookies_file else "no")

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            max_redirects=5,
            cookies=cookies,
            headers=headers or {},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception:
        logger.warning("Failed to download image from %s", url, exc_info=True)
        return None

    logger.info(
        "Image download response: status=%d size=%d content_type=%s url=%s",
        resp.status_code, len(resp.content),
        resp.headers.get("content-type", "(none)"), url,
    )

    if len(resp.content) == 0:
        logger.warning("Empty image response from %s", url)
        return None
    if len(resp.content) > _MAX_IMAGE_BYTES:
        logger.warning(
            "Image from %s too large (%d bytes); skipping", url, len(resp.content)
        )
        return None

    # Determine MIME type from Content-Type header; fall back to jpeg.
    raw_ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    mime = _IMAGE_CONTENT_TYPES.get(raw_ct, "image/jpeg")

    encoded = base64.b64encode(resp.content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class StashClient:
    """Async client for Stash's GraphQL API. Uses ApiKey header and 30s timeout.

    Use as an async context manager to share a single httpx connection pool::

        async with StashClient(url, api_key) as client:
            await client.health_check()

    Or instantiate directly (a per-request client is created each time, less efficient).
    """

    def __init__(
        self,
        url: str,
        api_key: str = "",
        *,
        request_timeout: float = 30.0,
        cookies_file: str | None = None,
        image_request_headers: dict[str, str] | None = None,
    ) -> None:
        self.graphql_url = f"{url.rstrip('/')}/graphql"
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self.headers["ApiKey"] = api_key
        self._request_timeout = request_timeout
        # Give slow *responses* the full budget (a busy Stash queue), but keep
        # connect fast so a dead host still fails quickly.
        self._timeout = httpx.Timeout(
            request_timeout, connect=min(10.0, request_timeout)
        )
        self._client: httpx.AsyncClient | None = None
        # Image download settings — passed through to _url_to_data_uri()
        # so thumbnail downloads use the same cookies/headers as yt-dlp.
        self.cookies_file = cookies_file
        self.image_request_headers = image_request_headers

    @classmethod
    def from_settings(cls, settings: "Settings") -> "StashClient":
        """Create a StashClient with image-download settings populated from app Settings.

        This ensures thumbnail downloads use the same cookies and HTTP headers
        that yt-dlp uses, so authenticated/CDN-protected images work.
        """
        image_headers: dict[str, str] = {}
        if settings.ytdlp_user_agent:
            image_headers["User-Agent"] = settings.ytdlp_user_agent
        if settings.ytdlp_referer:
            image_headers["Referer"] = settings.ytdlp_referer
        # Merge any extra headers from the JSON config
        try:
            extra = json.loads(settings.ytdlp_http_headers_json) if settings.ytdlp_http_headers_json else {}
        except Exception:
            extra = {}
        if isinstance(extra, dict):
            image_headers.update(extra)

        return cls(
            url=settings.stash_url,
            api_key=settings.stash_api_key,
            request_timeout=settings.stash_request_timeout_seconds,
            cookies_file=settings.cookies_file,
            image_request_headers=image_headers or None,
        )

    async def __aenter__(self) -> "StashClient":
        self._client = httpx.AsyncClient(
            headers=self.headers, timeout=self._timeout
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def download_image_data_uri(self, url: str) -> str | None:
        """Download an image and return a base64 data URI, using this client's cookies/headers."""
        return await _url_to_data_uri(
            url,
            cookies_file=self.cookies_file,
            headers=self.image_request_headers,
        )

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Send a GraphQL request. Raises on HTTP or GraphQL errors. Returns data dict."""
        payload = {"query": query, "variables": variables or {}}
        try:
            if self._client:
                response = await self._client.post(self.graphql_url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
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
            raise RuntimeError(
                f"Stash request timed out after {self._request_timeout:g}s"
            ) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise RuntimeError(
                    "Stash authentication failed. Check YTDL_STASH_API_KEY"
                ) from e
            # Include response body for diagnostics (Stash often returns
            # JSON with error details, especially on 400 Bad Request).
            body = e.response.text[:500] if e.response else ""
            raise RuntimeError(
                f"Stash HTTP error: {e}" + (f"\nResponse body: {body}" if body else "")
            ) from e

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

    async def trigger_scan(self, paths: list[str]) -> str:
        """Tell Stash to scan the given file paths. Returns the Stash job ID."""
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
        result = await self._query(_METADATA_SCAN_MUTATION, variables)
        job_id = (result or {}).get("metadataScan")
        if not job_id:
            raise RuntimeError("Stash metadataScan did not return a job ID")
        return job_id

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

    async def find_scene_by_title(self, title: str) -> dict | None:
        """Find a scene by exact title. Returns scene dict or None."""
        if not title or not title.strip():
            return None
        variables = {
            "filter": {"per_page": 1},
            "scene_filter": {
                "title": {"value": title.strip(), "modifier": "EQUALS"}
            },
        }
        data = await self._query(_FIND_SCENES_QUERY, variables)
        scenes = data["findScenes"]["scenes"]
        return scenes[0] if scenes else None

    async def wait_for_scene(
        self, oshash: str, timeout: float = 30, interval: float = 2
    ) -> dict | None:
        """Poll Stash for a scene matching the oshash. Returns scene dict or None on timeout.

        Deprecated: Prefer trigger_scan -> wait_for_job -> find_scene_by_oshash instead.
        """
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

    async def find_job(self, job_id: str) -> dict | None:
        """Get job status by ID. Returns job dict or None if not found."""
        data = await self._query(_FIND_JOB_QUERY, {"input": {"id": job_id}})
        return data.get("findJob")

    async def wait_for_job(
        self,
        job_id: str,
        poll_interval: float = 1.5,
        queue_timeout: float = 1800,
        stall_timeout: float = 900,
    ) -> dict:
        """Poll until the job reaches a terminal state. Returns the FINISHED job dict.

        A job is never killed merely for taking a long time, as long as Stash
        keeps making progress. Two bounds apply instead:

        * *queue_timeout* — max time the job may sit QUEUED (not yet RUNNING).
        * *stall_timeout* — once RUNNING, max time with NO observable progress
          (neither the progress fraction nor the active sub-task/description
          changes). Any change resets the clock, so a steadily-advancing job
          runs as long as it needs — fixing the old flat 5-min cap that could
          abandon a legitimately long generate mid-run.

        Raises RuntimeError if the job FAILED / was CANCELLED / STOPPING, if it
        sits queued past *queue_timeout*, if it stalls past *stall_timeout*, or
        if it vanishes from Stash.
        """
        queue_deadline = time.monotonic() + queue_timeout
        stall_deadline: float | None = None
        last_activity: tuple[object, object] | None = None
        terminal = {"FINISHED", "FAILED", "CANCELLED", "STOPPING"}

        while True:
            now = time.monotonic()
            job = await self.find_job(job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} not found in Stash")

            status = job.get("status")

            if status in terminal:
                if status == "FAILED":
                    err = job.get("error") or "Unknown error"
                    raise RuntimeError(f"Stash job {job_id} failed: {err}")
                if status in ("CANCELLED", "STOPPING"):
                    raise RuntimeError(f"Stash job {job_id} was {status.lower()}")
                return job  # FINISHED

            if status == "RUNNING":
                # Any change in progress OR the active sub-task means the job is
                # alive — reset the stall clock; only a true flat-line trips it.
                activity = (job.get("progress"), job.get("description"))
                if activity != last_activity:
                    last_activity = activity
                    stall_deadline = now + stall_timeout
                elif stall_deadline is not None and now >= stall_deadline:
                    raise RuntimeError(
                        f"Stash job {job_id} stalled — no progress for "
                        f"{stall_timeout:g}s (progress={job.get('progress')})"
                    )
            elif now >= queue_deadline:
                raise RuntimeError(
                    f"Stash job {job_id} timed out after {queue_timeout:g}s "
                    "waiting in queue"
                )

            await asyncio.sleep(poll_interval)

    async def get_job_queue(self) -> list[dict]:
        """Return Stash's current job queue (queued + running jobs)."""
        data = await self._query(_JOB_QUEUE_QUERY)
        return data.get("jobQueue") or []

    async def wait_for_queue_below(
        self,
        max_depth: int,
        *,
        timeout: float,
        poll_interval: float = 3.0,
    ) -> None:
        """Block until Stash's job queue holds at most *max_depth* jobs.

        Backpressure so the app doesn't pile a scan/generate onto a Stash that
        is already busy with a library-wide task on its single FIFO worker —
        that contention is what slows individual requests enough to time out.

        Best-effort and always bounded: ``max_depth <= 0`` disables it, and on
        *timeout* or any query error it logs and returns, so an import is never
        permanently blocked.
        """
        if max_depth <= 0:
            return
        deadline = time.monotonic() + timeout
        logged_wait = False
        while True:
            try:
                depth = len(await self.get_job_queue())
            except Exception as e:
                logger.warning("Stash queue check failed (proceeding): %s", e)
                return
            if depth <= max_depth:
                return
            if time.monotonic() >= deadline:
                logger.warning(
                    "Stash queue still deep (%d > %d) after %.0fs; proceeding anyway",
                    depth, max_depth, timeout,
                )
                return
            if not logged_wait:
                logger.info(
                    "Stash queue depth %d > %d; waiting up to %.0fs to drain",
                    depth, max_depth, timeout,
                )
                logged_wait = True
            await asyncio.sleep(poll_interval)

    async def find_scene_by_id(self, scene_id: str) -> dict | None:
        """Fetch a scene by Stash ID. Returns full scene dict or None."""
        data = await self._query(_FIND_SCENE_BY_ID_QUERY, {"id": scene_id})
        return data.get("findScene")

    @staticmethod
    def scene_primary_path(scene: dict | None) -> str | None:
        """Return a scene's primary file path (from a query that selects files{path})."""
        files = (scene or {}).get("files") or []
        if files and isinstance(files[0], dict):
            return files[0].get("path")
        return None

    async def wait_for_scene_path_stable(
        self,
        scene_id: str,
        *,
        settle: float = 2.0,
        interval: float = 1.0,
        attempts: int = 6,
        total_timeout: float | None = None,
        require_change: bool = False,
    ) -> str | None:
        """Wait until a scene's primary file path stops changing, then return it.

        A Stash renamer plugin can move/rename the underlying file
        *asynchronously* after an import scan — that work is NOT part of the
        scan job, so ``wait_for_job`` returns before it finishes. Callers that
        will act on the file (generate) or want to record its real location
        should await this first to avoid racing a file mid-move.

        ``settle`` gives the async move time to begin before we start trusting
        the path; we then poll until the path is unchanged across two reads.

        When Stash's job queue is busy the renamer can be delayed well past the
        head-start. ``total_timeout`` (when set) bounds the *whole* wait by
        wall-clock instead of a fixed ``attempts`` count, so we keep polling for
        the move to land. ``require_change`` makes the wait insist on observing
        the path change at least once before accepting it as stable — use it
        when a renamer is known to run on import, so a not-yet-started move
        can't masquerade as "settled" and leave us generating against the
        pre-move path.

        Returns the final observed path, or None if it can't be read. On
        ``total_timeout`` expiry it returns the last observed path (best effort);
        if ``require_change`` was set but no change was ever seen, it logs a
        warning first (the renamer likely didn't run).
        """
        if settle > 0:
            await asyncio.sleep(settle)

        deadline = (
            time.monotonic() + total_timeout
            if total_timeout and total_timeout > 0
            else None
        )

        initial: str | None = None
        prev: str | None = None
        final: str | None = None
        seen_change = False
        reads = 0
        while True:
            scene = await self.find_scene_by_id(scene_id)
            final = self.scene_primary_path(scene)
            if reads == 0:
                initial = final
            elif final is not None and final != initial:
                seen_change = True
            reads += 1

            # "Stable" = unchanged across two consecutive reads. When a renamer
            # is expected, also insist we've actually seen the path change, so a
            # move that hasn't started yet can't look settled.
            stable = reads > 1 and final is not None and final == prev
            if require_change and not seen_change:
                stable = False
            if stable:
                return final
            prev = final

            # Termination: wall-clock deadline when given, else fixed attempts.
            if deadline is not None:
                if time.monotonic() + interval >= deadline:
                    if require_change and not seen_change:
                        logger.warning(
                            "Stash scene %s: file path never changed within %.0fs; "
                            "proceeding with %r (renamer may not have run on import)",
                            scene_id, total_timeout, final,
                        )
                    return final
            elif reads >= max(1, attempts):
                return final

            await asyncio.sleep(interval)

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
        """Find a performer by URL (INCLUDES match on performer urls). Returns {id, name, urls, image_path} or None."""
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
        return self._performer_dict(performers[0])

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
            data_uri = await self.download_image_data_uri(image_url)
            if data_uri:
                input_dict["image"] = data_uri
        logger.info(
            "Creating Stash performer %r with fields: %s",
            name, list(input_dict.keys()),
        )
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
        if image_url and not _has_custom_image(performer.get("image_path")):
            data_uri = await self.download_image_data_uri(image_url)
            if data_uri:
                updates["image"] = data_uri
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
                await self._gap_fill_performer_url_image(by_url, url, image_url)
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
        circumcised, penis_length, alias_list, details, death_date, image
        (url string), rating100.

        Enum fields (gender, circumcised) are validated and uppercased;
        invalid values are silently dropped with a warning.
        """
        input_dict: dict = {"id": performer_id}
        for key, value in fields.items():
            if value is not None:
                input_dict[key] = value
        # Validate Stash enum fields — uppercase and drop invalid values
        for enum_field, valid_values in _ENUM_VALIDATORS.items():
            raw = input_dict.get(enum_field)
            if isinstance(raw, str):
                normalised = raw.upper()
                if normalised in valid_values:
                    input_dict[enum_field] = normalised
                else:
                    logger.warning(
                        "Dropping invalid %s value %r for performer %s",
                        enum_field, raw, performer_id,
                    )
                    del input_dict[enum_field]
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

    def _studio_dict(self, s: dict) -> dict:
        """Build {id, name, urls, image_path, details, scene_count} from GraphQL studio result."""
        return {
            "id": s["id"],
            "name": s.get("name") or "",
            "urls": s.get("urls") or [],
            "image_path": s.get("image_path"),
            "details": s.get("details"),
            "scene_count": s.get("scene_count"),
        }

    async def find_studio_by_url(self, url: str) -> dict | None:
        """Find a studio by URL (INCLUDES match on studio urls). Returns {id, name, urls, image_path, details} or None."""
        variables = {
            "filter": {"per_page": 1},
            "studio_filter": {
                "url": {
                    "value": url,
                    "modifier": "INCLUDES",
                }
            },
        }
        data = await self._query(_FIND_STUDIOS_BY_URL_QUERY, variables)
        studios = data["findStudios"]["studios"]
        if not studios:
            return None
        return self._studio_dict(studios[0])

    async def get_studio(self, studio_id: str) -> dict | None:
        """Fetch full studio data by Stash ID. Returns {id, name, urls, image_path, details} or None."""
        data = await self._query(_FIND_STUDIO_BY_ID_QUERY, {"id": studio_id})
        studio = data.get("findStudio")
        if not studio:
            return None
        return self._studio_dict(studio)

    async def create_studio_with_metadata(
        self,
        name: str,
        urls: list[str],
        image_url: str | None = None,
        details: str | None = None,
    ) -> str:
        """Create a studio with name, urls, and optional image/details. Returns the new studio's ID."""
        name = name.strip()
        input_dict: dict = {"name": name, "urls": urls}
        if image_url:
            data_uri = await self.download_image_data_uri(image_url)
            if data_uri:
                input_dict["image"] = data_uri
        if details:
            input_dict["details"] = details
        logger.info(
            "Creating Stash studio %r with fields: %s",
            name, list(input_dict.keys()),
        )
        data = await self._query(_STUDIO_CREATE_MUTATION, {"input": input_dict})
        return data["studioCreate"]["id"]

    async def update_studio(self, studio_id: str, **fields: object) -> None:
        """Update a Stash studio. Only non-None keyword args are sent.

        Supported fields match StudioUpdateInput: name, urls, parent_id, image,
        rating100, favorite, details, aliases, tag_ids, ignore_auto_tag.
        """
        input_dict: dict = {"id": studio_id}
        for key, value in fields.items():
            if value is not None:
                input_dict[key] = value
        if len(input_dict) <= 1:
            return
        await self._query(_STUDIO_UPDATE_MUTATION, {"input": input_dict})

    async def _gap_fill_studio_url_image_details(
        self,
        studio: dict,
        url: str,
        image_url: str | None,
        details: str | None,
    ) -> None:
        """Add URL, image, and details to an existing studio if missing."""
        updates: dict = {}
        stash_urls = studio.get("urls") or []
        if url and url not in stash_urls:
            updates["urls"] = stash_urls + [url]
        if image_url and not _has_custom_image(studio.get("image_path")):
            data_uri = await self.download_image_data_uri(image_url)
            if data_uri:
                updates["image"] = data_uri
        stash_details = (studio.get("details") or "").strip()
        if details and not stash_details:
            updates["details"] = details
        if updates:
            logger.info(
                "Stash: gap-filling studio %s with %s",
                studio["id"],
                list(updates.keys()),
            )
            await self.update_studio(studio["id"], **updates)

    async def find_or_create_studio_by_url(
        self,
        name: str,
        url: str,
        image_url: str | None = None,
        details: str | None = None,
    ) -> str:
        """Find studio by URL first, then by name, then create with metadata. Returns studio ID."""
        name = name.strip()
        key = name.lower()
        async with _lock_dict_meta:
            if key not in _studio_locks:
                _studio_locks[key] = asyncio.Lock()
            lock = _studio_locks[key]
        async with lock:
            by_url = await self.find_studio_by_url(url)
            if by_url:
                await self._gap_fill_studio_url_image_details(
                    by_url, url, image_url, details
                )
                return by_url["id"]
            studio_id = await self.find_studio(name)
            if studio_id:
                studio = await self.get_studio(studio_id)
                if studio:
                    await self._gap_fill_studio_url_image_details(
                        studio, url, image_url, details
                    )
                return studio_id
            logger.info("Stash: creating new studio '%s' with URL %s", name, url)
            return await self.create_studio_with_metadata(
                name=name,
                urls=[url],
                image_url=image_url,
                details=details,
            )

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
        organized: bool | None = None,
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
        if organized is not None:
            scene_input["organized"] = organized
        await self._query(_SCENE_UPDATE_MUTATION, {"input": scene_input})

    async def destroy_scene(
        self,
        scene_id: str,
        *,
        delete_file: bool = True,
        delete_generated: bool = True,
    ) -> bool:
        """Delete a scene from Stash. Returns True on success.

        ``delete_file`` also removes the underlying media file from disk;
        ``delete_generated`` removes generated artifacts (covers, previews,
        sprites, phashes).
        """
        variables = {
            "input": {
                "id": scene_id,
                "delete_file": delete_file,
                "delete_generated": delete_generated,
            }
        }
        data = await self._query(_SCENE_DESTROY_MUTATION, variables)
        return bool(data.get("sceneDestroy"))

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

    async def scrape_performer_url(self, url: str) -> dict | None:
        """Scrape performer metadata from a URL using Stash's configured scrapers.

        Returns the scraped performer dict or None if no scraper matched / no data returned.
        """
        try:
            data = await self._query(_SCRAPE_PERFORMER_URL_QUERY, {"url": url})
            scraped = data.get("scrapePerformerURL")
            if not scraped:
                logger.info("Stash: no scraper returned performer data for URL %s", url)
                return None
            logger.info("Stash: scraper returned performer data for URL %s", url)
            return scraped
        except RuntimeError as e:
            # GraphQL errors (e.g. no matching scraper) are non-fatal
            logger.warning("Stash: scrapePerformerURL failed for %s: %s", url, e)
            return None

    async def apply_scraped_performer(
        self,
        performer_id: str,
        scraped: dict,
    ) -> None:
        """Apply scraped metadata to an existing performer (gap-fill only).

        Only sets fields the scraper returned and that the performer doesn't already have.

        Note: ScrapedPerformer field names differ from Performer / PerformerUpdateInput
        in a few cases:
          - scraped ``height`` (String)       → update ``height_cm`` (Int)
          - scraped ``weight`` (String)       → update ``weight`` (Int)
          - scraped ``penis_length`` (String)  → update ``penis_length`` (Float)
          - scraped ``gender`` (String)       → update ``gender`` (GenderEnum, validated+uppercased)
          - scraped ``circumcised`` (String)  → update ``circumcised`` (CircumisedEnum, validated+uppercased)
        """
        current = await self.get_performer(performer_id)
        if not current:
            return

        updates: dict = {}

        # Simple string fields where scraped key == update key — only fill gaps
        _gap_fill_fields = [
            ("gender", "gender"),
            ("birthdate", "birthdate"),
            ("ethnicity", "ethnicity"),
            ("country", "country"),
            ("eye_color", "eye_color"),
            ("hair_color", "hair_color"),
            ("measurements", "measurements"),
            ("fake_tits", "fake_tits"),
            ("career_length", "career_length"),
            ("tattoos", "tattoos"),
            ("piercings", "piercings"),
            ("circumcised", "circumcised"),
            ("details", "details"),
            ("disambiguation", "disambiguation"),
            ("death_date", "death_date"),
        ]
        for scraped_key, stash_key in _gap_fill_fields:
            scraped_val = scraped.get(scraped_key)
            if scraped_val and not current.get(stash_key):
                # Validate Stash enum fields — uppercase and skip invalid
                if stash_key in _ENUM_VALIDATORS:
                    normalised = scraped_val.upper()
                    if normalised not in _ENUM_VALIDATORS[stash_key]:
                        logger.warning(
                            "Dropping invalid scraped %s value %r for performer %s",
                            stash_key, scraped_val, performer_id,
                        )
                        continue
                    scraped_val = normalised
                updates[stash_key] = scraped_val

        # height: ScrapedPerformer returns "height" as a String (e.g. "175"),
        # but PerformerUpdateInput expects "height_cm" as an Int.
        scraped_height = scraped.get("height")
        if scraped_height and not current.get("height_cm"):
            try:
                updates["height_cm"] = int(scraped_height)
            except (ValueError, TypeError):
                pass

        # weight: ScrapedPerformer returns "weight" as a String (e.g. "60"),
        # but PerformerUpdateInput expects "weight" as an Int.
        scraped_weight = scraped.get("weight")
        if scraped_weight and not current.get("weight"):
            try:
                updates["weight"] = int(scraped_weight)
            except (ValueError, TypeError):
                pass

        # penis_length: ScrapedPerformer returns "penis_length" as a String,
        # but PerformerUpdateInput expects "penis_length" as a Float.
        scraped_penis_length = scraped.get("penis_length")
        if scraped_penis_length and not current.get("penis_length"):
            try:
                updates["penis_length"] = float(scraped_penis_length)
            except (ValueError, TypeError):
                pass

        # URLs: merge scraped URLs into existing list
        scraped_urls = scraped.get("urls") or []
        current_urls = current.get("urls") or []
        new_urls = [u for u in scraped_urls if u not in current_urls]
        if new_urls:
            updates["urls"] = current_urls + new_urls

        # Image: use first scraped image if performer has no custom image
        scraped_images = scraped.get("images") or []
        if scraped_images and not _has_custom_image(current.get("image_path")):
            # Scraped images are base64 data URIs or URLs
            updates["image"] = scraped_images[0]

        if updates:
            logger.info(
                "Stash: applying scraped data to performer %s (fields: %s)",
                performer_id, list(updates.keys()),
            )
            await self.update_performer(performer_id, **updates)
        else:
            logger.info(
                "Stash: scraper returned no new data to apply for performer %s",
                performer_id,
            )

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
    ) -> str | None:
        """Trigger Stash metadata generation for specific scenes.

        Returns the Stash job ID if the mutation succeeds.
        """
        logger.info(
            "Stash: triggering generate for %d scene(s) "
            "(covers=%s, previews=%s, sprites=%s, phashes=%s)",
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
        result = await self._query(_METADATA_GENERATE_MUTATION, variables)
        job_id = (result or {}).get("metadataGenerate")
        logger.info("Stash: generate job started — job_id=%s", job_id)
        return job_id
