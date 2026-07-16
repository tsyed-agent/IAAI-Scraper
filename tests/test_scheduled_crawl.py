"""Offline tests for scripts/scheduled_crawl.sh (no network, no real crawl)."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scheduled_crawl.sh"


def _write_fake(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(env: dict[str, str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **env}
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=str(ROOT),
        env=merged,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_success_exits_zero_without_retry(tmp_path: Path):
    fake = tmp_path / "crawl.sh"
    counter = tmp_path / "n"
    counter.write_text("0")
    _write_fake(
        fake,
        f"#!/bin/sh\nn=$(cat '{counter}'); echo $((n+1)) > '{counter}'; echo ok; exit 0\n",
    )
    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_RETRY_DELAY_S": "0",
    })
    assert r.returncode == 0
    assert counter.read_text().strip() == "1"
    assert "crawl completed" in r.stdout


def test_failure_retries_exactly_once(tmp_path: Path):
    fake = tmp_path / "crawl.sh"
    counter = tmp_path / "n"
    counter.write_text("0")
    _write_fake(
        fake,
        f"#!/bin/sh\nn=$(cat '{counter}'); echo $((n+1)) > '{counter}'; echo boom; exit 7\n",
    )
    alert = tmp_path / "alert.sh"
    alert_log = tmp_path / "alert.log"
    _write_fake(alert, f"#!/bin/sh\ncat >> '{alert_log}'\n")
    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_RETRY_DELAY_S": "0",
        "IAAI_SCHED_ALERT_HOOK": str(alert),
    })
    assert r.returncode == 1
    assert counter.read_text().strip() == "2"
    assert "retrying once" in r.stdout
    assert "ALERT:" in r.stdout
    assert "failed twice" in alert_log.read_text()


def test_lock_busy_exits_zero_without_retry(tmp_path: Path):
    fake = tmp_path / "crawl.sh"
    counter = tmp_path / "n"
    counter.write_text("0")
    _write_fake(
        fake,
        "#!/bin/sh\n"
        f"n=$(cat '{counter}'); echo $((n+1)) > '{counter}'\n"
        "echo 'RuntimeError: Another crawl appears to be running (lock: data/crawl.lock)'\n"
        "exit 1\n",
    )
    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_RETRY_DELAY_S": "0",
    })
    assert r.returncode == 0
    assert counter.read_text().strip() == "1"
    assert "another crawl running" in r.stdout.lower()
    assert "retrying once" not in r.stdout


def test_retry_succeeds_after_first_failure(tmp_path: Path):
    fake = tmp_path / "crawl.sh"
    counter = tmp_path / "n"
    counter.write_text("0")
    _write_fake(
        fake,
        "#!/bin/sh\n"
        f"n=$(cat '{counter}'); echo $((n+1)) > '{counter}'\n"
        "if [ \"$n\" -eq 0 ]; then echo fail; exit 1; fi\n"
        "echo ok; exit 0\n",
    )
    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_RETRY_DELAY_S": "0",
    })
    assert r.returncode == 0
    assert counter.read_text().strip() == "2"
    assert "crawl completed on retry" in r.stdout
