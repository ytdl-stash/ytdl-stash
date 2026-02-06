# Recipe: Add a New API Route

How to add a new endpoint to the FastAPI application.

---

## Adding a Route to an Existing Router

### 1. Open the relevant route module (e.g., `app/routes/channels.py`)

### 2. Add the route function

```python
@router.post("/{channel_id}/check-now")
async def check_now(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # ... business logic ...

    # Return HTML for HTMX, or redirect for full-page requests
    if request.headers.get("HX-Request"):
        return HTMLResponse("<span>Scan started</span>")
    return RedirectResponse(url="/channels", status_code=303)
```

### 3. Key rules

- Always use `Depends(get_db)` for database sessions.
- Always use `Depends(get_settings)` for configuration.
- Always include `request: Request` if you need to detect HTMX or render templates.
- Use `HTTPException` for error responses.
- For POST/PUT/DELETE with HTMX: return HTML fragments.
- For POST/PUT/DELETE without HTMX: return `RedirectResponse` with `status_code=303` (POST-redirect-GET pattern).

---

## Creating a New Router Module

### 1. Create `app/routes/my_feature.py`

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import Settings, get_settings
from app.main import templates

router = APIRouter(prefix="/my-feature", tags=["my-feature"])

@router.get("")
async def list_items(request: Request, db: AsyncSession = Depends(get_db)):
    # ... query logic ...
    return templates.TemplateResponse("my_feature/list.html", {
        "request": request,
        "items": items,
    })
```

### 2. Register the router in `app/main.py`

```python
def create_app() -> FastAPI:
    app = FastAPI(title="ytdl-stash", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    from app.routes import dashboard, channels, videos, settings, my_feature
    app.include_router(dashboard.router)
    app.include_router(channels.router)
    app.include_router(videos.router)
    app.include_router(settings.router)
    app.include_router(my_feature.router)  # New router

    return app
```

### 3. Create the template

Create `app/templates/my_feature/list.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>My Feature</h1>
<!-- ... -->
{% endblock %}
```

---

## Route Naming Conventions

| HTTP Method | URL Pattern | Purpose | Returns |
|-------------|-------------|---------|---------|
| `GET` | `/things` | List all | Full page template |
| `GET` | `/things/{id}` | Detail view | Full page template |
| `POST` | `/things` | Create new | Redirect or HTML fragment |
| `PUT` | `/things/{id}` | Update | HTML fragment (HTMX) |
| `DELETE` | `/things/{id}` | Delete | Empty or HTML fragment |
| `POST` | `/things/{id}/action` | Custom action | HTML fragment |

---

## Form Handling

For `POST` routes that receive form data:

```python
from fastapi import Form

@router.post("")
async def create_channel(
    request: Request,
    url: str = Form(...),
    name: str = Form(""),
    check_interval_hours: int = Form(6),
    db: AsyncSession = Depends(get_db),
):
    channel = Channel(
        url=url,
        name=name or _derive_name(url),
        check_interval_hours=check_interval_hours,
        site=_derive_site(url),
    )
    db.add(channel)
    await db.flush()
    # ...
```

**Rule**: Use `Form(...)` for required fields, `Form(default)` for optional fields. The `python-multipart` dependency is required for form parsing (already in requirements.txt).

---

## Update Documentation

After adding a route:
1. Update `docs/architecture/README.md` if it changes the component map.
2. Verify the route appears in the OpenAPI docs at `/docs`.
