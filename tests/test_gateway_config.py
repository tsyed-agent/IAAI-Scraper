"""Offline assertions for the TLS gateway sample (no Docker required)."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NGINX_TEMPLATE = ROOT / "docker" / "gateway" / "nginx.conf.template"
COMPOSE = ROOT / "docker-compose.gateway.yml"
CERT_SCRIPT = ROOT / "scripts" / "gen_gateway_certs.sh"
OPS_DOC = ROOT / "docs" / "ops-gateway.md"


def test_nginx_conf_has_tls_limits_and_blocks_commands():
    text = NGINX_TEMPLATE.read_text()
    assert "ssl_certificate" in text
    assert "ssl_certificate_key" in text
    assert "TLSv1.2" in text and "TLSv1.3" in text
    assert "limit_req_zone" in text
    assert "limit_conn_zone" in text
    assert "client_max_body_size 1m" in text
    assert "log_format iaai_audit" in text
    assert 'set $iaai_upstream "iaai-api:8000"' in text
    assert "resolver 127.0.0.11" in text
    # Redirect must keep the compose-mapped HTTPS port (not bare :443).
    assert "https://$host:${IAAI_GATEWAY_HTTPS_PORT}$request_uri" in text

    # Command / docs surfaces must be denied at the edge.
    for needle in (
        "location ^~ /commands",
        "location ^~ /docs",
        "location ^~ /redoc",
        "location = /openapi.json",
    ):
        assert needle in text
    assert text.count("return 404") >= 4


def test_compose_overlay_publishes_https_and_mounts_config():
    text = COMPOSE.read_text()
    assert "iaai-gateway" in text
    assert "nginx:1.27-alpine" in text
    assert "docker/gateway/nginx.conf.template" in text
    assert "docker/gateway/certs" in text
    assert "IAAI_GATEWAY_HTTPS_PORT" in text
    assert "envsubst" in text
    assert "iaai-internal" in text
    # Commands remain on loopback from the base compose — gateway is HTTPS edge.
    assert re.search(r"ports:\s*\n\s*-\s*\"\$\{IAAI_GATEWAY_HTTP_PORT", text)


def test_ops_doc_and_cert_script_exist():
    assert OPS_DOC.is_file()
    assert "command" in OPS_DOC.read_text().lower()
    assert "IAAI_GATEWAY_HTTPS_PORT" in OPS_DOC.read_text()
    assert CERT_SCRIPT.is_file()
    mode = CERT_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "gen_gateway_certs.sh must be executable"


def test_gen_gateway_certs_writes_pem(tmp_path: Path):
    if shutil.which("openssl") is None:
        return
    cert_dir = tmp_path / "certs"
    r = subprocess.run(
        ["bash", str(CERT_SCRIPT)],
        cwd=str(ROOT),
        env={**os.environ, "IAAI_GATEWAY_CERT_DIR": str(cert_dir), "IAAI_GATEWAY_CN": "test.local"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert (cert_dir / "fullchain.pem").is_file()
    assert (cert_dir / "privkey.pem").is_file()
    # Idempotent without FORCE
    r2 = subprocess.run(
        ["bash", str(CERT_SCRIPT)],
        cwd=str(ROOT),
        env={**os.environ, "IAAI_GATEWAY_CERT_DIR": str(cert_dir)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert r2.returncode == 0
    assert "already present" in r2.stdout


def test_envsubst_redirect_keeps_https_port(tmp_path: Path):
    if shutil.which("envsubst") is None:
        return
    out = tmp_path / "nginx.conf"
    r = subprocess.run(
        ["envsubst", "${IAAI_GATEWAY_HTTPS_PORT}"],
        input=NGINX_TEMPLATE.read_text(),
        capture_output=True,
        text=True,
        env={**os.environ, "IAAI_GATEWAY_HTTPS_PORT": "8443"},
        timeout=10,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out.write_text(r.stdout)
    assert "https://$host:8443$request_uri" in r.stdout
    assert "${IAAI_GATEWAY_HTTPS_PORT}" not in r.stdout
    # nginx variables must survive envsubst
    assert "$request_uri" in r.stdout
    assert "$remote_addr" in r.stdout
