from __future__ import annotations

import threading
import uuid
from urllib.parse import quote
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.downloader import DEFAULT_HEADLESS, DEFAULT_PAGE_SLEEP, DEFAULT_SAVE_ROOT, DownloadConfig, run_download
from app.scheduler import (
    BROWSERS,
    DEFAULT_STATE_FILE,
    WEEKDAYS,
    ChapterScheduler,
    SchedulerStateStore,
    SchedulerValidationError,
    validate_settings,
)

JobStatus = Literal["queued", "running", "finished", "failed", "cancelled"]


@dataclass
class JobState:
    id: str
    config: DownloadConfig
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    logs: list[str] = field(default_factory=list)
    error: str = ""
    stop_flag: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    source: str = "manual"
    is_scheduled: bool = False

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"{timestamp} {message}")


scheduler_store = SchedulerStateStore(DEFAULT_STATE_FILE)
scheduler: ChapterScheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(title="One Piece Manga Downloader", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
_jobs: dict[str, JobState] = {}
_jobs_lock = threading.Lock()


def _has_active_job() -> bool:
    with _jobs_lock:
        return any(job.status in {"queued", "running"} for job in _jobs.values())


def _create_job(config: DownloadConfig, source: str = "manual", start_thread: bool = True) -> JobState:
    job = JobState(id=str(uuid.uuid4()), config=config, source=source, is_scheduled=(source == "scheduler"))
    if start_thread:
        job.thread = threading.Thread(target=_run_job, args=(job,), daemon=True)
    with _jobs_lock:
        _jobs[job.id] = job
    if job.thread is not None:
        job.thread.start()
    return job


def _run_scheduled_download(config: DownloadConfig, chapter: int, scheduler_log) -> JobState:
    job = _create_job(config, source="scheduler", start_thread=False)
    job.log(f"[i] Automatischer Scheduler-Job für Kapitel {chapter}.")

    original_log = job.log

    def combined_log(message: str) -> None:
        original_log(message)
        scheduler_log(message)

    job.log = combined_log  # type: ignore[method-assign]
    _run_job(job)
    return job


scheduler = ChapterScheduler(scheduler_store, _has_active_job, _run_scheduled_download)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda item: item.created_at, reverse=True)[:20]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": jobs,
            "default_save_root": DEFAULT_SAVE_ROOT,
            "default_page_sleep": DEFAULT_PAGE_SLEEP,
            "default_headless": DEFAULT_HEADLESS,
            "scheduler_status": scheduler.status(),
        },
    )


@app.post("/jobs")
def create_job(
    chapters_spec: str = Form(...),
    save_root: str = Form(DEFAULT_SAVE_ROOT),
    browser: str = Form("Auto"),
    page_sleep: float = Form(DEFAULT_PAGE_SLEEP),
    headless: bool = Form(False),
    delete_pages_after_cbz: bool = Form(False),
):
    if page_sleep < 0:
        raise HTTPException(status_code=400, detail="Pause je Seite darf nicht negativ sein.")
    if not chapters_spec.strip():
        raise HTTPException(status_code=400, detail="Bitte mindestens ein Kapitel angeben.")

    config = DownloadConfig(
        save_root=save_root.strip() or DEFAULT_SAVE_ROOT,
        browser=browser,
        headless=headless,
        page_sleep=page_sleep,
        chapters_spec=chapters_spec,
        delete_pages_after_cbz=delete_pages_after_cbz,
    )
    job = _create_job(config, source="manual")
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> HTMLResponse:
    job = _get_job(job_id)
    return templates.TemplateResponse("job.html", {"request": request, "job": job})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": scheduler_store.get(),
            "status": scheduler.status(),
            "weekdays": WEEKDAYS,
            "browsers": sorted(set(BROWSERS.values())),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/settings")
def save_settings_form(
    enabled: bool = Form(False),
    timezone_name: str = Form(..., alias="timezone"),
    download_root: str = Form(...),
    browser: str = Form("Auto"),
    headless: bool = Form(False),
    page_sleep: float = Form(DEFAULT_PAGE_SLEEP),
    delete_pages_after_cbz: bool = Form(False),
    last_successful_chapter: str = Form(""),
    next_chapter_to_check: str = Form(""),
    initial_chapter: int = Form(1),
    daily_check_enabled: bool = Form(False),
    daily_check_time: str = Form("09:00"),
    release_followup_enabled: bool = Form(False),
    release_weekday: str = Form("Sunday"),
    release_check_start_time: str = Form("08:00"),
    release_check_interval_minutes: int = Form(120),
    release_followup_days: int = Form(2),
    max_chapter_lookahead: int = Form(5),
    skip_existing_cbz: bool = Form(False),
):
    payload = {
        "enabled": enabled,
        "timezone": timezone_name,
        "download_root": download_root,
        "browser": browser,
        "headless": headless,
        "page_sleep": page_sleep,
        "delete_pages_after_cbz": delete_pages_after_cbz,
        "last_successful_chapter": last_successful_chapter,
        "next_chapter_to_check": next_chapter_to_check,
        "initial_chapter": initial_chapter,
        "daily_check_enabled": daily_check_enabled,
        "daily_check_time": daily_check_time,
        "release_followup_enabled": release_followup_enabled,
        "release_weekday": release_weekday,
        "release_check_start_time": release_check_start_time,
        "release_check_interval_minutes": release_check_interval_minutes,
        "release_followup_days": release_followup_days,
        "max_chapter_lookahead": max_chapter_lookahead,
        "skip_existing_cbz": skip_existing_cbz,
    }
    try:
        scheduler_store.save(validate_settings(payload, existing=scheduler_store.get()))
    except SchedulerValidationError as exc:
        return RedirectResponse(url=f"/settings?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url=f"/settings?message={quote('Einstellungen gespeichert')}", status_code=303)


@app.post("/settings/check-now")
def settings_check_now():
    scheduler.trigger_check()
    return RedirectResponse(url=f"/settings?message={quote('Prüfung wurde gestartet')}", status_code=303)


@app.post("/settings/reset-error")
def settings_reset_error():
    scheduler_store.update(last_error=None)
    return RedirectResponse(url=f"/settings?message={quote('Fehlerstatus zurückgesetzt')}", status_code=303)


@app.post("/settings/reset-state")
def settings_reset_state(confirm: str = Form("")):
    if confirm != "RESET":
        return RedirectResponse(url=f"/settings?error={quote('Zum Zurücksetzen bitte RESET eingeben')}", status_code=303)
    scheduler_store.update(last_successful_chapter=None, next_chapter_to_check=None)
    return RedirectResponse(url=f"/settings?message={quote('Kapitelstatus zurückgesetzt')}", status_code=303)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = _get_job(job_id)
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "logs": job.logs,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "source": job.source,
        "is_scheduled": job.is_scheduled,
        "config": {
            "chapters_spec": job.config.chapters_spec,
            "save_root": job.config.save_root,
            "browser": job.config.browser,
            "headless": job.config.headless,
            "page_sleep": job.config.page_sleep,
            "delete_pages_after_cbz": job.config.delete_pages_after_cbz,
        },
    }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    job = _get_job(job_id)
    if job.status in {"finished", "failed", "cancelled"}:
        return {"status": job.status}
    job.stop_flag.set()
    job.log("[!] Abbruch wurde über die Weboberfläche angefordert.")
    return {"status": "cancelling"}


@app.get("/api/scheduler/settings")
def api_scheduler_settings() -> dict[str, Any]:
    return scheduler_store.get().to_dict()


@app.post("/api/scheduler/settings")
async def api_save_scheduler_settings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON-Body muss ein Objekt sein.")
    try:
        return scheduler_store.save(validate_settings(payload, existing=scheduler_store.get())).to_dict()
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/scheduler/status")
def api_scheduler_status() -> dict[str, Any]:
    return scheduler.status()


@app.post("/api/scheduler/check-now")
def api_scheduler_check_now() -> dict[str, str]:
    scheduler.trigger_check()
    return {"status": "queued"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _run_job(job: JobState) -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    try:
        summary = run_download(job.config, job.log, job.stop_flag)
        job.status = "cancelled" if summary.stopped else "finished"
    except Exception as exc:
        job.error = str(exc)
        job.log(f"[!] Fehler: {exc}")
        job.status = "failed"
    finally:
        job.finished_at = datetime.now(timezone.utc)


def _get_job(job_id: str) -> JobState:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden.")
    return job
