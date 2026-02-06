# FastAPI Patterns

Reference patterns for how this project uses FastAPI. Read this before adding routes, dependencies, or modifying the app lifecycle.

---

## App Factory Pattern

The app is created via `create_app()` in `app/main.py`. This allows testing with different configurations.

```python
# app/main.py
def create_app() -> FastAPI:
    app = FastAPI(title="ytdl-stash", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    # app.include_router(channels.router)  # Added in Phase 7
    return app

app = create_app()
```

**Rule**: Always add new routers inside `create_app()` using `app.include_router()`.

---

## Lifespan Pattern

We use the modern async context manager lifespan (NOT the deprecated `@app.on_event("startup")` decorators).

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # === STARTUP ===
    settings = get_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.download_dir).mkdir(parents=True, exist_ok=True)
    await init_db(settings)
    start_scheduler()          # Phase 6
    yield
    # === SHUTDOWN ===
    stop_scheduler()           # Phase 6
```

**Rule**: All startup/shutdown logic goes in the `lifespan` function. Never use `@app.on_event`.

---

## Dependency Injection

FastAPI's `Depends()` is used for settings and database sessions.

### Settings dependency:
```python
from fastapi import Depends
from app.config import Settings, get_settings

@router.get("/settings")
async def settings_page(settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
    })
```

### Database session dependency:
```python
from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

@router.get("/channels")
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel))
    channels = result.scalars().all()
    ...
```

**Rule**: Never instantiate `Settings()` or create DB sessions manually in route handlers. Always use `Depends()`.

**Note**: `get_settings()` is decorated with `@lru_cache` so the `Settings` object is created once and reused. This avoids re-parsing environment variables on every request.

---

## Router Organization

Routes are organized into separate router modules under `app/routes/`:

```python
# app/routes/channels.py
from fastapi import APIRouter

router = APIRouter(prefix="/channels", tags=["channels"])

@router.get("")
async def list_channels(...):
    ...

@router.post("")
async def add_channel(...):
    ...
```

```python
# app/main.py (inside create_app)
from app.routes import channels, videos, settings, dashboard

app.include_router(dashboard.router)
app.include_router(channels.router)
app.include_router(videos.router)
app.include_router(settings.router)
```

**Rule**: Each route module defines its own `router = APIRouter(prefix=..., tags=[...])`. The `tags` parameter groups endpoints in the OpenAPI docs.

---

## Template Responses

Templates are rendered using `Jinja2Templates`. The `templates` instance is module-level in `main.py` and imported by route modules.

```python
from app.main import templates

@router.get("/channels")
async def list_channels(request: Request, db: AsyncSession = Depends(get_db)):
    channels = (await db.execute(select(Channel))).scalars().all()
    return templates.TemplateResponse("channels/list.html", {
        "request": request,  # REQUIRED by Jinja2Templates
        "channels": channels,
    })
```

**Rule**: Always include `"request": request` in the template context. Jinja2Templates requires it.

---

## HTMX Partial Responses

Some routes serve both full pages and HTMX partial fragments. Detect HTMX requests via the `HX-Request` header:

```python
@router.get("/videos")
async def list_videos(request: Request, db: AsyncSession = Depends(get_db)):
    videos = (await db.execute(select(Video))).scalars().all()

    # If HTMX request, return just the table body fragment
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("videos/_table_body.html", {
            "request": request,
            "videos": videos,
        })

    # Otherwise return the full page
    return templates.TemplateResponse("videos/list.html", {
        "request": request,
        "videos": videos,
    })
```

**Rule**: HTMX partial templates go in files prefixed with `_` (e.g., `_table_body.html`).

---

## Error Handling in Routes

Return user-friendly errors by catching exceptions in route handlers:

```python
@router.post("/channels/{channel_id}/check-now")
async def check_now(channel_id: int, db: AsyncSession = Depends(get_db)):
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    # ... trigger scan
```

For HTMX endpoints, return error HTML fragments instead of JSON errors:

```python
except Exception as e:
    return HTMLResponse(
        f'<span class="error">Error: {e}</span>',
        status_code=500,
    )
```
