"""Offline tests for scripts/scheduled_crawl.sh (no network, no real crawl)."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scheduled_crawl.sh"


def _write_fake(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(env: dict[str, str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    # Keep offline tests deterministic and avoid waiting for production jitter.
    merged = {**os.environ, "IAAI_SCHED_JITTER_S": "0", **env}
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


@pytest.mark.parametrize("value", ["-1", "not-a-number"])
def test_invalid_retry_delay_is_rejected_before_crawl(tmp_path: Path, value: str):
    fake = tmp_path / "crawl.sh"
    marker = tmp_path / "ran"
    _write_fake(fake, f"#!/bin/sh\necho ran > '{marker}'\n")

    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_RETRY_DELAY_S": value,
    })

    assert r.returncode == 2
    assert not marker.exists()
    assert "invalid IAAI_SCHED_RETRY_DELAY_S" in r.stdout


def test_invalid_jitter_is_rejected_before_crawl(tmp_path: Path):
    fake = tmp_path / "crawl.sh"
    marker = tmp_path / "ran"
    _write_fake(fake, f"#!/bin/sh\necho ran > '{marker}'\n")

    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_JITTER_S": "-5",
    })

    assert r.returncode == 2
    assert not marker.exists()
    assert "invalid IAAI_SCHED_JITTER_S" in r.stdout


def test_configured_jitter_runs_before_crawl_without_waiting(tmp_path: Path):
    fake = tmp_path / "crawl.sh"
    sleep_log = tmp_path / "sleep.log"
    order_log = tmp_path / "order.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake(fake, f"#!/bin/sh\necho crawl >> '{order_log}'\n")
    _write_fake(bin_dir / "od", "#!/bin/sh\necho '  1'\n")
    _write_fake(
        bin_dir / "sleep",
        f"#!/bin/sh\necho \"sleep:$1\" > '{sleep_log}'; exit 0\n",
    )

    r = _run({
        "IAAI_CRAWL_CMD": str(fake),
        "IAAI_SCHED_JITTER_S": "5",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    })

    assert r.returncode == 0
    assert sleep_log.read_text().strip() == "sleep:1"
    assert order_log.read_text().splitlines() == ["crawl"]
