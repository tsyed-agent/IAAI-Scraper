# IAAI Ontario scraper + API — minimal surface: one HTTP port, outbound HTTPS for crawls.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    IAAI_REQUIRE_AUTH=true \
    IAAI_DATA_DIR=/data \
    IAAI_DB_PATH=/data/iaai_ontario.db \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY iaai_scraper/ iaai_scraper/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN groupadd --system iaai \
    && useradd --system --gid iaai --home-dir /data --shell /usr/sbin/nologin iaai \
    && mkdir -p /data/raw \
    && chown -R iaai:iaai /data /app

USER iaai

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
