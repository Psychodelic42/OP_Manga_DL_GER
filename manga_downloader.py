from __future__ import annotations

import threading

from app.downloader import DEFAULT_HEADLESS, DEFAULT_PAGE_SLEEP, DEFAULT_SAVE_ROOT, DownloadConfig, run_download


def main() -> None:
    print("One Piece Manga Downloader (CLI-Kompatibilitätsmodus)")
    chapters_spec = input("Kapitel (z. B. 1162, 1150-1164, 1150,1152-1154): ").strip()
    save_root = input(f"Download-Ordner [{DEFAULT_SAVE_ROOT}]: ").strip() or DEFAULT_SAVE_ROOT
    browser = input("Browser [Auto]: ").strip() or "Auto"
    headless_input = input(f"Headless? [{'j' if DEFAULT_HEADLESS else 'n'}] (j/n): ").strip().lower()
    if headless_input:
        headless = headless_input.startswith("j") or headless_input.startswith("y")
    else:
        headless = DEFAULT_HEADLESS
    page_sleep_input = input(f"Pause je Seite in Sekunden [{DEFAULT_PAGE_SLEEP}]: ").strip()
    page_sleep = float(page_sleep_input) if page_sleep_input else DEFAULT_PAGE_SLEEP

    config = DownloadConfig(
        save_root=save_root,
        browser=browser,
        headless=headless,
        page_sleep=page_sleep,
        chapters_spec=chapters_spec,
    )
    run_download(config, print, threading.Event())


if __name__ == "__main__":
    main()
