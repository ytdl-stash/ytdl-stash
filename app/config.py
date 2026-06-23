from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables prefixed with YTDL_."""

    stash_url: str = "http://localhost:9999"
    stash_api_key: str = ""
    download_dir: str = "/downloads"
    stash_download_dir: str | None = None  # Path to downloads as Stash sees it; None = use download_dir
    data_dir: str = "/app/data"
    default_check_interval_hours: int = 6
    max_concurrent_downloads: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Max number of videos to download/import in parallel (default: 1).",
    )
    download_delay_seconds: int = 5
    download_timeout_seconds: int = Field(
        default=0,
        ge=0,
        description="Max seconds for a single download before timing out. 0 = no timeout.",
    )
    cookies_file: str | None = None
    ytdlp_output_template: str = "%(uploader)s - %(title)s [%(id)s].%(ext)s"
    # ---------------------------------------------------------------------
    # yt-dlp options (applied to scan/download calls)
    #
    # These are intentionally "opt-in" knobs with safe defaults; advanced
    # users can set any yt-dlp option via the JSON fields below.
    # ---------------------------------------------------------------------
    ytdlp_format: str | None = None
    ytdlp_impersonate: str | None = None
    ytdlp_user_agent: str | None = None
    ytdlp_referer: str | None = None
    ytdlp_proxy: str | None = None
    ytdlp_socket_timeout_seconds: int | None = None

    # Defaults match the previous hard-coded downloader behavior
    ytdlp_retries: int = 3
    ytdlp_fragment_retries: int = 3

    # Advanced overrides: JSON objects merged into yt-dlp options dict.
    # - http_headers_json is merged into opts["http_headers"]
    # - scan_opts_json merged for channel scans/metadata extraction
    # - download_opts_json merged for downloads
    #
    # Note: These are strings so they can be supplied via env vars easily.
    ytdlp_http_headers_json: str = "{}"
    ytdlp_scan_opts_json: str = "{}"
    ytdlp_download_opts_json: str = "{}"

    # Convenience: periodically check whether a newer yt-dlp exists on PyPI
    ytdlp_update_check_interval_hours: int = 24
    # Scheduler intervals (seconds for channel check and download processor)
    channel_check_interval_seconds: int = Field(default=60, ge=10, le=86400)
    download_process_interval_seconds: int = Field(default=30, ge=10, le=86400)
    # Auto-retry failed videos every N hours (0 = disabled / manual-only)
    retry_failed_interval_hours: int = Field(default=0, ge=0, le=8760)
    log_level: str = "INFO"

    # ---------------------------------------------------------------------
    # Post-sync Stash actions (run after a scene is synced to Stash)
    # ---------------------------------------------------------------------
    stash_scrape_after_sync: bool = True
    stash_generate_after_sync: bool = True
    stash_generate_covers: bool = True
    stash_generate_previews: bool = True
    stash_generate_sprites: bool = True
    stash_generate_phashes: bool = True
    # Head-start (seconds) before trusting a scene's file path after an import
    # scan or before generate — gives a Stash renamer / file-move rule time to
    # begin so we don't read a path that's about to change.
    stash_organized_settle_seconds: int = Field(default=5, ge=0, le=60)
    # Max wall-clock seconds to wait for a Stash renamer to finish moving a file
    # on import before we sync/generate. A busy Stash job queue can delay the
    # renamer well past the head-start; the wait returns as soon as the path is
    # observed stable, so this only bites when the move is genuinely slow.
    stash_import_settle_timeout_seconds: int = Field(default=600, ge=0, le=3600)
    # Set True if a Stash renamer plugin moves/renames files on import. The
    # import settle then waits for the path to actually change (bounded by
    # stash_import_settle_timeout_seconds) instead of accepting the pre-move
    # path, and generate re-checks the file didn't move mid-job. Leave False
    # when no renamer runs on import (default) — behavior is then unchanged.
    stash_expect_renamer_on_import: bool = False

    model_config = {"env_prefix": "YTDL_"}


@lru_cache
def get_settings() -> Settings:
    """Cached factory for use as a FastAPI dependency. Settings are read once from env vars."""
    return Settings()
