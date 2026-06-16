from __future__ import annotations

import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from threading import Event
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

import requests
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager

BASE_URL_TEMPLATE = os.getenv("BASE_URL_TEMPLATE", "https://onepiece.tube/manga/kapitel/{chapter}/{page}")
DEFAULT_SAVE_ROOT = os.getenv("DOWNLOAD_ROOT", "/downloads")
DEFAULT_HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"0", "false", "no"}
DEFAULT_PAGE_SLEEP = float(os.getenv("PAGE_SLEEP", "0.5"))
MAX_PAGES_GUESS = int(os.getenv("MAX_PAGES_GUESS", "999"))
MIN_PAGES_FOR_CBZ = int(os.getenv("MIN_PAGES_FOR_CBZ", "3"))
TIMEOUT_DOWNLOAD = int(os.getenv("TIMEOUT_DOWNLOAD", "30"))

OPWIKI_URLS = [
    "https://opwiki.org/index.php?title=Manga%3AKapitel_{ch}&useskin=wptouch",
    "https://opwiki.org/wiki/Manga:Kapitel_{ch}",
]

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class DownloadConfig:
    """Runtime settings for one web-triggered download job."""

    save_root: str = DEFAULT_SAVE_ROOT
    browser: str = "Auto"
    headless: bool = DEFAULT_HEADLESS
    page_sleep: float = DEFAULT_PAGE_SLEEP
    chapters_spec: str = ""
    delete_pages_after_cbz: bool = False


@dataclass
class ChapterResult:
    chapter: int
    pages: int
    cbz_path: str = ""
    complete: bool = False


@dataclass
class DownloadSummary:
    chapters: list[ChapterResult] = field(default_factory=list)
    stopped: bool = False

    @property
    def successful(self) -> int:
        return sum(1 for chapter in self.chapters if chapter.complete)


RANGE_RE = re.compile(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*$")
LIST_SPLIT_RE = re.compile(r"\s*,\s*")
SEITE_SUFFIX_RE = re.compile(r"\s*\(?Seite\s*\d+\)?\s*$", re.IGNORECASE)


def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    cleaned = unescape(title)
    cleaned = SEITE_SUFFIX_RE.sub("", cleaned)
    cleaned = cleaned.strip(" -–—:|")
    return cleaned or None


def ext_from_url(img_url: str) -> str:
    path = urlparse(img_url).path
    _, ext = os.path.splitext(path)
    return ext.split("?")[0] if ext else ".jpg"


def expand_chapter_spec(spec: str) -> list[int]:
    spec = spec.strip()
    if not spec:
        raise ValueError("Kapitelangabe ist leer.")
    if spec.isdigit():
        return [int(spec)]
    if "," in spec:
        chapters: list[int] = []
        for part in LIST_SPLIT_RE.split(spec):
            chapters.extend(_expand_single_part(part))
        return sorted(set(chapters))
    return _expand_single_part(spec)


def _expand_single_part(part: str) -> list[int]:
    part = part.strip()
    if part.isdigit():
        return [int(part)]
    match = RANGE_RE.match(part)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            start, end = end, start
        return list(range(start, end + 1))
    raise ValueError("Ungültiges Kapitel-Format. Beispiele: '30' | '30-60' | '30,33-35,40'.")


def _add_common_browser_options(options, headless: bool) -> None:
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1200,1000")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36")


def setup_driver(browser: str = "Auto", headless: bool = True) -> webdriver.Remote:
    normalized = browser.strip().lower()
    attempts: Iterable[str]
    if normalized in {"", "auto"}:
        attempts = ("chrome", "edge", "firefox")
    elif normalized in {"chrome", "chromium"}:
        attempts = ("chrome",)
    elif normalized == "edge":
        attempts = ("edge",)
    elif normalized == "firefox":
        attempts = ("firefox",)
    else:
        raise RuntimeError(f"Unbekannter Browser: {browser}")

    errors: list[str] = []
    for attempt in attempts:
        try:
            if attempt == "chrome":
                return _setup_chrome(headless)
            if attempt == "edge":
                return _setup_edge(headless)
            if attempt == "firefox":
                return _setup_firefox(headless)
        except Exception as exc:  # intentional: collect all browser fallback errors
            errors.append(f"{attempt}: {exc}")

    joined = " | ".join(errors) if errors else "keine Startversuche"
    raise RuntimeError(f"Konnte keinen Browser starten ({joined}).")


def _setup_chrome(headless: bool) -> webdriver.Remote:
    options = ChromeOptions()
    _add_common_browser_options(options, headless)
    chrome_bin = os.getenv("CHROME_BIN") or os.getenv("GOOGLE_CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    chromedriver = os.getenv("CHROMEDRIVER_PATH")
    service = ChromeService(chromedriver) if chromedriver else ChromeService(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def _setup_edge(headless: bool) -> webdriver.Remote:
    options = EdgeOptions()
    options.use_chromium = True
    _add_common_browser_options(options, headless)
    edgedriver = os.getenv("EDGEDRIVER_PATH")
    service = EdgeService(edgedriver) if edgedriver else EdgeService(EdgeChromiumDriverManager().install())
    return webdriver.Edge(service=service, options=options)


def _setup_firefox(headless: bool) -> webdriver.Remote:
    options = FirefoxOptions()
    if headless:
        options.add_argument("-headless")
    return webdriver.Firefox(service=FirefoxService(), options=options)


def save_image(session: requests.Session, img_url: str, out_path: str | Path) -> bool:
    try:
        response = session.get(img_url, timeout=TIMEOUT_DOWNLOAD)
        if response.status_code == 200 and response.content:
            Path(out_path).write_bytes(response.content)
            return True
    except requests.RequestException:
        return False
    return False


def pack_cbz_from_folder(folder: str | Path, cbz_path: str | Path) -> None:
    folder_path = Path(folder)
    files = sorted(path for path in folder_path.iterdir() if path.is_file())
    pack_cbz_from_files(files, cbz_path)


def pack_cbz_from_files(files: Iterable[str | Path], cbz_path: str | Path) -> None:
    sorted_files = sorted(Path(path) for path in files)
    with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted_files:
            archive.write(path, arcname=path.name)


def fetch_german_chapter_title(chapter: int, timeout: int = 15) -> Optional[str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    for url_template in OPWIKI_URLS:
        try:
            response = requests.get(url_template.format(ch=chapter), timeout=timeout, headers=headers)
        except requests.RequestException:
            continue
        if response.status_code != 200 or not response.text:
            continue
        html = response.text
        carlsen_match = re.search(r"Carlsen-Titel:\s*(?:</?[^>]+>)*([^<\n]+)", html, flags=re.IGNORECASE)
        if carlsen_match:
            return clean_title(carlsen_match.group(1))
        chapter_match = re.search(rf"Kapitel\s*{chapter}\s*:\s*(?:</?[^>]+>)*([^<\n]+)", html, flags=re.IGNORECASE)
        if chapter_match:
            return clean_title(chapter_match.group(1))
    return None


def download_chapter(
    driver: webdriver.Remote,
    chapter: int,
    save_root: str | Path,
    page_sleep: float,
    log: LogFn,
    stop_flag: Event,
    delete_pages_after_cbz: bool = False,
) -> ChapterResult:
    save_root_path = Path(save_root)
    chapter_folder = save_root_path / f"Kapitel_{chapter:04d}"
    ensure_dir(chapter_folder)

    session = requests.Session()
    seen_urls: set[str] = set()
    saved_paths: list[Path] = []
    page_count = 0

    for page in range(1, MAX_PAGES_GUESS + 1):
        if stop_flag.is_set():
            log(f"[!] Abbruch angefordert – Kapitel {chapter} wird beendet.")
            break

        url = BASE_URL_TEMPLATE.format(chapter=chapter, page=page)
        try:
            driver.get(url)
        except WebDriverException as exc:
            log(f"[!] WebDriver-Fehler bei {url}: {exc}")
            break

        time.sleep(page_sleep)
        img_url = _first_image_url(driver)
        if not img_url or img_url in seen_urls:
            if page == 1:
                log(f"[-] Kein Bild auf Seite 1 gefunden. Kapitel {chapter} existiert evtl. nicht.")
            else:
                log(f"[i] Kein weiteres Bild gefunden. Beende Kapitel {chapter} nach Seite {page - 1}.")
            break

        seen_urls.add(img_url)
        page_name = f"Kapitel {chapter} - Seite {page:03d}{ext_from_url(img_url)}"
        out_path = chapter_folder / page_name
        if save_image(session, img_url, out_path):
            page_count += 1
            saved_paths.append(out_path)
            log(f"[+] {chapter}: Seite {page:03d} gespeichert → {page_name}")
        else:
            log(f"[!] Download fehlgeschlagen: {img_url}")

    if page_count < MIN_PAGES_FOR_CBZ:
        _delete_saved_pages(saved_paths)
        _remove_empty_folder(chapter_folder)
        if page_count <= 0:
            log(f"[-] Kapitel {chapter} wurde nicht heruntergeladen: keine Seiten gefunden.")
        else:
            log(
                f"[-] Kapitel {chapter} unvollstaendig: nur {page_count}/{MIN_PAGES_FOR_CBZ} "
                "benoetigte Seiten gefunden. Keine CBZ erstellt."
            )
        return ChapterResult(chapter=chapter, pages=page_count)

    de_title = fetch_german_chapter_title(chapter)
    title_clean = clean_title(de_title) if de_title else None
    base_name = f"One Piece - Kapitel {chapter} - {title_clean}" if title_clean else f"One Piece - Kapitel {chapter}"
    cbz_path = save_root_path / f"{sanitize_filename(base_name)}.cbz"
    pack_cbz_from_files(saved_paths, cbz_path)
    log(f"[✔] CBZ erstellt: {cbz_path}")

    if delete_pages_after_cbz:
        _delete_saved_pages(saved_paths)
        try:
            chapter_folder.rmdir()
            log(f"[i] Temporäre Seitendateien gelöscht: {chapter_folder}")
        except OSError:
            pass

    return ChapterResult(chapter=chapter, pages=page_count, cbz_path=str(cbz_path), complete=True)


def _delete_saved_pages(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _remove_empty_folder(folder: Path) -> bool:
    try:
        folder.rmdir()
        return True
    except OSError:
        return False


def _first_image_url(driver: webdriver.Remote) -> Optional[str]:
    try:
        image = driver.find_element("tag name", "img")
        src = image.get_attribute("src")
    except NoSuchElementException:
        return None
    return src if src and src.startswith("http") else None


def count_available_chapter_images(
    driver: webdriver.Remote,
    chapter: int,
    page_sleep: float,
    min_pages: int = MIN_PAGES_FOR_CBZ,
) -> int:
    seen_urls: set[str] = set()
    for page in range(1, MAX_PAGES_GUESS + 1):
        url = BASE_URL_TEMPLATE.format(chapter=chapter, page=page)
        driver.get(url)
        time.sleep(page_sleep)
        img_url = _first_image_url(driver)
        if not img_url or img_url in seen_urls:
            break
        seen_urls.add(img_url)
        if len(seen_urls) >= min_pages:
            break
    return len(seen_urls)


def run_download(config: DownloadConfig, log: LogFn, stop_flag: Event) -> DownloadSummary:
    ensure_dir(config.save_root)
    chapters = expand_chapter_spec(config.chapters_spec)
    log(f"[i] Kapitel: {', '.join(str(chapter) for chapter in chapters)}")
    log(f"[i] Download-Ordner: {Path(config.save_root).resolve()}")
    log(f"[i] Browser: {config.browser}, Headless: {config.headless}")

    summary = DownloadSummary()
    driver: Optional[webdriver.Remote] = None
    try:
        driver = setup_driver(config.browser, config.headless)
        for chapter in chapters:
            if stop_flag.is_set():
                summary.stopped = True
                break
            log(f"\n=== Kapitel {chapter} ===")
            result = download_chapter(
                driver=driver,
                chapter=chapter,
                save_root=config.save_root,
                page_sleep=config.page_sleep,
                log=log,
                stop_flag=stop_flag,
                delete_pages_after_cbz=config.delete_pages_after_cbz,
            )
            summary.chapters.append(result)
        if stop_flag.is_set():
            summary.stopped = True
            log("[!] Download wurde abgebrochen.")
        log(f"[✔] Fertig. Erfolgreiche Kapitel: {summary.successful}/{len(chapters)}")
    finally:
        if driver is not None:
            driver.quit()
    return summary
