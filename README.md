# One Piece – Manga Downloader

Ein Tool, um **One Piece**-Manga-Kapitel von `onepiece.tube` automatisiert herunterzuladen, die Seiten fortlaufend zu benennen und die Kapitel als **CBZ** zu verpacken.
Es gibt eine **GUI-App (Tkinter)** sowie eine **CLI-Variante**. Die **deutschen Kapiteltitel** werden live von **OPwiki** geladen.

> ⚠️ **Hinweis/Disclaimer**
> Dieses Projekt ist nur für **privaten Gebrauch** gedacht. Beachte die **Nutzungsbedingungen** der Zielseiten und das **Urheberrecht** in deinem Land. Nutzung auf eigene Verantwortung.

---

## Inhalt

```
.
├─ onepiece_gui_downloader.py   # Empfohlene GUI-Anwendung (Logo & Icon werden zur Laufzeit erzeugt)
├─ manga_downloader.py          # Optionale CLI-Version
├─ assets_opdl/                 # Wird automatisch erzeugt (Logo/Icons)
├─ downloads/                   # Standard-Zielordner für Kapitel (wird angelegt)
└─ README.md
```

---

## Features

* 🔽 Download **Einzelkapitel**, **Bereich** (`1150-1164`) oder **Listen** (`1150,1152-1155,1160`)
* 🖼️ Automatisches Finden & Speichern aller Seiten mit Namen wie `Kapitel 1162 - Seite 001.jpg`
* 🗂️ Automatisches **CBZ pro Kapitel**
* 🏷️ **Deutsche Titel** via **OPwiki** (Carlsen-Titel/Fallback). Kein „(Seite 1)“ im Dateinamen
* 🖥️ **GUI**: Ordnerauswahl, Browserwahl (Auto/Chrome/Edge/Firefox), Headless-Modus, Pause je Seite, Live-Log + Scrollbar, Abbrechen, Ordner öffnen
* 🧰 **CLI** für Puristen

---

## Voraussetzungen

* **Python 3.10+** (getestet mit 3.10–3.12)
* Windows, macOS oder Linux
* Mindestens ein installierter Browser: **Chrome**, **Edge** oder **Firefox**

### Installation

```bash
git clone <REPO-URL>
cd <REPO-VERZEICHNIS>
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Nutzung (GUI)

```bash
python onepiece_gui_downloader.py
```

1. **Download-Ordner** wählen (Standard: `./downloads`)
2. **Kapitel** angeben:

   * einzelnes Kapitel: `1162`
   * Bereich: `1150-1164`
   * Liste/Kombination: `1150,1152-1155,1160`
3. **Browser** (Auto/Chrome/Edge/Firefox) & **Headless** wählen
4. Optional **Pause je Seite** (Standard `0.5 s`) anpassen
5. **Start** – Fortschritt im **Protokoll** (mit Scrollbar)

Ausgabe-Dateien:

```
One Piece - Kapitel 1162 - <DE-Kapitelname>.cbz
# Falls kein Titel gefunden:
One Piece - Kapitel 1162.cbz
```

---

## Nutzung (CLI)

```bash
python manga_downloader.py
```

* **E**: Ein Kapitel → z. B. `1162`
* **B**: Bereich → z. B. `1150-1164`

Die Seiten werden geladen, benannt und zu **CBZ** gepackt.

---

## Funktionsweise (Kurz)

* **Titelquelle**: OPwiki (2 URL/Skins). Reihenfolge:

  1. „**Carlsen-Titel:** …“
  2. `Kapitel {nr}: <Titel>`
* **Browsersteuerung**: `selenium` + `webdriver-manager` (lädt beim ersten Start automatisch den passenden Treiber).
* **Logo/Icons**: werden bei Programmstart per **Pillow** generiert (`assets_opdl/`).

### Wichtige Parameter

* `BASE_URL_TEMPLATE` – Quelle der Seitenbilder (aktuell `https://onepiece.tube/manga/kapitel/{chapter}/{page}`)
* `DEFAULT_PAGE_SLEEP` – Pause je Seite (Sekunden)
* `MAX_PAGES_GUESS` – Sicherheitsobergrenze für Seiten
* `TIMEOUT_DOWNLOAD` – Timeout für Bild-Downloads

---

## Als EXE bauen (Windows)

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name "OnePiece-Downloader" onepiece_gui_downloader.py
```

* Output: `dist/OnePiece-Downloader.exe`
* Für Debug-Ausgabe `--noconsole` weglassen.
* Beim ersten Start kann das Laden des WebDrivers etwas dauern (Internet erforderlich).

---

## Troubleshooting

**`cannot find Chrome binary`**

* Chrome ist nicht installiert oder nicht im Standardpfad.
  Lösungen:
* In der GUI **Browser = Edge/Firefox** wählen
* Oder in `setup_driver_chrome()` `chrome_opts.binary_location = r"C:\Pfad\zu\chrome.exe"` setzen

**Keine Bilder gefunden / leere Kapitel**

* Kapitel existiert nicht / Seitenstruktur geändert
* `Pause je Seite` erhöhen (z. B. `1.0–1.5 s`)
* Headless deaktivieren und erneut testen

**Kein Kapiteltitel**

* OPwiki hat (noch) keinen Eintrag → Datei wird ohne Titel erzeugt (gewollter Fallback)

**403/Rate-Limits**

* `Pause je Seite` erhöhen
* Headless aus, normalen Modus testen
* (Fortgeschritten) Eigener User-Agent/Proxy im Code setzen

---

## Roadmap

* Fortschrittsbalken (pro Kapitel & gesamt)
* Option „Seitenbilder nach CBZ löschen“
* Parallelisierte Kapitel-Jobs (mit Rücksicht auf Rate-Limits)
* Konfigurierbarer User-Agent / Proxy

---

## Mitmachen

Issues & PRs sind willkommen. Bitte angeben:

* OS & Python-Version
* Browser/Modus (Headless/Normal)
* Kapitelangabe
* relevanter Log-Ausschnitt

---

## Lizenz

```
MIT License — Copyright (c) 2025 Psy
```
