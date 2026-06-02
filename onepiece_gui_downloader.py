from __future__ import annotations

import os
import webbrowser

import uvicorn


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    url = f"http://{host}:{port}"
    print("Die frühere Tkinter-GUI wurde durch eine WebApp ersetzt.")
    print(f"Starte Weboberfläche unter {url}")
    if host in {"127.0.0.1", "localhost"}:
        webbrowser.open(url)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
