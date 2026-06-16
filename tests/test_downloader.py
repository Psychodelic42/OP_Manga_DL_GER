from pathlib import Path
from threading import Event
import zipfile

import pytest

from app import downloader
from app.downloader import (
    clean_title,
    count_available_chapter_images,
    download_chapter,
    expand_chapter_spec,
    sanitize_filename,
)


class FakeDriver:
    def __init__(self, images_by_page: dict[int, str | None]):
        self.images_by_page = images_by_page
        self.current_page: int | None = None
        self.visited_pages: list[int] = []

    def get(self, url: str) -> None:
        page = int(url.rstrip("/").split("/")[-1])
        self.current_page = page
        self.visited_pages.append(page)


def fake_first_image_url(driver: FakeDriver) -> str | None:
    assert driver.current_page is not None
    return driver.images_by_page.get(driver.current_page)


def fake_save_image(_session, img_url: str, out_path: str | Path) -> bool:
    Path(out_path).write_bytes(img_url.encode("utf-8"))
    return True


def test_expand_chapter_spec_single_range_and_list():
    assert expand_chapter_spec("1162") == [1162]
    assert expand_chapter_spec("1164-1162") == [1162, 1163, 1164]
    assert expand_chapter_spec("1150,1152-1154,1152") == [1150, 1152, 1153, 1154]


def test_expand_chapter_spec_rejects_invalid_input():
    with pytest.raises(ValueError):
        expand_chapter_spec("abc")


def test_clean_title_removes_page_suffix_and_unescapes_html():
    assert clean_title("Ein &amp; Zwei (Seite 1)") == "Ein & Zwei"


def test_sanitize_filename_replaces_forbidden_characters():
    assert sanitize_filename('One Piece: Kapitel/1?') == "One Piece_ Kapitel_1_"


def test_count_available_chapter_images_requires_unique_pages(monkeypatch):
    monkeypatch.setattr(downloader, "_first_image_url", fake_first_image_url)
    driver = FakeDriver(
        {
            1: "https://example.test/page-1.jpg",
            2: "https://example.test/page-2.jpg",
            3: None,
        }
    )

    assert count_available_chapter_images(driver, chapter=1163, page_sleep=0, min_pages=3) == 2
    assert driver.visited_pages == [1, 2, 3]


def test_download_chapter_drops_incomplete_chapter(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(downloader, "_first_image_url", fake_first_image_url)
    monkeypatch.setattr(downloader, "save_image", fake_save_image)
    monkeypatch.setattr(downloader, "fetch_german_chapter_title", lambda _chapter: None)
    driver = FakeDriver(
        {
            1: "https://example.test/page-1.jpg",
            2: "https://example.test/page-2.jpg",
            3: None,
        }
    )
    logs: list[str] = []

    result = download_chapter(
        driver=driver,
        chapter=1163,
        save_root=tmp_path,
        page_sleep=0,
        log=logs.append,
        stop_flag=Event(),
    )

    assert result.pages == 2
    assert result.complete is False
    assert result.cbz_path == ""
    assert not list(tmp_path.glob("*.cbz"))
    assert not (tmp_path / "Kapitel_1163").exists()
    assert any("unvollstaendig" in line for line in logs)


def test_download_chapter_creates_cbz_for_complete_chapter(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(downloader, "_first_image_url", fake_first_image_url)
    monkeypatch.setattr(downloader, "save_image", fake_save_image)
    monkeypatch.setattr(downloader, "fetch_german_chapter_title", lambda _chapter: None)
    driver = FakeDriver(
        {
            1: "https://example.test/page-1.jpg",
            2: "https://example.test/page-2.jpg",
            3: "https://example.test/page-3.jpg",
            4: None,
        }
    )

    result = download_chapter(
        driver=driver,
        chapter=1163,
        save_root=tmp_path,
        page_sleep=0,
        log=lambda _message: None,
        stop_flag=Event(),
    )

    assert result.pages == 3
    assert result.complete is True
    assert Path(result.cbz_path).exists()
    with zipfile.ZipFile(result.cbz_path) as archive:
        assert len(archive.namelist()) == 3
