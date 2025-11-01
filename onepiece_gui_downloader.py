import os
import re
import time
import zipfile
import requests
import threading
import queue
import webbrowser
import subprocess
from urllib.parse import urlparse
from typing import Optional, List, Tuple
from html import unescape
from dataclasses import dataclass
import io
import sys

# -------------------------------
# Pillow für Logo/Icons
# -------------------------------
from PIL import Image, ImageDraw, ImageFont, ImageTk

# -------------------------------
# Selenium / Browser Driver
# -------------------------------
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
# Optional: geckodriver_autoinstaller

# -------------------------------
# Tkinter GUI
# -------------------------------
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# =========================================
# Konfiguration (Default-Werte)
# =========================================
BASE_URL_TEMPLATE = "https://onepiece.tube/manga/kapitel/{chapter}/{page}"
DEFAULT_SAVE_ROOT = os.path.abspath("downloads")
DEFAULT_HEADLESS = True
DEFAULT_PAGE_SLEEP = 0.5     # Sekunden zwischen Seitenaufrufen
MAX_PAGES_GUESS = 999
TIMEOUT_DOWNLOAD = 30

APP_NAME   = "One Piece – Manga Downloader"
ASSET_DIR  = os.path.abspath("./assets_opdl")  # hierhin werden Logo/Icons geschrieben
LOGO_PNG   = os.path.join(ASSET_DIR, "opdl_logo_512.png")
ICON_ICO   = os.path.join(ASSET_DIR, "opdl_icon.ico")
LOGO_SIZE  = 160  # << kompakte Logo-Kantenlänge in px

# =========================================
# Nützliche Datentypen
# =========================================
@dataclass
class DownloadConfig:
    save_root: str
    browser: str           # "Auto", "Chrome", "Edge", "Firefox"
    headless: bool
    page_sleep: float
    chapters_spec: str     # "30" | "30-60" | "30,33-35,40"

# =========================================
# Hilfsfunktionen (Downloader Kern)
# =========================================
def _add_common_opts(opts, headless: bool):
    if headless:
        try:
            opts.add_argument("--headless=new")
        except Exception:
            opts.add_argument("--headless")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1200,1000")

def setup_driver_chrome(headless: bool) -> webdriver.Remote:
    chrome_opts = ChromeOptions()
    _add_common_opts(chrome_opts, headless)
    return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),
                            options=chrome_opts)

def setup_driver_edge(headless: bool) -> webdriver.Remote:
    edge_opts = EdgeOptions()
    edge_opts.use_chromium = True
    _add_common_opts(edge_opts, headless)
    return webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()),
                          options=edge_opts)

def setup_driver_firefox(headless: bool) -> webdriver.Remote:
    ff_opts = FirefoxOptions()
    if headless:
        ff_opts.add_argument("-headless")
    return webdriver.Firefox(service=FirefoxService(), options=ff_opts)

def setup_driver(browser: str, headless: bool) -> webdriver.Remote:
    if browser == "Chrome":
        return setup_driver_chrome(headless)
    if browser == "Edge":
        return setup_driver_edge(headless)
    if browser == "Firefox":
        return setup_driver_firefox(headless)
    for fn in (setup_driver_chrome, setup_driver_edge, setup_driver_firefox):
        try:
            return fn(headless)
        except Exception:
            continue
    raise RuntimeError("Konnte keinen Browser starten. Installiere Chrome/Edge/Firefox oder setze den Chrome-Pfad.")

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def ext_from_url(img_url: str) -> str:
    path = urlparse(img_url).path
    _, ext = os.path.splitext(path)
    if not ext:
        return ".jpg"
    return ext.split("?")[0]

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

SEITE_SUFFIX_RE = re.compile(r"\s*\(?Seite\s*\d+\)?\s*$", re.IGNORECASE)
def clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    t = unescape(title)
    t = SEITE_SUFFIX_RE.sub("", t)
    t = t.strip(" -–—:|")
    return t or None

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
    files.sort()
    with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(os.path.join(folder, f), arcname=f)

# ---------- OPwiki (DE-Titel) ----------
OPWIKI_URLS = [
    "https://opwiki.org/index.php?title=Manga%3AKapitel_{ch}&useskin=wptouch",
    "https://opwiki.org/wiki/Manga:Kapitel_{ch}",
]

def fetch_german_chapter_title(chapter: int, timeout: int = 15) -> Optional[str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    for url_tmpl in OPWIKI_URLS:
        url = url_tmpl.format(ch=chapter)
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            if r.status_code != 200 or not r.text:
                continue
            html = r.text
            m = re.search(r"Carlsen-Titel:\s*(?:</?[^>]+>)*([^<\n]+)", html, flags=re.IGNORECASE)
            if m:
                return clean_title(m.group(1))
            pat = rf"Kapitel\s*{chapter}\s*:\s*(?:</?[^>]+>)*([^<\n]+)"
            m2 = re.search(pat, html, flags=re.IGNORECASE)
            if m2:
                return clean_title(m2.group(1))
        except requests.RequestException:
            continue
    return None

# ---------- Kapitel-Auswahl-Parsing ----------
RANGE_RE = re.compile(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*$")
LIST_SPLIT_RE = re.compile(r"\s*,\s*")

def expand_chapter_spec(spec: str) -> List[int]:
    spec = spec.strip()
    if not spec:
        raise ValueError("Kapitelangabe ist leer.")
    if spec.isdigit():
        return [int(spec)]
    if "," in spec:
        nums: List[int] = []
        parts = LIST_SPLIT_RE.split(spec)
        for p in parts:
            p = p.strip()
            if p.isdigit():
                nums.append(int(p))
                continue
            m = RANGE_RE.match(p)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a > b:
                    a, b = b, a
                nums.extend(range(a, b+1))
                continue
            raise ValueError(f"Ungültiger Eintrag in Liste: '{p}'")
        return sorted(set(nums))
    m = RANGE_RE.match(spec)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        return list(range(a, b+1))
    raise ValueError("Ungültiges Kapitel-Format. Beispiele: '30' | '30-60' | '30,33-35,40'.")

# ---------- Kapitel-Download ----------
def download_chapter(driver: webdriver.Remote, chapter: int, save_root: str, page_sleep: float,
                     log: callable, stop_flag: threading.Event) -> Tuple[int, str]:
    chapter_folder = os.path.join(save_root, f"Kapitel_{chapter:04d}")
    ensure_dir(chapter_folder)

    seen_urls = set()
    page_count = 0

    for page in range(1, MAX_PAGES_GUESS + 1):
        if stop_flag.is_set():
            log(f"[!] Abbruch angefordert – Kapitel {chapter} wird beendet.")
            break

        url = BASE_URL_TEMPLATE.format(chapter=chapter, page=page)
        try:
            driver.get(url)
        except WebDriverException as e:
            log(f"[!] WebDriver-Fehler bei {url}: {e}")
            break

        time.sleep(page_sleep)

        img_url = None
        try:
            img_el = driver.find_element("tag name", "img")
            src = img_el.get_attribute("src")
            if src and src.startswith("http"):
                img_url = src
        except NoSuchElementException:
            img_url = None

        if not img_url or img_url in seen_urls:
            if page == 1:
                log(f"[-] Kein Bild auf Seite 1 gefunden. Kapitel {chapter} existiert evtl. nicht.")
            else:
                log(f"[i] Kein weiteres Bild gefunden. Beende Kapitel {chapter} nach Seite {page-1}.")
            break

        seen_urls.add(img_url)

        ext = ext_from_url(img_url)
        page_name = f"Kapitel {chapter} - Seite {page:03d}{ext}"
        out_path = os.path.join(chapter_folder, page_name)

        if save_image(img_url, out_path):
            page_count += 1
            log(f"[+] {chapter}: Seite {page:03d} gespeichert → {page_name}")
        else:
            log(f"[!] Download fehlgeschlagen: {img_url}")

    if page_count > 0:
        de_title = fetch_german_chapter_title(chapter)
        title_clean = clean_title(de_title) if de_title else None
        if title_clean:
            base = f"One Piece - Kapitel {chapter} - {title_clean}"
        else:
            base = f"One Piece - Kapitel {chapter}"
        cbz_name = sanitize_filename(base) + ".cbz"
        cbz_path = os.path.join(save_root, cbz_name)
        pack_cbz_from_folder(chapter_folder, cbz_path)
        log(f"[✔] CBZ erstellt: {cbz_path}")
        return page_count, cbz_path

    return 0, ""

# =========================================
# Logo/Icons erzeugen
# =========================================
def generate_logo_assets():
    ensure_dir(ASSET_DIR)
    size = 512
    img = Image.new("RGBA", (size, size), (16, 24, 32, 0))
    d = ImageDraw.Draw(img)

    # Hintergrund-Kringel
    d.ellipse((32, 32, size-32, size-32), fill=(8, 100, 180, 255))
    d.ellipse((48, 48, size-48, size-48), fill=(12, 70, 130, 255))

    # „Strohhut“ (Icon)
    cx, cy = size//2, size//2 - 20
    d.ellipse((cx-160, cy-10, cx+160, cy+30), fill=(220, 170, 60, 255))     # Krempe
    d.ellipse((cx-90,  cy-110, cx+90,  cy+30),  fill=(235, 190, 70, 255))   # Krone
    d.rectangle((cx-90, cy-25,  cx+90,  cy-5),  fill=(210, 40, 40, 255))    # Band

    # Text „OPDL“
    try:
        font = ImageFont.truetype("arial.ttf", 96)
    except Exception:
        font = ImageFont.load_default()
    text = "OPDL"
    tw = d.textlength(text, font=font)
    d.text((cx - tw/2, cy + 70), text, fill=(255, 255, 255, 240), font=font)

    img.save(LOGO_PNG, "PNG")
    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(ICON_ICO, sizes=ico_sizes)

# =========================================
# GUI-Logik
# =========================================
class DownloaderThread(threading.Thread):
    def __init__(self, cfg: DownloadConfig, log_q: queue.Queue, done_cb, stop_flag: threading.Event):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.log_q = log_q
        self.done_cb = done_cb
        self.stop_flag = stop_flag

    def _log(self, msg: str):
        self.log_q.put(msg)

    def run(self):
        try:
            ensure_dir(self.cfg.save_root)
            self._log("[…] Starte Browser…")
            driver = setup_driver(self.cfg.browser, self.cfg.headless)
            try:
                chapters = expand_chapter_spec(self.cfg.chapters_spec)
                self._log(f"[i] Kapitel: {chapters}")
                created = []
                for ch in chapters:
                    if self.stop_flag.is_set():
                        self._log("[!] Abbruch angefordert – stoppe.")
                        break
                    self._log(f"\n=== Kapitel {ch} ===")
                    count, cbz_path = download_chapter(
                        driver, ch, self.cfg.save_root, self.cfg.page_sleep, self._log, self.stop_flag
                    )
                    if count > 0 and cbz_path:
                        created.append(cbz_path)
                if created:
                    self._log("\n[✓] Fertig. Erzeugte Dateien:")
                    for p in created:
                        self._log(f"  • {p}")
                else:
                    self._log("\n[!] Keine CBZ erzeugt.")
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass
        except Exception as e:
            self._log(f"[X] Fehler: {e}")
        finally:
            self.done_cb()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        # --- Logo/Icons erzeugen + laden ---
        try:
            generate_logo_assets()
        except Exception as e:
            print(f"Logo-Generierung fehlgeschlagen: {e}")

        self.title(APP_NAME)
        self.geometry("980x680")   # << größere Standardgröße
        self.minsize(880, 600)     # << sinnvolle Mindestgröße

        # Fenster-Icon setzen
        try:
            self.iconphoto(True, tk.PhotoImage(file=LOGO_PNG))
        except Exception:
            try:
                if os.name == "nt":
                    self.iconbitmap(ICON_ICO)
            except Exception:
                pass

        self.stop_flag = threading.Event()
        self.log_q = queue.Queue()
        self.worker: Optional[DownloaderThread] = None

        # --- Layout-Master ---
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # ===== Header mit kleinem Logo + Titel =====
        header = ttk.Frame(frm)
        header.grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 8))
        try:
            pil_logo = Image.open(LOGO_PNG).resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(pil_logo)
            ttk.Label(header, image=self._logo_img).grid(row=0, column=0, sticky="w")
        except Exception:
            ttk.Label(header, text="OPDL", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=APP_NAME, font=("Segoe UI", 16, "bold")).grid(row=0, column=1, sticky="w", padx=(12, 0))
        header.columnconfigure(1, weight=1)

        # ===== Eingaben =====
        row = 1
        ttk.Label(frm, text="Download-Ordner:").grid(row=row, column=0, sticky="w", pady=(0, 4))
        self.var_folder = tk.StringVar(value=DEFAULT_SAVE_ROOT)
        ttk.Entry(frm, textvariable=self.var_folder, width=70).grid(row=row, column=1, sticky="we", padx=6, pady=(0, 4))
        ttk.Button(frm, text="Wählen…", command=self.choose_folder).grid(row=row, column=2, sticky="we", pady=(0, 4))
        row += 1

        ttk.Label(frm, text="Kapitel (Einzel/Bereich/Liste):").grid(row=row, column=0, sticky="w", pady=2)
        self.var_spec = tk.StringVar(value="1150-1164")
        ttk.Entry(frm, textvariable=self.var_spec).grid(row=row, column=1, sticky="we", padx=6, columnspan=2, pady=2)
        row += 1

        ttk.Label(frm, text="Browser:").grid(row=row, column=0, sticky="w", pady=2)
        self.var_browser = tk.StringVar(value="Auto")
        ttk.Combobox(frm, textvariable=self.var_browser,
                     values=["Auto", "Chrome", "Edge", "Firefox"], state="readonly", width=12)\
            .grid(row=row, column=1, sticky="w", padx=(6,0), pady=2)
        self.var_headless = tk.BooleanVar(value=DEFAULT_HEADLESS)
        ttk.Checkbutton(frm, text="Headless (ohne Fenster)", variable=self.var_headless)\
            .grid(row=row, column=2, sticky="w", pady=2)
        row += 1

        ttk.Label(frm, text="Pause je Seite (Sek.):").grid(row=row, column=0, sticky="w", pady=2)
        self.var_sleep = tk.StringVar(value=str(DEFAULT_PAGE_SLEEP))
        ttk.Entry(frm, textvariable=self.var_sleep, width=10).grid(row=row, column=1, sticky="w", padx=6, pady=2)
        row += 1

        # ===== Buttons =====
        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="we", pady=(8, 4))
        self.btn_start = ttk.Button(btn_frame, text="Start", command=self.start_download)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btn_frame, text="Abbrechen", command=self.stop_download, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.btn_open = ttk.Button(btn_frame, text="Ordner öffnen", command=self.open_folder)
        self.btn_open.pack(side="left", padx=6)
        row += 1

        # ===== Protokoll (mit Scrollbar, füllt den Rest) =====
        ttk.Label(frm, text="Protokoll:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1

        log_frame = ttk.Frame(frm)
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew")
        self.txt = tk.Text(log_frame, height=14, wrap="word")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=scroll.set)
        self.txt.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        # ===== Grid-Gewichte: mittlere Spalte dehnt, Logbereich wächst =====
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(row, weight=1)  # die Logframe-Zeile wächst

        # Polling für Log
        self.after(100, self._poll_log)

    # ---------------- GUI-Handler ----------------
    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.var_folder.get() or os.getcwd())
        if path:
            self.var_folder.set(path)

    def start_download(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Läuft bereits", "Ein Download läuft bereits.")
            return
        try:
            save_root = self.var_folder.get().strip()
            if not save_root:
                raise ValueError("Bitte einen Download-Ordner wählen.")
            page_sleep = float(self.var_sleep.get().strip())
            spec = self.var_spec.get().strip()
            browser = self.var_browser.get()
            headless = bool(self.var_headless.get())

            cfg = DownloadConfig(
                save_root=save_root,
                browser=browser,
                headless=headless,
                page_sleep=page_sleep,
                chapters_spec=spec
            )
        except Exception as e:
            messagebox.showerror("Fehler", f"Eingabe prüfen: {e}")
            return

        # UI sperren
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.log_q.queue.clear()
        self.txt.delete("1.0", "end")
        self.stop_flag.clear()

        def on_done():
            self.after(0, lambda: (self.btn_start.config(state="normal"),
                                   self.btn_stop.config(state="disabled")))

        self.worker = DownloaderThread(cfg, self.log_q, on_done, self.stop_flag)
        self.worker.start()

    def stop_download(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            self.log_q.put("[!] Abbruch wird ausgeführt…")

    def open_folder(self):
        folder = self.var_folder.get().strip()
        if not os.path.isdir(folder):
            messagebox.showerror("Fehler", "Ordner existiert nicht.")
            return
        if os.name == "nt":
            os.startfile(folder)  # type: ignore
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt.insert("end", msg + "\n")
                self.txt.see("end")
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

if __name__ == "__main__":
    App().mainloop()
