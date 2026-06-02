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
* ⏱️ Integrierter FastAPI-Scheduler für automatische Kapitelprüfungen und Downloads

---

## Projektstruktur

```text
.
├─ app/
│  ├─ downloader.py          # Wiederverwendbare Download- und CBZ-Logik
│  ├─ main.py                # FastAPI-WebApp, Jobverwaltung, Scheduler-Anbindung und API-Endpunkte
│  ├─ scheduler.py           # Persistente Scheduler-Einstellungen, Timing und Auto-Checks
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

Die Downloads werden lokal in `./downloads` abgelegt, weil `docker-compose.yml` diesen Ordner nach `/downloads` in den Container mountet. Dort liegt standardmäßig auch die persistente Scheduler-Datei `/downloads/scheduler_state.json`, sodass Einstellungen und Fortschritt Container-Neustarts überleben.

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


## Automatischer Kapitel-Scheduler

Die WebApp enthält jetzt einen **integrierten Hintergrund-Scheduler**. Er läuft im bestehenden FastAPI-Prozess; es werden keine Cronjobs, separaten Services, noVNC-Container oder externen Scheduler benötigt. Die manuelle Download-Funktion bleibt unverändert nutzbar.

Über den Navigationslink **Scheduler-Einstellungen** oder direkt über `/settings` kannst du:

* den Scheduler aktivieren oder deaktivieren,
* Zeitzone, Download-Ordner, Browser, Headless-Modus, Seitenpause und CBZ-Aufräumen konfigurieren,
* das letzte erfolgreiche Kapitel und das nächste zu prüfende Kapitel setzen,
* tägliche Prüfungen zu einer festen Uhrzeit konfigurieren,
* ein Release-Follow-up-Fenster konfigurieren, das am eingestellten Wochentag ab einer Startzeit alle `release_check_interval_minutes` prüft,
* Lookahead-Prüfungen und das Überspringen bereits vorhandener CBZ-Dateien steuern,
* den aktuellen Status, letzte Prüf-/Erfolgszeiten, Fehler und die letzten Scheduler-Logs einsehen.

Der Scheduler speichert Einstellungen und Laufzeitstatus als JSON. Der Pfad ist per Umgebungsvariable konfigurierbar:

```bash
SCHEDULER_STATE_FILE=/downloads/scheduler_state.json
```

Wenn die Datei fehlt, wird sie mit sinnvollen Defaults angelegt. Wenn sie beschädigt ist, wird sie als `.corrupt.<timestamp>` gesichert und neu erstellt, damit die WebApp weiter starten kann. JSON-Updates werden atomar geschrieben.

Der Scheduler prüft vor einem Download leichtgewichtig, ob Kapitel-Seite 1 über die konfigurierte `BASE_URL_TEMPLATE` tatsächlich ein Bild liefert. Nicht verfügbare Kapitel werden nur protokolliert und nicht als Erfolg markiert. Bereits vorhandene `.cbz`-Dateien unterhalb des Download-Ordners werden bei aktiviertem `skip_existing_cbz` toleranter Kapitelnummer-Erkennung übersprungen und nicht gelöscht.

> Hinweis: Der Scheduler kann nur prüfen, ob ein Kapitel verfügbar ist; er garantiert keine Release-Termine. Bitte nutze respektvolle Intervalle und vermeide aggressives Polling. Das Standard-Release-Intervall von **120 Minuten** sollte nicht unterschritten werden.

## Wichtige Umgebungsvariablen

| Variable | Standard | Beschreibung |
| --- | --- | --- |
| `DOWNLOAD_ROOT` | `/downloads` | Standard-Zielordner für Downloads |
| `HEADLESS` | `true` | Headless-Standard für neue Jobs |
| `PAGE_SLEEP` | `0.5` (`1.0` in Compose) | Standardpause je Seite in Sekunden |
| `MAX_PAGES_GUESS` | `999` | Sicherheitsobergrenze pro Kapitel |
| `TIMEOUT_DOWNLOAD` | `30` | Timeout für Bilddownloads in Sekunden |
| `SCHEDULER_STATE_FILE` | `/downloads/scheduler_state.json` | Persistente Scheduler-Einstellungen und Laufzeitstatus |
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
* `GET /settings` – Scheduler-Einstellungsseite
* `POST /settings` – Scheduler-Einstellungen speichern
* `POST /settings/check-now` – sofortige Scheduler-Prüfung anstoßen
* `POST /jobs` – neuen Download-Job starten
* `GET /jobs/{job_id}` – Job-Detailseite
* `GET /api/jobs/{job_id}` – Jobstatus als JSON
* `POST /api/jobs/{job_id}/cancel` – laufenden Job abbrechen
* `GET /api/scheduler/settings` – Scheduler-Einstellungen als JSON
* `POST /api/scheduler/settings` – Scheduler-Einstellungen per JSON aktualisieren
* `GET /api/scheduler/status` – Scheduler-Status inklusive Laufzustand und Logs
* `POST /api/scheduler/check-now` – sofortige Scheduler-Prüfung per API anstoßen
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
