"""In-memory download progress tracking for yt-dlp downloads.

This is intentionally kept out of the database:
- yt-dlp progress callbacks run in a worker thread (`asyncio.to_thread`)
- async SQLAlchemy sessions are not thread-safe

We store progress per video ID in memory so the UI can render a progress bar.
If the app restarts, progress resets (the video status in DB still reflects
downloading/failed/etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time


def _format_bytes(n: int | None) -> str | None:
    if n is None:
        return None
    if n < 0:
        return None
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    idx = 0
    while f >= 1024 and idx < len(units) - 1:
        f /= 1024
        idx += 1
    if idx == 0:
        return f"{int(f)} {units[idx]}"
    return f"{f:.1f} {units[idx]}"


def _format_eta(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 0:
        return None
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _format_speed(bps: float | None) -> str | None:
    if bps is None:
        return None
    if bps <= 0:
        return None
    pretty = _format_bytes(int(bps))
    return f"{pretty}/s" if pretty else None


@dataclass(frozen=True)
class DownloadProgressView:
    percent: int | None
    downloaded: str | None
    total: str | None
    speed: str | None
    eta: str | None
    status: str | None
    updated_at: float


class DownloadProgressStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_video_id: dict[int, DownloadProgressView] = {}

    def clear(self, video_id: int) -> None:
        with self._lock:
            self._by_video_id.pop(video_id, None)

    def snapshot(self) -> dict[int, DownloadProgressView]:
        with self._lock:
            return dict(self._by_video_id)

    def update_from_ytdlp_hook(self, video_id: int, d: dict) -> None:
        """Update progress from a yt-dlp `progress_hooks` callback dict."""
        try:
            status = d.get("status")
            downloaded_bytes = d.get("downloaded_bytes")
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
            speed_bps = d.get("speed")
            eta_seconds = d.get("eta")

            percent: int | None = None
            if status == "finished":
                percent = 100
            elif isinstance(downloaded_bytes, (int, float)) and isinstance(
                total_bytes, (int, float)
            ):
                if total_bytes and total_bytes > 0:
                    percent = int(max(0.0, min(100.0, (downloaded_bytes / total_bytes) * 100.0)))

            view = DownloadProgressView(
                percent=percent,
                downloaded=_format_bytes(int(downloaded_bytes))
                if isinstance(downloaded_bytes, (int, float))
                else None,
                total=_format_bytes(int(total_bytes))
                if isinstance(total_bytes, (int, float))
                else None,
                speed=_format_speed(float(speed_bps))
                if isinstance(speed_bps, (int, float))
                else None,
                eta=_format_eta(int(eta_seconds))
                if isinstance(eta_seconds, (int, float))
                else None,
                status=str(status) if status is not None else None,
                updated_at=time.time(),
            )

            with self._lock:
                self._by_video_id[video_id] = view
        except Exception:
            # Progress should never crash a download; ignore malformed hook dicts.
            return


download_progress = DownloadProgressStore()

