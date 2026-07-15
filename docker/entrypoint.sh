#!/bin/sh
set -eu

if [ -z "${IAAI_API_TOKEN:-}" ]; then
  echo "ERROR: IAAI_API_TOKEN must be set (generate with: openssl rand -hex 32)" >&2
  exit 1
fi

export IAAI_REQUIRE_AUTH="${IAAI_REQUIRE_AUTH:-true}"
export IAAI_DATA_DIR="${IAAI_DATA_DIR:-/data}"
export IAAI_DB_PATH="${IAAI_DB_PATH:-${IAAI_DATA_DIR}/iaai_ontario.db}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"

mkdir -p "${IAAI_DATA_DIR}/raw"

exec python -m iaai_scraper.cli serve --host 0.0.0.0 --port "${IAAI_API_PORT:-8000}"
