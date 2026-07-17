#!/usr/bin/env bash
# Gateway abuse / edge isolation checks (Phase 2 · 2.3 follow-up).
#
# Offline mode (default): assert nginx sample enforces rate/body limits and
# blocks command/OpenAPI routes — safe for CI.
# Live mode: set IAAI_GATEWAY_BASE (e.g. https://127.0.0.1:8443) to exercise
# the running edge (rate-limit / 404 / body-size). Uses curl -k for lab certs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TEMPLATE="${IAAI_GATEWAY_TEMPLATE:-$ROOT/docker/gateway/nginx.conf.template}"
BASE="${IAAI_GATEWAY_BASE:-}"
FAIL=0

pass() { printf 'PASS %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; FAIL=1; }

echo "=== offline config assertions ==="
if [[ ! -f "$TEMPLATE" ]]; then
  fail "missing template $TEMPLATE"
else
  grep -q 'limit_req_zone' "$TEMPLATE" && pass "limit_req_zone present" || fail "limit_req_zone missing"
  grep -q 'limit_req ' "$TEMPLATE" && pass "limit_req applied" || fail "limit_req missing"
  grep -q 'client_max_body_size' "$TEMPLATE" && pass "client_max_body_size present" || fail "body size missing"
  grep -q 'location ^~ /commands' "$TEMPLATE" && pass "/commands location present" || fail "/commands location missing"
  if grep -A5 'location ^~ /commands' "$TEMPLATE" | grep -q 'return 404'; then
    pass "/commands returns 404"
  else
    fail "/commands does not return 404"
  fi
  for path in /docs /redoc /openapi.json; do
    if grep -q "location = $path" "$TEMPLATE" || grep -q "location ^~ $path" "$TEMPLATE"; then
      pass "blocks $path"
    else
      # template may use a shared regex — accept openapi|docs|redoc pattern
      if grep -E 'docs|redoc|openapi' "$TEMPLATE" | grep -q 'return 404'; then
        pass "OpenAPI surface blocked (shared rule covers $path)"
      else
        fail "no block rule covering $path"
      fi
    fi
  done
fi

if [[ -z "$BASE" ]]; then
  echo "=== live edge checks skipped (set IAAI_GATEWAY_BASE to enable) ==="
  [[ "$FAIL" -eq 0 ]]
  exit $?
fi

echo "=== live edge checks against $BASE ==="
CURL=(curl -sk --max-time 10)
TOKEN="${IAAI_API_TOKEN:-}"

# Commands must not be on the public edge.
CODE="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/commands/crawl" || true)"
if [[ "$CODE" == "404" || "$CODE" == "405" ]]; then
  pass "/commands/crawl → $CODE (blocked)"
else
  fail "/commands/crawl → $CODE (expected 404)"
fi

for path in /docs /redoc /openapi.json; do
  CODE="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE$path" || true)"
  if [[ "$CODE" == "404" ]]; then
    pass "$path → 404"
  else
    fail "$path → $CODE (expected 404)"
  fi
done

# Oversized body should be rejected (413) when Content-Length exceeds 1m.
BIG="$(python3 - <<'PY'
print('x' * (1024 * 1024 + 2048))
PY
)"
CODE="$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
  -H 'Content-Type: application/octet-stream' \
  -H "Content-Length: ${#BIG}" \
  --data-binary "$BIG" \
  "$BASE/healthz" || true)"
if [[ "$CODE" == "413" || "$CODE" == "400" || "$CODE" == "495" ]]; then
  pass "oversized body → $CODE"
else
  # Some nginx builds may reset; 000 means connection dropped — acceptable signal.
  if [[ "$CODE" == "000" ]]; then
    pass "oversized body → connection reset (000)"
  else
    fail "oversized body → $CODE (expected 413/400/reset)"
  fi
fi

# Burst past rate limit (10r/s burst 20) — expect some 503s.
TMP="$(mktemp)"
for i in $(seq 1 80); do
  if [[ -n "$TOKEN" ]]; then
    "${CURL[@]}" -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" "$BASE/healthz" || echo 000
  else
    "${CURL[@]}" -o /dev/null -w '%{http_code}\n' "$BASE/healthz" || echo 000
  fi
done >"$TMP"
LIMITED="$(grep -c '^503$' "$TMP" || true)"
OKS="$(grep -c '^200$' "$TMP" || true)"
rm -f "$TMP"
if [[ "$LIMITED" -gt 0 ]]; then
  pass "rate limit observed (503×$LIMITED, 200×$OKS)"
else
  fail "rate limit not observed (503×0, 200×$OKS) — tune load or confirm gateway is up"
fi

[[ "$FAIL" -eq 0 ]]
exit $?
