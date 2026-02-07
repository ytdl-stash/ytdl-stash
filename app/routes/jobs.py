"""Job routes: list jobs, get status, trigger manual runs."""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.main import templates
from app.scheduler import job_registry, trigger_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def jobs_page(request: Request):
    """Full jobs page listing every triggerable job with status and controls."""
    jobs = list(job_registry.values())
    return templates.TemplateResponse(
        "jobs/list.html",
        {"request": request, "jobs": jobs},
    )


@router.get("/status")
async def jobs_status(request: Request):
    """HTMX partial: updated job rows for polling."""
    jobs = list(job_registry.values())
    return templates.TemplateResponse(
        "jobs/_job_rows.html",
        {"request": request, "jobs": jobs},
    )


@router.get("/{job_id}/inline-status")
async def inline_status(job_id: str, request: Request):
    """HTMX partial: inline status for contextual buttons. Self-polls while running,
    returns original button when idle."""
    info = job_registry.get(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return templates.TemplateResponse(
        "jobs/_inline_status.html",
        {"request": request, "job": info},
    )


@router.post("/{job_id}/trigger")
async def trigger(job_id: str, request: Request):
    """Trigger a job manually. Returns the updated job row (HTMX) or redirect."""
    info = job_registry.get(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Unknown job")

    started = trigger_job(job_id)
    if not started:
        logger.info("Job %s trigger skipped — already running", job_id)
    else:
        logger.info("Job %s triggered manually", job_id)

    if request.headers.get("HX-Request"):
        # If the trigger came from the jobs page table, return a full <tr>.
        # If it came from a contextual button (channels/videos page), return
        # a compact inline snippet that self-polls until the job finishes.
        trigger_target = request.headers.get("HX-Target", "")
        if trigger_target.startswith("job-row-"):
            return templates.TemplateResponse(
                "jobs/_job_row.html",
                {"request": request, "job": info},
            )
        # Contextual inline response (self-polls while running)
        return templates.TemplateResponse(
            "jobs/_inline_status.html",
            {"request": request, "job": info},
        )
    return HTMLResponse(
        f"Job {job_id} {'started' if started else 'already running'}",
        status_code=200,
    )
