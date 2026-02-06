# ADR-006: Use Pydantic BaseSettings for Configuration

**Status**: Accepted

## Context

The app needs to read configuration values (Stash URL, API key, download directory, etc.) from environment variables. These values need validation, defaults, and type coercion.

## Decision

Use **pydantic-settings** (`BaseSettings`) with the `YTDL_` env prefix for all application configuration.

Implementation in `app/config.py`:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    stash_url: str = "http://localhost:9999"
    stash_api_key: str = ""
    download_dir: str = "/downloads"
    data_dir: str = "/app/data"
    default_check_interval_hours: int = 6
    download_delay_seconds: int = 5
    cookies_file: str | None = None
    ytdlp_output_template: str = "%(uploader)s - %(title)s [%(id)s].%(ext)s"

    model_config = {"env_prefix": "YTDL_"}

def get_settings() -> Settings:
    return Settings()
```

## Alternatives Considered

### python-dotenv + os.environ
- Simple, widely used.
- No type validation, no defaults documentation, manual casting.
- Rejected because Pydantic gives us validated, typed, documented settings for free.

### dynaconf
- Feature-rich (multiple formats, environments, vaults).
- Overkill for a single-environment Docker app reading env vars.
- Rejected as unnecessary complexity.

### YAML/TOML config file
- Clean human-readable config.
- Harder to inject in Docker (env vars are the Docker standard).
- Would need a file mount or config generation.
- Rejected in favor of 12-factor env var configuration.

## Consequences

**Positive:**
- Type validation at startup: wrong types cause immediate, clear errors.
- All settings documented in one class with defaults visible.
- `env_prefix = "YTDL_"` prevents collision with system env vars.
- FastAPI dependency injection: `Depends(get_settings)` in any route.
- IDE autocomplete on `settings.stash_url`, `settings.download_dir`, etc.

**Negative:**
- Extra dependency (`pydantic-settings` is separate from `pydantic` since v2).
- `get_settings()` creates a new instance each call; add `@lru_cache` if startup cost matters (it doesn't currently).

## Key Details

- **Pydantic v2 style**: Uses `model_config = {"env_prefix": "YTDL_"}` dict, NOT the deprecated inner `class Config`.
- **env_prefix**: Setting `stash_url` reads from `YTDL_STASH_URL` environment variable.
- **Optional fields**: Use `str | None = None` (Python 3.10+ union syntax, valid in 3.12).
- **docker-compose.yml**: Maps `${STASH_URL}` and `${STASH_API_KEY}` to `YTDL_STASH_URL` and `YTDL_STASH_API_KEY` with defaults.
