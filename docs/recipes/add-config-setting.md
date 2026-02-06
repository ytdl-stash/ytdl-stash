# Recipe: Add a New Configuration Setting

How to add a new application setting that reads from environment variables.

---

## Steps

### 1. Add the field to `app/config.py`

```python
class Settings(BaseSettings):
    # ... existing fields ...
    my_new_setting: int = 42           # With default
    my_optional_setting: str | None = None  # Optional (nullable)
```

- Use Python type annotations for automatic validation.
- Provide a sensible default value.
- Optional settings use `str | None = None`.

### 2. The env var is auto-derived

The `env_prefix = "YTDL_"` means:
- `my_new_setting` reads from `YTDL_MY_NEW_SETTING`
- `my_optional_setting` reads from `YTDL_MY_OPTIONAL_SETTING`

Pydantic handles case-insensitive matching and type coercion.

### 3. Add to `docker-compose.yml`

```yaml
environment:
  # ... existing vars ...
  YTDL_MY_NEW_SETTING: ${MY_NEW_SETTING:-42}
```

### 4. Use in code via dependency injection

```python
from fastapi import Depends
from app.config import Settings, get_settings

async def my_route(settings: Settings = Depends(get_settings)):
    value = settings.my_new_setting
```

Or outside of a route context:

```python
from app.config import get_settings

settings = get_settings()
value = settings.my_new_setting
```

### 5. Update documentation

- Add the new variable to the config table in `docs/architecture/README.md`.
- If the setting represents a meaningful design choice, consider writing an ADR.

---

## Supported Types

| Python Type | Env Var Example | Notes |
|-------------|----------------|-------|
| `str` | `"hello"` | Default |
| `int` | `"42"` | Auto-parsed |
| `float` | `"3.14"` | Auto-parsed |
| `bool` | `"true"`, `"1"`, `"yes"` | Case-insensitive |
| `str \| None` | `""` or unset | Optional field |
| `list[str]` | `'["a","b"]'` | JSON-encoded in env var |
