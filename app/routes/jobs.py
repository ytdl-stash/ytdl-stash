"""Job routes: list jobs, get status, trigger manual runs, pause/resume controls."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.main import templates
from app.download_control import download_control, persist_pause_state
from app.models import Video
from app.scheduler import job_registry, stop_job, trigger_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def jobs_page(request: Request):
    """Full jobs page listing every triggerable job with status and controls."""
    jobs = list(job_registry.values())
    return templates.TemplateResponse(
        "jobs/list.html",
        {
            "request": request,
            "jobs": jobs,
            "downloads_paused": download_control.is_downloads_paused(),
            "channels_paused": download_control.is_channels_paused(),
        },
    )


@router.get("/pause-banner")
async def pause_banner(request: Request):
    """HTMX partial: global pause banner. Refreshed when pause state changes."""
    return templates.TemplateResponse(
        "components/_pause_banner.html",
        {"request": request},
    )


@router.get("/pause-toggle/{pause_key}")
async def pause_toggle(pause_key: str, request: Request):
    """HTMX partial: pause/resume toggle button for a specific key."""
    if pause_key not in ("downloads", "channels"):
        raise HTTPException(status_code=404, detail="Unknown pause key")

    labels = {"downloads": "Downloads", "channels": "Channel Scans"}
    is_paused = (
        download_control.is_downloads_paused()
        if pause_key == "downloads"
        else download_control.is_channels_paused()
    )
    return templates.TemplateResponse(
        "components/_pause_toggle.html",
        {
            "request": request,
            "pause_key": pause_key,
            "is_paused": is_paused,
            "label": labels[pause_key],
        },
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


@router.post("/{job_id}/stop")
async def stop(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Request a running job to stop.

    For the download processor, also request cancellation of the active video
    so the yt-dlp worker thread can be cooperatively aborted.
    """
    info = job_registry.get(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Unknown job")

    # Special-case: stopping the download processor should stop the active download.
    if job_id == "process_downloads":
        # Request cooperative cancellation (yt-dlp hook checks this flag).
        # With concurrency enabled there may be multiple active videos.
        active_ids = download_control.get_active_ids()
        videos: list[Video] = []

        if active_ids:
            for vid in active_ids:
                v = await db.get(Video, vid)
                if v is not None:
                    videos.append(v)
        else:
            # Fallback: find any in-flight rows (covers restarts / control-plane reset).
            result = await db.execute(
                select(Video).where(
                    Video.status.in_(["downloading", "downloaded", "importing"])
                )
            )
            videos = list(result.scalars().all())

        changed = False
        for video in videos:
            download_control.request_cancel(video.id)
            if video.status in {"downloading", "downloaded", "importing"}:
                video.status = "cancelling"
                changed = True
        if changed:
            await db.commit()

        # Do not cancel the job task: the download runs in a worker thread and must
        # be stopped cooperatively via the yt-dlp hook.
        stopped = True
    else:
        stopped = stop_job(job_id)

    if not stopped:
        logger.info("Job %s stop requested but no running task found", job_id)
    else:
        logger.info("Job %s stop requested", job_id)

    if request.headers.get("HX-Request"):
        stop_target = request.headers.get("HX-Target", "")
        if stop_target.startswith("job-row-"):
            return templates.TemplateResponse(
                "jobs/_job_row.html",
                {"request": request, "job": info},
            )
        return templates.TemplateResponse(
            "jobs/_inline_status.html",
            {"request": request, "job": info},
        )
    return HTMLResponse(
        f"Job {job_id} stop requested",
        status_code=200,
    )


# ---------------------------------------------------------------------------
# Pause / resume controls
# ---------------------------------------------------------------------------


@router.post("/downloads/pause")
async def pause_downloads(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Pause all downloads: cancel active downloads and prevent new ones from starting."""
    download_control.set_downloads_paused(True)
    await persist_pause_state("downloads_paused", True)

    # Hard pause: cancel all active downloads
    active_ids = download_control.get_active_ids()
    if active_ids:
        for vid in active_ids:
            download_control.request_cancel(vid)
            video = await db.get(Video, vid)
            if video and video.status in {"downloading", "downloaded", "importing"}:
                video.status = "cancelling"
        # get_db dependency auto-commits on success
        logger.info("Pause downloads: cancelled %d active download(s)", len(active_ids))

    logger.info("Downloads paused")

    if request.headers.get("HX-Request"):
        resp = templates.TemplateResponse(
            "components/_pause_toggle.html",
            {
                "request": request,
                "pause_key": "downloads",
                "is_paused": True,
                "label": "Downloads",
            },
        )
        resp.headers["HX-Trigger"] = "pauseStateChanged"
        return resp
    return HTMLResponse("Downloads paused", status_code=200)


@router.post("/downloads/resume")
async def resume_downloads(request: Request):
    """Resume downloads: allow the scheduler to pick up pending videos again."""
    download_control.set_downloads_paused(False)
    await persist_pause_state("downloads_paused", False)
    logger.info("Downloads resumed")

    if request.headers.get("HX-Request"):
        resp = templates.TemplateResponse(
            "components/_pause_toggle.html",
            {
                "request": request,
                "pause_key": "downloads",
                "is_paused": False,
                "label": "Downloads",
            },
        )
        resp.headers["HX-Trigger"] = "pauseStateChanged"
        return resp
    return HTMLResponse("Downloads resumed", status_code=200)


@router.post("/channels/pause")
async def pause_channels(request: Request):
    """Pause channel scanning: prevent the scheduler from checking channels for new videos."""
    download_control.set_channels_paused(True)
    await persist_pause_state("channels_paused", True)
    logger.info("Channel scanning paused")

    if request.headers.get("HX-Request"):
        resp = templates.TemplateResponse(
            "components/_pause_toggle.html",
            {
                "request": request,
                "pause_key": "channels",
                "is_paused": True,
                "label": "Channel Scans",
            },
        )
        resp.headers["HX-Trigger"] = "pauseStateChanged"
        return resp
    return HTMLResponse("Channel scanning paused", status_code=200)


@router.post("/channels/resume")
async def resume_channels(request: Request):
    """Resume channel scanning: allow the scheduler to check channels again."""
    download_control.set_channels_paused(False)
    await persist_pause_state("channels_paused", False)
    logger.info("Channel scanning resumed")

    if request.headers.get("HX-Request"):
        resp = templates.TemplateResponse(
            "components/_pause_toggle.html",
            {
                "request": request,
                "pause_key": "channels",
                "is_paused": False,
                "label": "Channel Scans",
            },
        )
        resp.headers["HX-Trigger"] = "pauseStateChanged"
        return resp
    return HTMLResponse("Channel scanning resumed", status_code=200)
