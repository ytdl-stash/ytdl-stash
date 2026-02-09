"""Health check route: GET /health for Docker and load balancers."""

from fastapi import APIRouter, Depends

from app import database as db_module
from app.config import Settings, get_settings
from app.stash_client import StashClient

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    """Return status, db, and stash connectivity. Kept fast for health checks."""
    db_ok = db_module.async_session is not None
    async with StashClient.from_settings(settings) as stash:
        stash_ok = await stash.health_check()
    status = "ok" if db_ok else "degraded"
    return {"status": status, "db": db_ok, "stash": stash_ok}
