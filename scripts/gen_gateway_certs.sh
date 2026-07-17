#!/usr/bin/env bash
# Generate lab TLS material for docker/gateway (self-signed).
# Production: replace with certificates from your CA / ACME / secrets manager.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="${IAAI_GATEWAY_CERT_DIR:-$ROOT/docker/gateway/certs}"
CN="${IAAI_GATEWAY_CN:-localhost}"
DAYS="${IAAI_GATEWAY_CERT_DAYS:-825}"

mkdir -p "$CERT_DIR"
umask 077

if [[ -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" && "${IAAI_GATEWAY_FORCE:-0}" != "1" ]]; then
  echo "certs already present in $CERT_DIR (set IAAI_GATEWAY_FORCE=1 to overwrite)"
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is required" >&2
  exit 1
fi

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/privkey.pem" \
  -out "$CERT_DIR/fullchain.pem" \
  -days "$DAYS" \
  -subj "/CN=$CN" \
  -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/privkey.pem"
chmod 644 "$CERT_DIR/fullchain.pem"
echo "wrote $CERT_DIR/fullchain.pem and privkey.pem (CN=$CN, ${DAYS}d)"
