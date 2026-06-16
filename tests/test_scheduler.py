from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.downloader import ChapterResult, DownloadSummary
from app.scheduler import (
    MIN_PAGES_FOR_CBZ,
    ChapterScheduler,
    SchedulerStateStore,
    SchedulerValidationError,
    _job_has_successful_download,
    default_settings,
    find_existing_cbz,
    in_release_followup_window,
    next_chapter,
    should_run_check,
    validate_settings,
)


def test_scheduler_settings_default_creation(tmp_path: Path):
    state_file = tmp_path / "scheduler_state.json"
    store = SchedulerStateStore(state_file)

    settings = store.get()

    assert state_file.exists()
    assert settings.enabled is False
    assert settings.timezone == "Europe/Berlin"
    assert settings.download_root
    assert settings.release_check_interval_minutes == 120
    assert settings.scheduler_logs == []


def test_scheduler_settings_load_save_roundtrip(tmp_path: Path):
    state_file = tmp_path / "scheduler_state.json"
    store = SchedulerStateStore(state_file)
    settings = store.get()
    settings.enabled = True
    settings.last_successful_chapter = 1162
    settings.next_chapter_to_check = 1163

    store.save(settings)
    reloaded = SchedulerStateStore(state_file).get()

    assert reloaded.enabled is True
    assert reloaded.last_successful_chapter == 1162
    assert reloaded.next_chapter_to_check == 1163
    assert json.loads(state_file.read_text())["enabled"] is True


def test_corrupt_state_file_is_backed_up_and_recreated(tmp_path: Path):
    state_file = tmp_path / "scheduler_state.json"
    state_file.write_text("{not-json", encoding="utf-8")

    store = SchedulerStateStore(state_file)

    assert store.get().enabled is False
    assert state_file.exists()
    backups = list(tmp_path.glob("scheduler_state.json.corrupt.*"))
    assert len(backups) == 1
    assert "not-json" in backups[0].read_text(encoding="utf-8")


def test_next_chapter_decision_logic():
    settings = default_settings()
    settings.initial_chapter = 1000
    assert next_chapter(settings) == 1000

    settings.last_successful_chapter = 1162
    assert next_chapter(settings) == 1163

    settings.next_chapter_to_check = 1170
    assert next_chapter(settings) == 1170


def test_daily_next_check_decision_logic():
    settings = default_settings()
    settings.enabled = True
    settings.daily_check_time = "09:00"
    settings.release_followup_enabled = False
    tz = ZoneInfo(settings.timezone)

    due, reason = should_run_check(settings, datetime(2026, 6, 2, 8, 59, tzinfo=tz))
    assert due is False
    assert reason == "waiting"

    due, reason = should_run_check(settings, datetime(2026, 6, 2, 9, 1, tzinfo=tz))
    assert due is True
    assert reason == "daily"

    settings.last_check_at = datetime(2026, 6, 2, 8, 0, tzinfo=tz).isoformat()
    due, reason = should_run_check(settings, datetime(2026, 6, 2, 9, 1, tzinfo=tz))
    assert due is False
    assert reason == "waiting"

    settings.last_check_at = datetime(2026, 6, 1, 10, 0, tzinfo=tz).isoformat()
    due, reason = should_run_check(settings, datetime(2026, 6, 2, 9, 1, tzinfo=tz))
    assert due is True
    assert reason == "daily"


def test_release_followup_window_logic_and_success_stops_followup():
    settings = default_settings()
    settings.enabled = True
    settings.release_weekday = "Sunday"
    settings.release_check_start_time = "08:00"
    settings.release_check_interval_minutes = 120
    settings.release_followup_days = 2
    settings.daily_check_enabled = False
    tz = ZoneInfo(settings.timezone)
    sunday_0900 = datetime(2026, 6, 7, 9, 0, tzinfo=tz)

    in_window, start, end = in_release_followup_window(sunday_0900, settings)
    assert in_window is True
    assert start == datetime(2026, 6, 7, 8, 0, tzinfo=tz)
    assert end == datetime(2026, 6, 9, 8, 0, tzinfo=tz)

    due, reason = should_run_check(settings, sunday_0900)
    assert due is True
    assert reason == "release-followup"

    settings.last_check_at = datetime(2026, 6, 7, 9, 30, tzinfo=tz).isoformat()
    due, reason = should_run_check(settings, datetime(2026, 6, 7, 10, 0, tzinfo=tz))
    assert due is False
    assert reason == "release-followup-wait"

    settings.last_check_at = datetime(2026, 6, 7, 9, 30, tzinfo=tz).isoformat()
    due, reason = should_run_check(settings, datetime(2026, 6, 7, 11, 30, tzinfo=tz))
    assert due is True
    assert reason == "release-followup"

    settings.last_success_at = datetime(2026, 6, 7, 9, 45, tzinfo=tz).isoformat()
    due, reason = should_run_check(settings, datetime(2026, 6, 7, 12, 0, tzinfo=tz))
    assert due is False
    assert reason == "waiting"


def test_skip_existing_cbz_detection_is_tolerant(tmp_path: Path):
    nested = tmp_path / "One Piece"
    nested.mkdir()
    cbz = nested / "One Piece - Kapitel 01162 - Titel.cbz"
    cbz.write_bytes(b"PK")
    (nested / "One Piece - Kapitel 11162.cbz").write_bytes(b"PK")

    assert find_existing_cbz(tmp_path, 1162) == cbz
    assert find_existing_cbz(tmp_path, 1163) is None


def test_finished_job_needs_complete_download_summary():
    incomplete_summary = DownloadSummary(
        chapters=[ChapterResult(chapter=1163, pages=2, complete=False)]
    )
    complete_summary = DownloadSummary(
        chapters=[ChapterResult(chapter=1163, pages=MIN_PAGES_FOR_CBZ, complete=True)]
    )

    assert _job_has_successful_download(
        SimpleNamespace(status="finished", error="", summary=incomplete_summary)
    ) is False
    assert _job_has_successful_download(
        SimpleNamespace(status="finished", error="", summary=complete_summary)
    ) is True
    assert _job_has_successful_download(
        SimpleNamespace(status="failed", error="nope", summary=complete_summary)
    ) is False


def test_chapter_available_requires_minimum_pages(tmp_path: Path, monkeypatch):
    state_file = tmp_path / "scheduler_state.json"
    store = SchedulerStateStore(state_file)
    scheduler = ChapterScheduler(store, lambda: False, lambda *_args: None)
    settings = store.get()

    monkeypatch.setattr("app.scheduler.count_available_chapter_images", lambda **_kwargs: 2)
    assert scheduler._chapter_available(object(), 1163, settings) is False

    monkeypatch.setattr(
        "app.scheduler.count_available_chapter_images",
        lambda **_kwargs: MIN_PAGES_FOR_CBZ,
    )
    assert scheduler._chapter_available(object(), 1163, settings) is True


def test_api_settings_validation_rejects_invalid_time():
    with pytest.raises(SchedulerValidationError):
        validate_settings({"daily_check_time": "25:00"}, existing=default_settings())
