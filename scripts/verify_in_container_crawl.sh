#!/usr/bin/env bash
# Phase 0 · 0.11 — verify in-container crawl via Docker Compose.
# Authorized live crawl against ca.iaai.com. Uses an isolated compose project
# name; docker volume is separate from the host gitignored data/ tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Creating .env from .env.example with fresh tokens"
  cp .env.example .env
  TOKEN="$(openssl rand -hex 32)"
  CMD_TOKEN="$(openssl rand -hex 32)"
  if sed --version >/dev/null 2>&1; then
    sed -i "s/^IAAI_API_TOKEN=.*/IAAI_API_TOKEN=$TOKEN/" .env
    sed -i "s/^IAAI_COMMAND_TOKEN=.*/IAAI_COMMAND_TOKEN=$CMD_TOKEN/" .env
  else
    sed -i '' "s/^IAAI_API_TOKEN=.*/IAAI_API_TOKEN=$TOKEN/" .env
    sed -i '' "s/^IAAI_COMMAND_TOKEN=.*/IAAI_COMMAND_TOKEN=$CMD_TOKEN/" .env
  fi
fi

TOKEN="$(grep -E '^IAAI_API_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
CMD_TOKEN="$(grep -E '^IAAI_COMMAND_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
CMD_TOKEN="${CMD_TOKEN:-$TOKEN}"
if [[ -z "$TOKEN" || "$TOKEN" == change-me* ]]; then
  echo "Set a real IAAI_API_TOKEN in .env first (not the change-me placeholder)" >&2
  exit 2
fi

PROJECT="${IAAI_COMPOSE_PROJECT:-iaai011}"
# Prefer shell env, else the same .env Compose loads for published ports.
ENV_PORT="$(grep -E '^IAAI_API_PORT=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
PORT="${IAAI_API_PORT:-${ENV_PORT:-8000}}"
BASE="http://127.0.0.1:${PORT}"
AUTH=(-H "Authorization: Bearer $TOKEN")
CMD_AUTH=(-H "Authorization: Bearer $CMD_TOKEN")
STATUS_FILE="$(mktemp)"

cleanup() {
  rm -f "$STATUS_FILE"
  docker compose -p "$PROJECT" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building and starting compose project=$PROJECT"
docker compose -p "$PROJECT" up --build -d

echo "Waiting for /healthz"
for _ in $(seq 1 60); do
  if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "$BASE/healthz"
echo

echo "POST /commands/crawl"
curl -fsS -X POST "${CMD_AUTH[@]}" "$BASE/commands/crawl"
echo

echo "Polling /commands/crawl/status (max ~10 min)"
for i in $(seq 1 120); do
  curl -fsS "${AUTH[@]}" "$BASE/commands/crawl/status" >"$STATUS_FILE"
  STATUS="$(python3 - "$STATUS_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
job = d.get("job") or {}
print(job.get("status") or ("running" if d.get("running") else "unknown"))
PY
)"
  echo "poll $i status=$STATUS"
  case "$STATUS" in
    completed)
      echo "IN-CONTAINER CRAWL OK"
      exit 0
      ;;
    failed|partial)
      echo "IN-CONTAINER CRAWL FAILED status=$STATUS" >&2
      cat "$STATUS_FILE" >&2
      docker compose -p "$PROJECT" logs --tail=120 >&2 || true
      exit 1
      ;;
  esac
  sleep 5
done

echo "Timed out waiting for crawl" >&2
docker compose -p "$PROJECT" logs --tail=120 >&2 || true
exit 1
