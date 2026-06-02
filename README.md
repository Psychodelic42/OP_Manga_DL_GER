# One Piece – Manga Downloader als Docker-WebApp

Dieses Projekt wurde von einer lokalen Tkinter/CLI-Anwendung zu einer **containerisierten WebApp** umgebaut. Die bisherigen Kernfunktionen bleiben erhalten: Kapitel können als Einzelwert, Bereich oder kombinierte Liste heruntergeladen, fortlaufend benannt und als **CBZ** verpackt werden. Die Bedienung läuft jetzt über den Browser.

> ⚠️ **Hinweis/Disclaimer**
> Dieses Projekt ist nur für den privaten Gebrauch gedacht. Beachte die Nutzungsbedingungen der Zielseiten und das Urheberrecht in deinem Land. Nutzung auf eigene Verantwortung.

---

## Features

* 🌐 **Weboberfläche** mit Formular, Job-Übersicht, Live-Protokoll und Abbrechen-Funktion
* 🐳 **Dockerfile** und **docker-compose.yml** für einen reproduzierbaren Container
* 🔽 Download von Einzelkapiteln (`1162`), Bereichen (`1150-1164`) und Listen (`1150,1152-1155,1160`)
* 🖼️ Automatisches Finden und Speichern der Seitenbilder
* 🗂️ Automatische CBZ-Erstellung pro Kapitel
* 🏷️ Deutsche Kapitel-Titel via OPwiki mit Dateinamen-Fallback
* 🧰 CLI-Kompatibilitätsmodus über `manga_downloader.py`

---

## Projektstruktur

```text
.
├─ app/
│  ├─ downloader.py          # Wiederverwendbare Download- und CBZ-Logik
│  ├─ main.py                # FastAPI-WebApp, Jobverwaltung und API-Endpunkte
│  ├─ static/styles.css      # Styling der Weboberfläche
│  └─ templates/             # HTML-Templates
├─ tests/                    # Pytest-Tests für Parsing und Hilfsfunktionen
├─ Dockerfile                # Container mit Python, Chromium und Chromedriver
├─ docker-compose.yml        # Lokaler Start mit gemountetem ./downloads-Ordner
├─ manga_downloader.py       # CLI-Kompatibilitätsstarter
└─ onepiece_gui_downloader.py# Startet die neue WebApp statt der alten Tkinter-GUI
```

---

## Schnellstart mit Docker Compose

```bash
docker compose up --build
```

Danach im Browser öffnen:

```text
http://localhost:8000
```

Die Downloads werden lokal in `./downloads` abgelegt, weil `docker-compose.yml` diesen Ordner nach `/downloads` in den Container mountet.

---

## Docker ohne Compose

```bash
docker build -t op-manga-dl-ger .
docker run --rm -p 8000:8000 -v "$PWD/downloads:/downloads" op-manga-dl-ger
```

---

## Lokale Entwicklung ohne Docker

Voraussetzungen:

* Python 3.10+
* Chrome/Chromium, Edge oder Firefox

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Optional kann die frühere GUI-Datei weiterhin gestartet werden. Sie öffnet jetzt die WebApp:

```bash
python onepiece_gui_downloader.py
```

CLI-Kompatibilitätsmodus:

```bash
python manga_downloader.py
```

---

## WebApp-Bedienung

1. Kapitelangabe eintragen:
   * `1162`
   * `1150-1164`
   * `1150,1152-1155,1160`
2. Download-Ordner im Container wählen, standardmäßig `/downloads`.
3. Browser auf `Auto` lassen. Im Docker-Container wird Chromium genutzt.
4. Headless aktiviert lassen.
5. Optional Pause je Seite und das Löschen temporärer Seitenbilder nach CBZ-Erstellung einstellen.
6. Download starten und das Live-Protokoll auf der Job-Seite verfolgen.

---

## Wichtige Umgebungsvariablen

| Variable | Standard | Beschreibung |
| --- | --- | --- |
| `DOWNLOAD_ROOT` | `/downloads` | Standard-Zielordner für Downloads |
| `HEADLESS` | `true` | Headless-Standard für neue Jobs |
| `PAGE_SLEEP` | `0.5` | Standardpause je Seite in Sekunden |
| `MAX_PAGES_GUESS` | `999` | Sicherheitsobergrenze pro Kapitel |
| `TIMEOUT_DOWNLOAD` | `30` | Timeout für Bilddownloads in Sekunden |
| `BASE_URL_TEMPLATE` | `https://onepiece.tube/manga/kapitel/{chapter}/{page}` | Seitenquelle |
| `CHROME_BIN` | `/usr/bin/chromium` im Dockerfile | Chromium/Chrome-Binary |
| `CHROMEDRIVER_PATH` | `/usr/bin/chromedriver` im Dockerfile | Chromedriver-Pfad |

---

## Tests

```bash
pytest
```

---

## API-Endpunkte

* `GET /` – Weboberfläche
* `POST /jobs` – neuen Download-Job starten
* `GET /jobs/{job_id}` – Job-Detailseite
* `GET /api/jobs/{job_id}` – Jobstatus als JSON
* `POST /api/jobs/{job_id}/cancel` – laufenden Job abbrechen
* `GET /health` – einfacher Healthcheck

---

## Troubleshooting

**Container startet, aber Downloads schlagen mit Browserfehler fehl**

* Baue das Image neu: `docker compose build --no-cache`
* Prüfe, ob Chromium und Chromedriver im Container vorhanden sind:
  `docker compose run --rm op-manga-dl-ger chromium --version`

**Keine Bilder gefunden / leere Kapitel**

* Kapitel existiert nicht oder die Zielseite hat ihre Struktur geändert.
* Erhöhe die Pause je Seite, z. B. auf `1.0` bis `1.5` Sekunden.

**403/Rate-Limits**

* Pause je Seite erhöhen.
* Weniger Kapitel pro Job starten.

---

## Lizenz

MIT License — Copyright (c) 2025 Psy
