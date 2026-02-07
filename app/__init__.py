"""ytdl-stash application package."""

from functools import lru_cache
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the app version.

    Reads from the VERSION file baked into the Docker image at build time
    (via the APP_VERSION build arg).  Falls back to ``"dev"`` when running
    outside the container or when the file is missing.
    """
    try:
        return _VERSION_FILE.read_text().strip() or "dev"
    except FileNotFoundError:
        return "dev"
