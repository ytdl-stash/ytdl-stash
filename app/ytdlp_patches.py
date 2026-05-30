"""Local runtime patches for upstream yt-dlp bugs not yet fixed.

These are monkeypatches applied at import time, before any
``yt_dlp.YoutubeDL`` is constructed. Each patch is idempotent (guarded by a
marker attribute) and defensive (a failure logs and is swallowed rather than
breaking startup or a download).

Active patches
--------------
PornHub "HTTP Error 410: Gone" — yt-dlp issue #16729
    PornHub began rejecting yt-dlp's TLS handshake with ``HTTP Error 410:
    Gone`` on both the watch page (``/view_video.php``) and the media CDN,
    even though the same URLs load fine in a browser. The request succeeds
    when made with a *legacy* OpenSSL context. yt-dlp already supports a
    per-request ``legacy_ssl`` extension, but the PornHub extractor doesn't
    set it, and there is no merged upstream fix — PR #16776 (legacy_ssl) and
    PR #16794 (impersonate) are both stalled as of 2026-05.

    We reproduce the working fix from PR #16776 without forking yt-dlp:
      1. Wrap ``YoutubeDL.urlopen`` so every request bound for a PornHub host
         or its media CDNs (phncdn/phprcdn) carries ``legacy_ssl=True``.
      2. Bump the age-disclaimer cookie ``accessAgeDisclaimerPH`` from ``1``
         to ``2`` so the HLS formats are exposed.

    Remove this module (and the ``apply_patches()`` call in ``downloader.py``)
    once a fix lands upstream and we pin a nightly that includes it.
    Tracking: https://github.com/yt-dlp/yt-dlp/issues/16729
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_applied = False


def _is_pornhub_host(host: str) -> bool:
    """True for PornHub watch hosts and the media CDNs that serve its video files."""
    host = (host or "").lower()
    if not host:
        return False
    return (
        "pornhub" in host          # www.pornhub.com, de.pornhub.com, pornhubpremium.com, …
        or host.endswith("phncdn.com")   # primary media CDN
        or host.endswith("phprcdn.com")  # premium media CDN
    )


def _patch_pornhub_legacy_ssl() -> None:
    """Attach ``legacy_ssl=True`` to every request to a PornHub host."""
    import yt_dlp
    from yt_dlp.networking import Request

    if getattr(yt_dlp.YoutubeDL.urlopen, "_ytdl_stash_patched", False):
        return

    _orig_urlopen = yt_dlp.YoutubeDL.urlopen

    def urlopen(self, req):
        # yt-dlp's urlopen accepts a str, a urllib Request, or a yt_dlp
        # Request. Normalize a str so we can attach the extension; for a
        # yt_dlp Request mutate it in place; anything else passes through.
        if isinstance(req, str):
            req = Request(req)
        try:
            if isinstance(req, Request):
                host = urlparse(req.url).hostname or ""
                if _is_pornhub_host(host):
                    req.extensions["legacy_ssl"] = True
        except Exception:  # never let the shim break a real download
            logger.debug("PornHub legacy_ssl shim skipped a request", exc_info=True)
        return _orig_urlopen(self, req)

    urlopen._ytdl_stash_patched = True
    yt_dlp.YoutubeDL.urlopen = urlopen
    logger.info("yt-dlp patch applied: PornHub legacy_ssl (issue #16729)")


def _patch_pornhub_age_cookie() -> None:
    """Set ``accessAgeDisclaimerPH=2`` (instead of 1) to expose HLS formats."""
    try:
        from yt_dlp.extractor.pornhub import PornHubBaseIE
    except Exception:
        logger.warning(
            "yt-dlp patch skipped: PornHub extractor not importable", exc_info=True
        )
        return

    if getattr(PornHubBaseIE._set_age_cookies, "_ytdl_stash_patched", False):
        return

    def _set_age_cookies(self, host):
        self._set_cookie(host, "age_verified", "1")
        self._set_cookie(host, "accessAgeDisclaimerPH", "2")
        self._set_cookie(host, "accessAgeDisclaimerUK", "1")
        self._set_cookie(host, "accessPH", "1")

    _set_age_cookies._ytdl_stash_patched = True
    PornHubBaseIE._set_age_cookies = _set_age_cookies
    logger.info("yt-dlp patch applied: PornHub age-disclaimer cookie=2 (issue #16729)")


def apply_patches() -> None:
    """Apply all local yt-dlp patches. Idempotent; safe to call repeatedly."""
    global _applied
    if _applied:
        return
    try:
        _patch_pornhub_legacy_ssl()
        _patch_pornhub_age_cookie()
    except Exception:
        logger.exception("Failed to apply yt-dlp patches")
    _applied = True
