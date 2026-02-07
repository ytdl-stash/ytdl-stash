"""yt-dlp update checks and (optional) in-container self-update.

Note: In a Docker deployment, the preferred upgrade path is still "rebuild image".
The self-update is mainly useful for quick testing before rebuilding.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YtdlpUpdateStatus:
    loaded_version: str | None
    installed_version: str | None
    latest_version: str | None
    last_checked_at: datetime | None
    last_update_at: datetime | None
    update_available: bool
    restart_required: bool
    last_error: str | None
    checking: bool = False
    updating: bool = False


_lock = asyncio.Lock()
_status = YtdlpUpdateStatus(
    loaded_version=None,
    installed_version=None,
    latest_version=None,
    last_checked_at=None,
    last_update_at=None,
    update_available=False,
    restart_required=False,
    last_error=None,
    checking=False,
    updating=False,
)


def _get_loaded_version() -> str | None:
    try:
        import yt_dlp

        return getattr(yt_dlp.version, "__version__", None)
    except Exception:
        return None


def _get_installed_version() -> str | None:
    try:
        return importlib.metadata.version("yt-dlp")
    except Exception:
        return None


def _compute_flags(
    *, loaded_version: str | None, installed_version: str | None, latest_version: str | None
) -> tuple[bool, bool]:
    update_available = bool(
        latest_version
        and installed_version
        and latest_version.strip()
        and installed_version.strip()
        and latest_version.strip() != installed_version.strip()
    )
    restart_required = bool(
        loaded_version
        and installed_version
        and loaded_version.strip()
        and installed_version.strip()
        and loaded_version.strip() != installed_version.strip()
    )
    return update_available, restart_required


async def get_status() -> YtdlpUpdateStatus:
    """Return a snapshot of the current update status."""
    async with _lock:
        loaded = _get_loaded_version()
        installed = _get_installed_version()
        update_available, restart_required = _compute_flags(
            loaded_version=loaded,
            installed_version=installed,
            latest_version=_status.latest_version,
        )
        return replace(
            _status,
            loaded_version=loaded,
            installed_version=installed,
            update_available=update_available,
            restart_required=restart_required,
        )


async def check_for_update() -> YtdlpUpdateStatus:
    """Fetch latest yt-dlp version from PyPI and update the in-memory status."""
    global _status
    async with _lock:
        if _status.checking:
            return _status
        _status = replace(_status, checking=True, last_error=None)

    latest: str | None = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://pypi.org/pypi/yt-dlp/json")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            info = data.get("info") or {}
            if isinstance(info, dict):
                v = info.get("version")
                latest = str(v).strip() if v else None
    except Exception as exc:
        logger.warning("yt-dlp update check failed: %s", exc)
        async with _lock:
            _status = replace(
                _status,
                checking=False,
                last_checked_at=datetime.now(UTC),
                last_error=str(exc)[:500],
            )
        return await get_status()

    async with _lock:
        _status = replace(
            _status,
            checking=False,
            last_checked_at=datetime.now(UTC),
            latest_version=latest,
        )
    return await get_status()


def _run_pip_update() -> tuple[int, str]:
    """Run a pip update for yt-dlp. Returns (exit_code, combined_output)."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "-U",
        "yt-dlp",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
    return proc.returncode, out[-8000:]  # cap


async def update_ytdlp() -> YtdlpUpdateStatus:
    """Attempt to self-update yt-dlp in the running container via pip.

    IMPORTANT: The running process may have already imported yt-dlp. After
    updating, a restart is usually required to use the new code.
    """
    global _status
    async with _lock:
        if _status.updating:
            return _status
        _status = replace(_status, updating=True, last_error=None)

    # Refresh latest first so we can compute "update available".
    status = await check_for_update()
    if not status.update_available:
        async with _lock:
            _status = replace(_status, updating=False)
        return await get_status()

    code, output = await asyncio.to_thread(_run_pip_update)
    async with _lock:
        if code != 0:
            _status = replace(
                _status,
                updating=False,
                last_update_at=datetime.now(UTC),
                last_error=f"pip exited {code}: {output}".strip()[:500],
            )
        else:
            _status = replace(
                _status,
                updating=False,
                last_update_at=datetime.now(UTC),
                last_error=None,
            )
    # After pip update, installed version may differ from loaded version.
    return await get_status()

