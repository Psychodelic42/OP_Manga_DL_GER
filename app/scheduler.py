from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selenium.common.exceptions import WebDriverException

from app.downloader import (
    BASE_URL_TEMPLATE,
    DEFAULT_HEADLESS,
    DEFAULT_PAGE_SLEEP,
    DEFAULT_SAVE_ROOT,
    DownloadConfig,
    _first_image_url,
    setup_driver,
)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BROWSERS = {"auto": "Auto", "chrome": "Chrome", "chromium": "Chromium", "edge": "Edge", "firefox": "Firefox"}
DEFAULT_STATE_FILE = os.getenv("SCHEDULER_STATE_FILE", "/downloads/scheduler_state.json")
MAX_LOG_LINES = 200

LogFn = Callable[[str], None]
ActiveJobFn = Callable[[], bool]
RunScheduledDownloadFn = Callable[[DownloadConfig, int, LogFn], Any]


@dataclass
class SchedulerSettings:
    enabled: bool = False
    timezone: str = "Europe/Berlin"
    download_root: str = DEFAULT_SAVE_ROOT
    browser: str = "Auto"
    headless: bool = DEFAULT_HEADLESS
    page_sleep: float = DEFAULT_PAGE_SLEEP
    delete_pages_after_cbz: bool = True

    last_successful_chapter: Optional[int] = None
    next_chapter_to_check: Optional[int] = None
    initial_chapter: int = 1

    daily_check_enabled: bool = True
    daily_check_time: str = "09:00"
    idle_check_interval_minutes: int = 1440

    release_followup_enabled: bool = True
    release_weekday: str = "Sunday"
    release_check_start_time: str = "08:00"
    release_check_interval_minutes: int = 120
    release_followup_days: int = 2

    max_chapter_lookahead: int = 5
    skip_existing_cbz: bool = True

    last_check_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error: Optional[str] = None
    scheduler_logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_settings() -> SchedulerSettings:
    return SchedulerSettings()


class SchedulerValidationError(ValueError):
    pass


class SchedulerStateStore:
    def __init__(self, path: str | Path = DEFAULT_STATE_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._settings = self._load_or_create()

    def get(self) -> SchedulerSettings:
        with self._lock:
            return SchedulerSettings(**self._settings.to_dict())

    def save(self, settings: SchedulerSettings) -> SchedulerSettings:
        settings = validate_settings(settings.to_dict(), existing=self.get())
        with self._lock:
            self._settings = settings
            self._write_locked(settings)
            return self.get()

    def update(self, **changes: Any) -> SchedulerSettings:
        with self._lock:
            data = self._settings.to_dict()
            data.update(changes)
            self._settings = validate_settings(data, existing=self._settings)
            self._write_locked(self._settings)
            return self.get()

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            logs = [*self._settings.scheduler_logs, f"{timestamp} {message}"][-MAX_LOG_LINES:]
            self._settings.scheduler_logs = logs
            self._write_locked(self._settings)

    def _load_or_create(self) -> SchedulerSettings:
        with self._lock:
            if not self.path.exists():
                settings = default_settings()
                self._settings = settings
                self._write_locked(settings)
                return settings
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                return validate_settings(raw, existing=default_settings())
            except Exception:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                backup = self.path.with_name(f"{self.path.name}.corrupt.{timestamp}")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(self.path), str(backup))
                settings = default_settings()
                settings.scheduler_logs.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [!] Fehlerhafte State-Datei gesichert als {backup.name}; Defaults neu erstellt.")
                self._settings = settings
                self._write_locked(settings)
                return settings

    def _write_locked(self, settings: SchedulerSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def validate_settings(payload: dict[str, Any], existing: SchedulerSettings | None = None) -> SchedulerSettings:
    base = default_settings().to_dict()
    if existing is not None:
        base.update(existing.to_dict())
    allowed = set(base)
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise SchedulerValidationError(f"Unbekannte Einstellung(en): {', '.join(unknown)}")
    base.update(payload)

    try:
        base["enabled"] = _as_bool(base["enabled"])
        base["headless"] = _as_bool(base["headless"])
        base["delete_pages_after_cbz"] = _as_bool(base["delete_pages_after_cbz"])
        base["daily_check_enabled"] = _as_bool(base["daily_check_enabled"])
        base["release_followup_enabled"] = _as_bool(base["release_followup_enabled"])
        base["skip_existing_cbz"] = _as_bool(base["skip_existing_cbz"])

        ZoneInfo(str(base["timezone"]))
        base["timezone"] = str(base["timezone"]).strip()
        base["download_root"] = str(base["download_root"]).strip() or DEFAULT_SAVE_ROOT
        browser_key = str(base["browser"]).strip().lower() or "auto"
        if browser_key not in BROWSERS:
            raise SchedulerValidationError("Browser muss Auto, Chrome, Chromium, Edge oder Firefox sein.")
        base["browser"] = BROWSERS[browser_key]

        base["page_sleep"] = _as_float(base["page_sleep"], "page_sleep", minimum=0.0, maximum=60.0)
        for key in ("last_successful_chapter", "next_chapter_to_check"):
            base[key] = _as_optional_int(base[key], key, minimum=1)
        base["initial_chapter"] = _as_int(base["initial_chapter"], "initial_chapter", minimum=1)
        base["idle_check_interval_minutes"] = _as_int(base["idle_check_interval_minutes"], "idle_check_interval_minutes", minimum=1)
        base["release_check_interval_minutes"] = _as_int(base["release_check_interval_minutes"], "release_check_interval_minutes", minimum=30)
        base["release_followup_days"] = _as_int(base["release_followup_days"], "release_followup_days", minimum=1, maximum=14)
        base["max_chapter_lookahead"] = _as_int(base["max_chapter_lookahead"], "max_chapter_lookahead", minimum=1, maximum=25)

        base["daily_check_time"] = _normalize_time(base["daily_check_time"], "daily_check_time")
        base["release_check_start_time"] = _normalize_time(base["release_check_start_time"], "release_check_start_time")
        if base["release_weekday"] not in WEEKDAYS:
            raise SchedulerValidationError("release_weekday muss ein englischer Wochentag sein.")
        for key in ("last_check_at", "last_success_at"):
            base[key] = _normalize_iso_datetime(base[key], key)
        if base["last_error"] in {"", None}:
            base["last_error"] = None
        else:
            base["last_error"] = str(base["last_error"])
        logs = base.get("scheduler_logs") or []
        if not isinstance(logs, list):
            raise SchedulerValidationError("scheduler_logs muss eine Liste sein.")
        base["scheduler_logs"] = [str(line) for line in logs][-MAX_LOG_LINES:]
    except ZoneInfoNotFoundError as exc:
        raise SchedulerValidationError("Ungültige Zeitzone.") from exc
    return SchedulerSettings(**base)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "ja"}
    return bool(value)


def _as_int(value: Any, name: str, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise SchedulerValidationError(f"{name} muss eine ganze Zahl sein.") from exc
    if minimum is not None and converted < minimum:
        raise SchedulerValidationError(f"{name} muss mindestens {minimum} sein.")
    if maximum is not None and converted > maximum:
        raise SchedulerValidationError(f"{name} darf höchstens {maximum} sein.")
    return converted


def _as_optional_int(value: Any, name: str, minimum: int | None = None) -> Optional[int]:
    if value in {None, ""}:
        return None
    return _as_int(value, name, minimum=minimum)


def _as_float(value: Any, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise SchedulerValidationError(f"{name} muss eine Zahl sein.") from exc
    if minimum is not None and converted < minimum:
        raise SchedulerValidationError(f"{name} muss mindestens {minimum} sein.")
    if maximum is not None and converted > maximum:
        raise SchedulerValidationError(f"{name} darf höchstens {maximum} sein.")
    return converted


def _normalize_time(value: Any, name: str) -> str:
    text = str(value).strip()
    if not re.match(r"^\d{2}:\d{2}$", text):
        raise SchedulerValidationError(f"{name} muss im Format HH:MM sein.")
    hour, minute = [int(part) for part in text.split(":")]
    if hour > 23 or minute > 59:
        raise SchedulerValidationError(f"{name} ist keine gültige Uhrzeit.")
    return text


def _normalize_iso_datetime(value: Any, name: str) -> Optional[str]:
    if value in {None, ""}:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SchedulerValidationError(f"{name} muss ein ISO-Zeitstempel sein.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def next_chapter(settings: SchedulerSettings) -> int:
    if settings.next_chapter_to_check:
        return settings.next_chapter_to_check
    if settings.last_successful_chapter:
        return settings.last_successful_chapter + 1
    return settings.initial_chapter


def parse_local_datetime(value: Optional[str], tz: ZoneInfo) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz)


def combine_local(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hour, minute = [int(part) for part in hhmm.split(":")]
    return datetime.combine(day, datetime_time(hour, minute), tzinfo=tz)


def latest_release_start(now: datetime, settings: SchedulerSettings) -> datetime:
    weekday_index = WEEKDAYS.index(settings.release_weekday)
    days_since = (now.weekday() - weekday_index) % 7
    release_day = now.date() - timedelta(days=days_since)
    start = combine_local(release_day, settings.release_check_start_time, now.tzinfo or ZoneInfo(settings.timezone))
    if start > now:
        start -= timedelta(days=7)
    return start


def in_release_followup_window(now: datetime, settings: SchedulerSettings) -> tuple[bool, datetime, datetime]:
    start = latest_release_start(now, settings)
    end = start + timedelta(days=settings.release_followup_days)
    return start <= now < end, start, end


def should_run_check(settings: SchedulerSettings, now: datetime | None = None) -> tuple[bool, str]:
    if not settings.enabled:
        return False, "disabled"
    tz = ZoneInfo(settings.timezone)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    last_check = parse_local_datetime(settings.last_check_at, tz)
    last_success = parse_local_datetime(settings.last_success_at, tz)

    if settings.release_followup_enabled:
        in_window, release_start, _ = in_release_followup_window(local_now, settings)
        success_in_window = bool(last_success and last_success >= release_start)
        if in_window and not success_in_window:
            if last_check is None or last_check < release_start:
                return True, "release-followup"
            elapsed = local_now - last_check
            if elapsed >= timedelta(minutes=settings.release_check_interval_minutes):
                return True, "release-followup"
            return False, "release-followup-wait"

    if settings.daily_check_enabled:
        daily_at = combine_local(local_now.date(), settings.daily_check_time, tz)
        if local_now >= daily_at and (last_check is None or last_check.date() < local_now.date()):
            return True, "daily"

    if last_check is None:
        if settings.daily_check_enabled:
            return False, "waiting"
        return True, "initial"
    if local_now - last_check >= timedelta(minutes=settings.idle_check_interval_minutes):
        return True, "idle"
    return False, "waiting"


def find_existing_cbz(download_root: str | Path, chapter: int) -> Optional[Path]:
    root = Path(download_root)
    if not root.exists():
        return None
    pattern = re.compile(rf"(?<!\d)0*{re.escape(str(chapter))}(?!\d)")
    for path in root.rglob("*.cbz"):
        comparable = str(path.relative_to(root))
        if pattern.search(comparable):
            return path
    return None


class ChapterScheduler:
    def __init__(
        self,
        store: SchedulerStateStore,
        has_active_job: ActiveJobFn,
        run_scheduled_download: RunScheduledDownloadFn,
        sleep_seconds: int = 45,
    ):
        self.store = store
        self.has_active_job = has_active_job
        self.run_scheduled_download = run_scheduled_download
        self.sleep_seconds = sleep_seconds
        self._stop_event = threading.Event()
        self._trigger_event = threading.Event()
        self._check_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._check_lock.locked()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="chapter-scheduler", daemon=True)
        self._thread.start()
        self.log("[i] Scheduler-Thread gestartet.")

    def stop(self) -> None:
        self._stop_event.set()
        self._trigger_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self.log("[i] Scheduler-Thread gestoppt.")

    def trigger_check(self) -> bool:
        self._trigger_event.set()
        return True

    def run_once(self, reason: str = "manual") -> bool:
        if not self._check_lock.acquire(blocking=False):
            self.log("[i] Scheduler-Prüfung läuft bereits; neuer Trigger ignoriert.")
            return False
        try:
            self._perform_check(reason)
            return True
        finally:
            self._check_lock.release()

    def status(self) -> dict[str, Any]:
        settings = self.store.get()
        due, reason = should_run_check(settings)
        data = settings.to_dict()
        data.update({"running": self.is_running, "next_due_now": due, "next_due_reason": reason})
        return data

    def log(self, message: str) -> None:
        self.store.append_log(message)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            triggered = self._trigger_event.wait(timeout=self.sleep_seconds)
            self._trigger_event.clear()
            if self._stop_event.is_set():
                break
            settings = self.store.get()
            due, reason = (True, "manual-trigger") if triggered else should_run_check(settings)
            if due:
                self.run_once(reason)

    def _perform_check(self, reason: str) -> None:
        settings = self.store.get()
        if reason != "manual-trigger" and not settings.enabled:
            return
        if self.has_active_job():
            self.log("[i] Kein Scheduler-Download gestartet: Es läuft bereits ein Download-Job.")
            return

        checked_at = datetime.now(timezone.utc).isoformat()
        self.store.update(last_check_at=checked_at, last_error=None)
        self.log(f"[i] Starte Kapitelprüfung ({reason}).")

        driver = None
        try:
            driver = setup_driver(settings.browser, settings.headless)
            start = next_chapter(settings)
            for chapter in range(start, start + settings.max_chapter_lookahead):
                if settings.skip_existing_cbz:
                    existing = find_existing_cbz(settings.download_root, chapter)
                    if existing:
                        self.log(f"[i] Kapitel {chapter} bereits als CBZ vorhanden: {existing}")
                        self._mark_success(chapter, skipped=True)
                        continue

                if not self._chapter_available(driver, chapter, settings):
                    self.store.update(next_chapter_to_check=chapter)
                    self.log(f"[-] Kapitel {chapter} ist noch nicht verfügbar.")
                    break

                if self.has_active_job():
                    self.log("[i] Download übersprungen: Zwischenzeitlich wurde ein anderer Job gestartet.")
                    break

                config = DownloadConfig(
                    save_root=settings.download_root,
                    browser=settings.browser,
                    headless=settings.headless,
                    page_sleep=settings.page_sleep,
                    chapters_spec=str(chapter),
                    delete_pages_after_cbz=settings.delete_pages_after_cbz,
                )
                self.log(f"[+] Kapitel {chapter} verfügbar; Scheduler startet Download-Job.")
                job = self.run_scheduled_download(config, chapter, lambda msg: self.log(f"[job {chapter}] {msg}"))
                if getattr(job, "status", None) == "finished":
                    self._mark_success(chapter)
                    continue
                error = getattr(job, "error", "") or f"Scheduler-Download für Kapitel {chapter} nicht erfolgreich."
                self.store.update(last_error=error, next_chapter_to_check=chapter)
                self.log(f"[!] {error}")
                break
        except Exception as exc:
            self.store.update(last_error=str(exc))
            self.log(f"[!] Scheduler-Fehler: {exc}")
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass

    def _mark_success(self, chapter: int, skipped: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        settings = self.store.get()
        highest = max(chapter, settings.last_successful_chapter or 0)
        changes = {
            "last_successful_chapter": highest,
            "next_chapter_to_check": highest + 1,
            "last_error": None,
        }
        if not skipped:
            changes["last_success_at"] = now
        self.store.update(**changes)
        action = "als vorhanden markiert" if skipped else "erfolgreich abgeschlossen"
        self.log(f"[✔] Kapitel {chapter} {action}; nächster Check: {highest + 1}.")

    def _chapter_available(self, driver: Any, chapter: int, settings: SchedulerSettings) -> bool:
        url = BASE_URL_TEMPLATE.format(chapter=chapter, page=1)
        try:
            driver.get(url)
            time.sleep(settings.page_sleep)
            return bool(_first_image_url(driver))
        except WebDriverException as exc:
            self.log(f"[!] Verfügbarkeitsprüfung für Kapitel {chapter} fehlgeschlagen: {exc}")
            return False
