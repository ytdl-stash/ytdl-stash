"""In-memory control plane for the download pipeline.

This module supports:
- tracking currently active video downloads
- requesting cancellation for a specific video (or the active one)
- per-video asyncio task tracking for force-cancel when yt-dlp hangs
- global pause/resume for downloads and channel scans (persisted to DB)

Notes:
- Cancellation requests must be thread-safe because yt-dlp progress hooks run
  in a worker thread (`asyncio.to_thread`).
- We intentionally keep active/cancel state out of the database; it is
  ephemeral and resets on restart.
- Pause flags are persisted to the ``app_state`` table so they survive restarts.
"""

from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


class DownloadControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_video_ids: set[int] = set()
        self._cancel_requested: set[int] = set()
        self._download_tasks: dict[int, asyncio.Task] = {}
        self._downloads_paused: bool = False
        self._channels_paused: bool = False
        self._stash_healthy: bool = True

    # ------------------------------------------------------------------
    # Active / cancel tracking (unchanged)
    # ------------------------------------------------------------------

    def set_active(self, video_id: int) -> None:
        with self._lock:
            self._active_video_ids.add(video_id)

    def clear_active(self, video_id: int | None = None) -> None:
        with self._lock:
            if video_id is None:
                self._active_video_ids.clear()
                return
            self._active_video_ids.discard(video_id)

    def get_active(self) -> int | None:
        """Back-compat: return an arbitrary active video id (or None)."""
        with self._lock:
            return next(iter(self._active_video_ids), None)

    def get_active_ids(self) -> set[int]:
        with self._lock:
            return set(self._active_video_ids)

    def request_cancel(self, video_id: int) -> None:
        with self._lock:
            self._cancel_requested.add(video_id)

    def request_cancel_active(self) -> int | None:
        """Back-compat: request cancel for an arbitrary active video."""
        with self._lock:
            active_id = next(iter(self._active_video_ids), None)
            if active_id is None:
                return None
            self._cancel_requested.add(active_id)
            return active_id

    def request_cancel_all_active(self) -> set[int]:
        """Request cancellation for all active video IDs. Returns the IDs requested."""
        with self._lock:
            ids = set(self._active_video_ids)
            self._cancel_requested.update(ids)
            return ids

    def is_cancel_requested(self, video_id: int) -> bool:
        with self._lock:
            return video_id in self._cancel_requested

    def clear_cancel(self, video_id: int) -> None:
        with self._lock:
            self._cancel_requested.discard(video_id)

    def cancel_snapshot(self) -> set[int]:
        """Return a copy of the cancel-requested set (for UI hints)."""
        with self._lock:
            return set(self._cancel_requested)

    # ------------------------------------------------------------------
    # Per-video asyncio task tracking (for force-cancel when yt-dlp hangs)
    # ------------------------------------------------------------------

    def set_download_task(self, video_id: int, task: asyncio.Task) -> None:
        with self._lock:
            self._download_tasks[video_id] = task

    def cancel_download_task(self, video_id: int) -> bool:
        """Cancel the asyncio download task for a video. Returns True if cancelled."""
        with self._lock:
            task = self._download_tasks.get(video_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def clear_download_task(self, video_id: int) -> None:
        with self._lock:
            self._download_tasks.pop(video_id, None)

    # ------------------------------------------------------------------
    # Pause / resume (in-memory; persisted via load/save helpers below)
    # ------------------------------------------------------------------

    def set_downloads_paused(self, paused: bool) -> None:
        with self._lock:
            self._downloads_paused = paused

    def is_downloads_paused(self) -> bool:
        with self._lock:
            return self._downloads_paused

    def set_channels_paused(self, paused: bool) -> None:
        with self._lock:
            self._channels_paused = paused

    def is_channels_paused(self) -> bool:
        with self._lock:
            return self._channels_paused

    def set_stash_health(self, healthy: bool) -> None:
        with self._lock:
            self._stash_healthy = healthy

    def is_stash_healthy(self) -> bool:
        with self._lock:
            return self._stash_healthy


download_control = DownloadControl()


# ------------------------------------------------------------------
# DB persistence helpers (called from routes and startup)
# ------------------------------------------------------------------

_DOWNLOADS_PAUSED_KEY = "downloads_paused"
_CHANNELS_PAUSED_KEY = "channels_paused"


async def load_pause_state_from_db() -> None:
    """Read pause flags from the app_state table and set them in-memory.

    Called once at startup (after init_db).
    """
    from app import database as db_module
    from app.models import AppState

    if db_module.async_session is None:
        return

    from sqlalchemy import select

    async with db_module.async_session() as session:
        for key, setter in [
            (_DOWNLOADS_PAUSED_KEY, download_control.set_downloads_paused),
            (_CHANNELS_PAUSED_KEY, download_control.set_channels_paused),
        ]:
            result = await session.execute(
                select(AppState).where(AppState.key == key)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                paused = row.value == "1"
                setter(paused)
                logger.info("Restored pause state: %s = %s", key, paused)


async def persist_pause_state(key: str, value: bool) -> None:
    """Write a pause flag to the app_state table (upsert)."""
    if key not in {_DOWNLOADS_PAUSED_KEY, _CHANNELS_PAUSED_KEY}:
        raise ValueError(f"Unknown pause state key: {key}")

    from app import database as db_module
    from app.models import AppState

    if db_module.async_session is None:
        return

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    str_value = "1" if value else "0"
    async with db_module.async_session() as session:
        result = await session.execute(
            select(AppState).where(AppState.key == key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            try:
                session.add(AppState(key=key, value=str_value))
                await session.commit()
            except IntegrityError:
                # Race: another request inserted first — update instead.
                await session.rollback()
                result2 = await session.execute(
                    select(AppState).where(AppState.key == key)
                )
                row2 = result2.scalar_one_or_none()
                if row2 is not None:
                    row2.value = str_value
                await session.commit()
        else:
            row.value = str_value
            await session.commit()
