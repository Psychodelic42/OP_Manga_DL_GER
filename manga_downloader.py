import os
import re
import time
import zipfile
import requests
from urllib.parse import urlparse
from typing import Optional, List, Tuple
from html import unescape  # NEU: für saubere Titel

# Selenium / Chrome / Edge / FF
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, NoSuchElementException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
# Optional: für Firefox
# import geckodriver_autoinstaller

# =========================================
# Konfiguration
# =========================================
BASE_URL_TEMPLATE = "https://onepiece.tube/manga/kapitel/{chapter}/{page}"
SAVE_ROOT = os.path.abspath("downloads")     # Alle Kapitel landen hier drin
HEADLESS = True
PAGE_SLEEP = 0.1                             # Sekunden zwischen Seitenaufrufen
MAX_PAGES_GUESS = 999                        # Sicherheitsgrenze, wir brechen früher ab
TIMEOUT_DOWNLOAD = 30

# =========================================
# Hilfsfunktionen
# =========================================

def _add_common_opts(opts, headless: bool):
    # Headless-Flag kompatibel halten
    if headless:
        try:
            opts.add_argument("--headless=new")   # neuere Browser
        except Exception:
            opts.add_argument("--headless")       # Fallback
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1200,1000")

def setup_driver(headless: bool = True) -> webdriver.Remote:
    # 1) Versuche CHROME
    try:
        chrome_opts = ChromeOptions()
        _add_common_opts(chrome_opts, headless)
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),
                                  options=chrome_opts)
        return driver
    except WebDriverException:
        pass
    except Exception:
        pass

    # 2) Fallback EDGE (auf Windows fast immer vorhanden)
    try:
        edge_opts = EdgeOptions()
        edge_opts.use_chromium = True
        _add_common_opts(edge_opts, headless)
        driver = webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()),
                                options=edge_opts)
        return driver
    except WebDriverException:
        pass
    except Exception:
        pass

    # 3) Fallback FIREFOX (wenn installiert)
    try:
        # geckodriver_autoinstaller.install()  # optional, wenn kein geckodriver im PATH
        ff_opts = FirefoxOptions()
        if headless:
            ff_opts.add_argument("-headless")
        driver = webdriver.Firefox(service=FirefoxService(), options=ff_opts)
        return driver
    except Exception as e:
        raise RuntimeError(
            "Konnte keinen Browser starten. Installiere Chrome/Edge/Firefox oder setze den Chrome-Pfad."
        ) from e

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def ext_from_url(img_url: str) -> str:
    path = urlparse(img_url).path
    _, ext = os.path.splitext(path)
    if not ext:
        # Fallback
        return ".jpg"
    return ext.split("?")[0]

def sanitize_filename(name: str) -> str:
    # Entferne verbotene Zeichen für Dateinamen
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

# --- Titel-Cleanup: entfernt "(Seite X)" und überflüssige Trennzeichen ---
SEITE_SUFFIX_RE = re.compile(r"\s*\(?Seite\s*\d+\)?\s*$", re.IGNORECASE)
def clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    t = unescape(title)
    t = SEITE_SUFFIX_RE.sub("", t)          # "(Seite 1)" etc. weg
    t = t.strip(" -–—:|")                  # Ränder säubern
    return t or None

def get_chapter_title_guess(page_title: str) -> Optional[str]:
    """
    Optional: heuristische Titelrater aus <title>.
    Wird NICHT mehr zum Benennen verwendet, nur für Logs falls gewünscht.
    """
    if not page_title:
        return None
    m = re.search(r"Kapitel\s+\d+\s*[–-]\s*(.+?)\s*(\||$)", page_title, flags=re.IGNORECASE)
    if m:
        return clean_title(m.group(1))
    m2 = re.search(r"Kapitel\s+\d+\s+(.+?)\s*(\||$)", page_title, flags=re.IGNORECASE)
    if m2:
        return clean_title(m2.group(1))
    return None

def save_image(img_url: str, out_path: str) -> bool:
    try:
        resp = requests.get(img_url, timeout=TIMEOUT_DOWNLOAD)
        if resp.status_code == 200 and resp.content:
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return True
        return False
    except requests.RequestException:
        return False

def pack_cbz_from_folder(folder: str, cbz_path: str):
    files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    # Sortiert (Seite 001, 002, …)
    files.sort()
    with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(os.path.join(folder, f), arcname=f)

def iter_chapters_from_input(choice: str, value: str) -> List[int]:
    if choice.lower().startswith("e"):  # Einzel
        return [int(value)]
    # Bereich z.B. "30-60"
    m = re.match(r"\s*(\d+)\s*[-–]\s*(\d+)\s*$", value)
    if not m:
        raise ValueError("Ungültiges Bereichsformat. Beispiel: 30-60")
    start, end = int(m.group(1)), int(m.group(2))
    if start > end:
        start, end = end, start
    return list(range(start, end + 1))

# =========================================
# Deutscher Kapiteltitel (OPwiki)
# =========================================

# Zwei Varianten/Skins probieren – manche Infos stehen nur in einem Layout
OPWIKI_URLS = [
    "https://opwiki.org/index.php?title=Manga%3AKapitel_{ch}&useskin=wptouch",
    "https://opwiki.org/wiki/Manga:Kapitel_{ch}",
]

def fetch_german_chapter_title(chapter: int, timeout: int = 15) -> Optional[str]:
    """
    Holt den deutschen Kapiteltitel von OPwiki.
    Primär: 'Carlsen-Titel: ...'
    Fallback: 'Kapitel {n}: <Titel>'
    Gibt None zurück, wenn nichts gefunden wird.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    for url_tmpl in OPWIKI_URLS:
        url = url_tmpl.format(ch=chapter)
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            if r.status_code != 200 or not r.text:
                continue
            html = r.text

            # 1) "Carlsen-Titel: XYZ"
            m = re.search(r"Carlsen-Titel:\s*(?:</?[^>]+>)*([^<\n]+)", html, flags=re.IGNORECASE)
            if m:
                return clean_title(m.group(1))

            # 2) "Kapitel 1162: GVBR – God Valley Battle Royale"
            pat = rf"Kapitel\s*{chapter}\s*:\s*(?:</?[^>]+>)*([^<\n]+)"
            m2 = re.search(pat, html, flags=re.IGNORECASE)
            if m2:
                return clean_title(m2.group(1))

        except requests.RequestException:
            continue
    return None

# =========================================
# Kernlogik pro Kapitel
# =========================================
def download_chapter(driver: webdriver.Chrome, chapter: int) -> Tuple[int, Optional[str], str]:
    """
    Lädt alle Seiten eines Kapitels herunter.
    Gibt zurück: (anzahl_bilder, kapiteltitel_od_none, kapitel_ordner)
    """
    chapter_folder = os.path.join(SAVE_ROOT, f"Kapitel_{chapter:04d}")
    ensure_dir(chapter_folder)

    seen_urls = set()
    page_count = 0
    chapter_title: Optional[str] = None

    for page in range(1, MAX_PAGES_GUESS + 1):
        url = BASE_URL_TEMPLATE.format(chapter=chapter, page=page)
        try:
            driver.get(url)
        except WebDriverException as e:
            print(f"[!] WebDriver-Fehler bei {url}: {e}")
            break

        # kurze Wartezeit damit Bilder/JS laden
        time.sleep(PAGE_SLEEP)

        if chapter_title is None:
            # Nur noch für Logs interessant; nicht für Dateinamen
            chapter_title = get_chapter_title_guess(driver.title)

        # Versuche Bild zu greifen (erstes IMG im sichtbaren Bereich)
        img_url = None
        try:
            img_el = driver.find_element("tag name", "img")
            src = img_el.get_attribute("src")
            if src and src.startswith("http"):
                img_url = src
        except NoSuchElementException:
            img_url = None

        # Abbruchkriterien: kein Bild oder doppelt
        if not img_url or img_url in seen_urls:
            if page == 1:
                print(f"[-] Kein Bild auf Seite 1 gefunden. Kapitel {chapter} existiert evtl. nicht.")
            else:
                print(f"[i] Kein weiteres Bild gefunden. Beende Kapitel {chapter} nach Seite {page-1}.")
            break

        seen_urls.add(img_url)

        # Dateiname: "Kapitel 1148 - Seite 001.jpg"
        ext = ext_from_url(img_url)
        page_name = f"Kapitel {chapter} - Seite {page:03d}{ext}"
        out_path = os.path.join(chapter_folder, page_name)

        ok = save_image(img_url, out_path)
        if ok:
            page_count += 1
            print(f"[+] {chapter}: Seite {page:03d} gespeichert → {page_name}")
        else:
            print(f"[!] Download fehlgeschlagen: {img_url}")

    return page_count, chapter_title, chapter_folder

# =========================================
# CBZ-Namensschema (bereinigt)
# =========================================
def build_cbz_name(chapter: int, german_title: Optional[str]) -> str:
    """
    "One Piece - Kapitel {nr} - {Titel}.cbz"
    Fällt bei fehlendem Titel auf "One Piece - Kapitel {nr}.cbz" zurück.
    """
    clean = clean_title(german_title) if german_title else None
    if clean:
        base = f"One Piece - Kapitel {chapter} - {clean}"
    else:
        base = f"One Piece - Kapitel {chapter}"
    return sanitize_filename(base) + ".cbz"

# =========================================
# Main
# =========================================
def main():
    print("=== Manga Downloader → CBZ (onepiece.tube) ===")
    print("Modus: (E) Ein Kapitel  |  (B) Bereich von-bis")
    choice = input("Bitte E oder B eingeben: ").strip()

    if choice.lower().startswith("e"):
        value = input("Kapitelnummer (z.B. 30): ").strip()
    else:
        value = input("Kapitelbereich (z.B. 30-60): ").strip()

    chapters = iter_chapters_from_input(choice, value)

    ensure_dir(SAVE_ROOT)
    driver = setup_driver(headless=HEADLESS)

    try:
        for ch in chapters:
            print(f"\n=== Kapitel {ch} ===")
            count, chap_title_guess, chap_folder = download_chapter(driver, ch)
            if count == 0:
                print(f"[!] Kapitel {ch}: Keine Seiten geladen – übersprungen.")
                continue

            # DE-Titel NUR von OPwiki holen (kein Fallback mehr auf page-title-guess!)
            de_title = fetch_german_chapter_title(ch)

            cbz_name = build_cbz_name(ch, de_title)
            cbz_path = os.path.join(SAVE_ROOT, cbz_name)
            pack_cbz_from_folder(chap_folder, cbz_path)
            print(f"[✔] CBZ erstellt: {cbz_path}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print("\nFertig.")

if __name__ == "__main__":
    main()
