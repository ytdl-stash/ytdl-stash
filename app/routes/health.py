"""Health check route: GET /health for Docker and load balancers."""

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.database import async_session
from app.stash_client import StashClient

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    """Return status, db, and stash connectivity. Kept fast for health checks."""
    db_ok = async_session is not None
    async with StashClient(settings.stash_url, settings.stash_api_key) as stash:
        stash_ok = await stash.health_check()
    status = "ok" if db_ok else "degraded"
    return {"status": status, "db": db_ok, "stash": stash_ok}
