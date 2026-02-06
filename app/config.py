from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables prefixed with YTDL_."""

    stash_url: str = "http://localhost:9999"
    stash_api_key: str = ""
    download_dir: str = "/downloads"
    stash_download_dir: str | None = None  # Path to downloads as Stash sees it; None = use download_dir
    data_dir: str = "/app/data"
    default_check_interval_hours: int = 6
    download_delay_seconds: int = 5
    cookies_file: str | None = None
    ytdlp_output_template: str = "%(uploader)s - %(title)s [%(id)s].%(ext)s"
    log_level: str = "INFO"

    model_config = {"env_prefix": "YTDL_"}


@lru_cache
def get_settings() -> Settings:
    """Cached factory for use as a FastAPI dependency. Settings are read once from env vars."""
    return Settings()
