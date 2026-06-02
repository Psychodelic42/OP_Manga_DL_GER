FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_ROOT=/downloads \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY manga_downloader.py onepiece_gui_downloader.py README.md LICENSE ./
RUN mkdir -p /downloads

EXPOSE 8000
VOLUME ["/downloads"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
