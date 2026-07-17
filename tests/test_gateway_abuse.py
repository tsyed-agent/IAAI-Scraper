"""Offline gateway abuse-control assertions (+ script smoke)."""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docker" / "gateway" / "nginx.conf.template"
SCRIPT = ROOT / "scripts" / "gateway_abuse_check.sh"


def test_nginx_template_has_abuse_controls():
    text = TEMPLATE.read_text()
    assert "limit_req_zone" in text
    assert re.search(r"limit_req\s+zone=iaai_edge", text)
    assert "client_max_body_size" in text
    assert "location ^~ /commands" in text
    assert "return 404" in text
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert path in text


def test_gateway_abuse_check_script_offline_passes():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    env = {**os.environ, "IAAI_GATEWAY_BASE": ""}  # force offline-only
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS limit_req_zone present" in r.stdout
    assert "PASS /commands returns 404" in r.stdout
    assert "live edge checks skipped" in r.stdout
