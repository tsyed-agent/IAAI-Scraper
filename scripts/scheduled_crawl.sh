#!/usr/bin/env sh
# Cron/systemd entrypoint for a whole Ontario crawl with one delayed retry.
#
# - Invokes the CLI crawl (not POST /commands/crawl).
# - On lock contention ("another crawl running"), exits 0 without retry.
# - On other failures: wait, retry once; on second failure, loud log + optional hook.
#
# Env:
#   IAAI_PYTHON              python binary (default: python3)
#   IAAI_CRAWL_CMD           full command string (default: "$IAAI_PYTHON -m iaai_scraper.cli crawl")
#   IAAI_SCHED_RETRY_DELAY_S nonnegative integer seconds before retry (default: 1200)
#   IAAI_SCHED_JITTER_S      max nonnegative integer startup delay (default: 300)
#   IAAI_SCHED_ALERT_HOOK    optional shell command run after two failures
#
# See docs/ops-scheduling.md.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RETRY_DELAY_S="${IAAI_SCHED_RETRY_DELAY_S:-1200}"
JITTER_MAX_S="${IAAI_SCHED_JITTER_S:-300}"
PYTHON="${IAAI_PYTHON:-python3}"
CRAWL_CMD="${IAAI_CRAWL_CMD:-$PYTHON -m iaai_scraper.cli crawl}"
ALERT_HOOK="${IAAI_SCHED_ALERT_HOOK:-}"
TAG="iaai-scheduled-crawl"

log() {
  printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" "$*"
}

configuration_error() {
  MSG="$1"
  log "ALERT: $MSG"
  if [ -n "$ALERT_HOOK" ]; then
    printf '%s\n' "$MSG" | sh -c "$ALERT_HOOK" || log "alert hook exited non-zero"
  fi
  exit 2
}

case "$RETRY_DELAY_S" in
  ''|*[!0-9]*)
    configuration_error \
      "invalid IAAI_SCHED_RETRY_DELAY_S='$RETRY_DELAY_S' (expected a nonnegative integer)"
    ;;
esac

case "$JITTER_MAX_S" in
  ''|*[!0-9]*)
    configuration_error \
      "invalid IAAI_SCHED_JITTER_S='$JITTER_MAX_S' (expected a nonnegative integer)"
    ;;
esac

is_lock_busy() {
  # Match the RuntimeError text from iaai_scraper.crawler._acquire_lock
  grep -qi 'Another crawl appears to be running' "$1"
}

run_crawl() {
  # Allow IAAI_CRAWL_CMD to be a multi-word command string (tests / wrappers).
  # shellcheck disable=SC2086
  sh -c "$CRAWL_CMD"
}

random_jitter() {
  MAX="$1"
  if [ "$MAX" -eq 0 ]; then
    printf '0\n'
    return 0
  fi

  # /dev/urandom is available on the POSIX hosts supported by this wrapper.
  # awk keeps the implementation independent of non-POSIX shell $RANDOM.
  VALUE="$(od -An -N4 -tu4 /dev/urandom 2>/dev/null |
    awk -v max="$MAX" 'NF { print $1 % (max + 1); exit }')"
  case "$VALUE" in
    ''|*[!0-9]*) return 1 ;;
  esac
  printf '%s\n' "$VALUE"
}

JITTER_DELAY_S="$(random_jitter "$JITTER_MAX_S")" || {
  configuration_error "unable to generate startup jitter from /dev/urandom"
}

OUT="$(mktemp "${TMPDIR:-/tmp}/iaai-sched.XXXXXX")"
trap 'rm -f "$OUT"' EXIT

if [ "$JITTER_DELAY_S" -gt 0 ]; then
  log "waiting ${JITTER_DELAY_S}s startup jitter (max ${JITTER_MAX_S}s)"
  sleep "$JITTER_DELAY_S"
fi

log "starting crawl: $CRAWL_CMD"
set +e
run_crawl >"$OUT" 2>&1
RC=$?
set -e
cat "$OUT"

if [ "$RC" -eq 0 ]; then
  log "crawl completed"
  exit 0
fi

if is_lock_busy "$OUT"; then
  log "another crawl running — skip (no retry)"
  exit 0
fi

log "crawl failed (rc=$RC); retrying once after ${RETRY_DELAY_S}s"
sleep "$RETRY_DELAY_S"

log "retry starting"
set +e
run_crawl >"$OUT" 2>&1
RC=$?
set -e
cat "$OUT"

if [ "$RC" -eq 0 ]; then
  log "crawl completed on retry"
  exit 0
fi

if is_lock_busy "$OUT"; then
  log "another crawl running on retry — skip"
  exit 0
fi

MSG="scheduled crawl failed twice (last_rc=$RC)"
log "ALERT: $MSG"
if [ -n "$ALERT_HOOK" ]; then
  # Hook receives the alert text on stdin; failures here must not mask exit 1.
  printf '%s\n' "$MSG" | sh -c "$ALERT_HOOK" || log "alert hook exited non-zero"
fi
exit 1
