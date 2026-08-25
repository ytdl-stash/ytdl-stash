"""ytdl-stash application package."""

import re
from functools import lru_cache
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

GITHUB_REPO_URL = "https://github.com/ytdl-stash/ytdl-stash"

_RELEASE_VERSION_RE = re.compile(r"v?\d+\.\d+\.\d+")


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


def get_release_url() -> str:
    """Where the version badge links.

    CI bakes the git tag in as the version, so a released build points at its
    own release notes; a ``dev`` build just points at the repo.
    """
    version = get_version()
    if _RELEASE_VERSION_RE.fullmatch(version):
        return f"{GITHUB_REPO_URL}/releases/tag/{version}"
    return GITHUB_REPO_URL
