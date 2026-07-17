"""Offline tests for the durable crawl job queue / worker (2.4b)."""
from __future__ import annotations

import time
from pathlib import Path

from typer.testing import CliRunner

from iaai_scraper import cli
from iaai_scraper.worker import DurableWorker, JobQueue, write_metrics


def test_enqueue_claim_complete_persists(tmp_path: Path):
    db = tmp_path / "jobs.db"
    queue = JobQueue(db)
    job = queue.enqueue("crawl", {"max_list_pages": 1}, max_attempts=2)
    assert job.status == "queued"
    claimed = queue.claim_next(worker_id="w1", lease_seconds=60)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempt == 1
    done = queue.complete(job.id, {"crawl_status": "completed"})
    assert done.status == "completed"
    assert done.result["crawl_status"] == "completed"
    # Survives reopen
    queue.close()
    queue2 = JobQueue(db)
    again = queue2.get(job.id)
    assert again is not None
    assert again.status == "completed"
    queue2.close()


def test_restart_recovers_stale_running_job(tmp_path: Path):
    db = tmp_path / "jobs.db"
    queue = JobQueue(db)
    job = queue.enqueue("crawl", {}, max_attempts=2)
    claimed = queue.claim_next(worker_id="w1", lease_seconds=1)
    assert claimed is not None
    assert claimed.status == "running"
    # Simulate crash: drop the queue without completing; lease expires.
    queue.close()
    time.sleep(1.05)
    queue2 = JobQueue(db)
    recovered = queue2.recover_stale()
    assert len(recovered) == 1
    assert recovered[0].id == job.id
    assert recovered[0].status == "queued"
    # Worker can claim and finish after restart.
    calls: list[dict] = []

    def runner(payload):
        calls.append(payload)
        return {"crawl_status": "completed", "ontario_seen": 3}

    w = DurableWorker(queue2, runner=runner, worker_id="w2", lease_seconds=60)
    done = w.run_once()
    assert done is not None
    assert done.status == "completed"
    assert calls
    w.close()


def test_failed_job_requeues_then_alerts(tmp_path: Path):
    db = tmp_path / "jobs.db"
    metrics = tmp_path / "metrics" / "crawl_worker.prom"
    alerts: list[str] = []
    queue = JobQueue(db)
    queue.enqueue("crawl", {}, max_attempts=2)

    def boom(_payload):
        raise RuntimeError("imperva blocked")

    w = DurableWorker(
        queue,
        runner=boom,
        alert=alerts.append,
        worker_id="w1",
        metrics_path=metrics,
    )
    first = w.run_once()
    assert first is not None
    assert first.status == "queued"  # re-queued after attempt 1
    assert not alerts

    second = w.run_once()
    assert second is not None
    assert second.status == "failed"
    assert second.attempt == 2
    assert alerts and "imperva blocked" in alerts[0]
    assert metrics.is_file()
    text = metrics.read_text()
    assert 'iaai_crawl_jobs{status="failed"} 1' in text
    w.close()


def test_write_metrics_counts(tmp_path: Path):
    db = tmp_path / "jobs.db"
    path = tmp_path / "out.prom"
    queue = JobQueue(db)
    queue.enqueue("crawl", {})
    queue.enqueue("crawl", {})
    j = queue.claim_next(worker_id="w", lease_seconds=30)
    assert j
    queue.complete(j.id, {})
    write_metrics(queue, path, last_duration_s=12.5)
    body = path.read_text()
    assert 'iaai_crawl_jobs{status="queued"} 1' in body
    assert 'iaai_crawl_jobs{status="completed"} 1' in body
    assert "iaai_crawl_job_last_duration_seconds 12.500" in body
    queue.close()


def test_cli_enqueue_and_worker_once(tmp_path: Path, monkeypatch):
    jobs_db = tmp_path / "jobs.db"
    monkeypatch.setenv("IAAI_DATA_DIR", str(tmp_path))
    runner = CliRunner()

    # Patch DurableWorker runner via enqueue then process with fake by
    # monkeypatching default_crawl_runner used inside DurableWorker when None —
    # instead call worker module through CLI after injecting via env is hard.
    # Use library path for worker; CLI enqueue only.
    r = runner.invoke(
        cli.app,
        ["enqueue-crawl", "--jobs-db", str(jobs_db), "--max-pages", "1"],
    )
    assert r.exit_code == 0, r.output
    assert '"status": "queued"' in r.output

    calls = []

    def ok(payload):
        calls.append(payload)
        return {"crawl_status": "completed"}

    queue = JobQueue(jobs_db)
    w = DurableWorker(queue, runner=ok, worker_id="cli-test")
    done = w.run_once()
    assert done.status == "completed"
    assert calls and calls[0]["max_list_pages"] == 1
    w.close()
