from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.downloader import DEFAULT_HEADLESS, DEFAULT_PAGE_SLEEP, DEFAULT_SAVE_ROOT, DownloadConfig, run_download

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

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"{timestamp} {message}")


app = FastAPI(title="One Piece Manga Downloader")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
_jobs: dict[str, JobState] = {}
_jobs_lock = threading.Lock()


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
    job = JobState(id=str(uuid.uuid4()), config=config)
    job.thread = threading.Thread(target=_run_job, args=(job,), daemon=True)
    with _jobs_lock:
        _jobs[job.id] = job
    job.thread.start()
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> HTMLResponse:
    job = _get_job(job_id)
    return templates.TemplateResponse("job.html", {"request": request, "job": job})


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
