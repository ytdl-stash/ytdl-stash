"""In-memory control plane for the download pipeline.

This module supports:
- tracking currently active video downloads
- requesting cancellation for a specific video (or the active one)

Notes:
- Cancellation requests must be thread-safe because yt-dlp progress hooks run
  in a worker thread (`asyncio.to_thread`).
- We intentionally keep this out of the database; it is ephemeral and resets
  on restart.
"""

from __future__ import annotations

import threading


class DownloadControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_video_ids: set[int] = set()
        self._cancel_requested: set[int] = set()

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


download_control = DownloadControl()

