"""Durable crawl job queue + worker (Phase 2 · 2.4b).

Jobs persist in a small SQLite DB under ``IAAI_DATA_DIR`` so a worker crash or
host restart does not lose queued/in-flight state. The API ``SyncManager`` remains
for manual ``POST /commands/crawl`` only — production schedules should enqueue
here and run ``python -m iaai_scraper.cli worker``.

Acceptance (doc 09 / 10 · 2.4b):
- restart recovers stale ``running`` jobs
- one automatic re-queue attempt, then fail + optional alert
- Prometheus textfile metrics for scrape/node_exporter
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from . import config

log = logging.getLogger("iaai.worker")

JobRunner = Callable[[dict[str, Any]], dict[str, Any]]
AlertHook = Callable[[str], None]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    attempt       INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 2,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    result_json   TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    lease_owner   TEXT,
    lease_until   REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease
    ON jobs (status, lease_until);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def default_jobs_db_path() -> Path:
    override = os.getenv("IAAI_JOBS_DB", "").strip()
    if override:
        return Path(override)
    return Path(config.DATA_DIR) / "jobs.db"


def default_metrics_path() -> Path:
    override = os.getenv("IAAI_WORKER_METRICS_PATH", "").strip()
    if override:
        return Path(override)
    return Path(config.DATA_DIR) / "metrics" / "crawl_worker.prom"


@dataclass
class Job:
    id: str
    kind: str
    status: str
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_until: Optional[float] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        payload = json.loads(row["payload_json"] or "{}")
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return cls(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            payload=payload if isinstance(payload, dict) else {},
            result=result if isinstance(result, dict) else None,
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
        }


class JobQueue:
    """SQLite-backed durable job queue."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_jobs_db_path()
        self._conn = _connect(self.db_path)

    def close(self) -> None:
        self._conn.close()

    def enqueue(
        self,
        kind: str = "crawl",
        payload: Optional[dict[str, Any]] = None,
        *,
        max_attempts: int = 2,
        job_id: Optional[str] = None,
    ) -> Job:
        job_id = job_id or uuid.uuid4().hex[:12]
        created = _utc_now()
        self._conn.execute(
            "INSERT INTO jobs (id, kind, status, attempt, max_attempts, payload_json, created_at) "
            "VALUES (?, ?, 'queued', 0, ?, ?, ?)",
            (
                job_id,
                kind,
                max(1, int(max_attempts)),
                json.dumps(payload or {}, sort_keys=True),
                created,
            ),
        )
        self._conn.commit()
        log.info("enqueued job id=%s kind=%s", job_id, kind)
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> Optional[Job]:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def recover_stale(self, *, now: Optional[float] = None) -> list[Job]:
        """Re-queue ``running`` jobs whose lease expired (worker crashed)."""
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE status = 'running' "
            "AND (lease_until IS NULL OR lease_until < ?)",
            (now,),
        ).fetchall()
        recovered: list[Job] = []
        for row in rows:
            job_id = row["id"]
            job = self.get(job_id)
            if not job:
                continue
            if job.attempt >= job.max_attempts:
                self._conn.execute(
                    "UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, "
                    "lease_owner = NULL, lease_until = NULL WHERE id = ?",
                    (
                        job.error
                        or "stale running lease expired; max attempts exhausted",
                        _utc_now(),
                        job_id,
                    ),
                )
            else:
                self._conn.execute(
                    "UPDATE jobs SET status = 'queued', error = ?, started_at = NULL, "
                    "lease_owner = NULL, lease_until = NULL WHERE id = ?",
                    (
                        "recovered after stale running lease (worker restart)",
                        job_id,
                    ),
                )
            recovered.append(self.get(job_id))  # type: ignore[arg-type]
            log.warning("recovered stale job id=%s → %s", job_id, recovered[-1].status)
        if recovered:
            self._conn.commit()
        return recovered

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 3600.0,
        now: Optional[float] = None,
    ) -> Optional[Job]:
        """Atomically claim the oldest queued job."""
        now = time.time() if now is None else now
        self.recover_stale(now=now)
        row = self._conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        job_id = row["id"]
        lease_until = now + float(lease_seconds)
        cur = self._conn.execute(
            "UPDATE jobs SET status = 'running', attempt = attempt + 1, "
            "started_at = COALESCE(started_at, ?), lease_owner = ?, lease_until = ?, "
            "error = NULL WHERE id = ? AND status = 'queued'",
            (_utc_now(), worker_id, lease_until, job_id),
        )
        self._conn.commit()
        if cur.rowcount != 1:
            return None
        return self.get(job_id)

    def heartbeat(self, job_id: str, *, lease_seconds: float = 3600.0) -> None:
        self._conn.execute(
            "UPDATE jobs SET lease_until = ? WHERE id = ? AND status = 'running'",
            (time.time() + float(lease_seconds), job_id),
        )
        self._conn.commit()

    def complete(self, job_id: str, result: Optional[dict[str, Any]] = None) -> Job:
        self._conn.execute(
            "UPDATE jobs SET status = 'completed', result_json = ?, error = NULL, "
            "finished_at = ?, lease_owner = NULL, lease_until = NULL WHERE id = ?",
            (json.dumps(result or {}, sort_keys=True), _utc_now(), job_id),
        )
        self._conn.commit()
        job = self.get(job_id)
        assert job is not None
        return job

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        requeue: bool = False,
    ) -> Job:
        if requeue:
            self._conn.execute(
                "UPDATE jobs SET status = 'queued', error = ?, started_at = NULL, "
                "finished_at = NULL, lease_owner = NULL, lease_until = NULL WHERE id = ?",
                (error, job_id),
            )
        else:
            self._conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, "
                "lease_owner = NULL, lease_until = NULL WHERE id = ?",
                (error, _utc_now(), job_id),
            )
        self._conn.commit()
        job = self.get(job_id)
        assert job is not None
        return job

    def counts_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}


def write_metrics(
    queue: JobQueue,
    path: Optional[Path] = None,
    *,
    last_duration_s: Optional[float] = None,
) -> Path:
    """Write Prometheus textfile metrics for node_exporter / local scrape."""
    path = Path(path) if path else default_metrics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = queue.counts_by_status()
    lines = [
        "# HELP iaai_crawl_jobs Jobs in the durable worker queue by status",
        "# TYPE iaai_crawl_jobs gauge",
    ]
    for status in ("queued", "running", "completed", "failed"):
        lines.append(f'iaai_crawl_jobs{{status="{status}"}} {counts.get(status, 0)}')
    if last_duration_s is not None:
        lines.append(
            "# HELP iaai_crawl_job_last_duration_seconds Duration of the last processed job"
        )
        lines.append("# TYPE iaai_crawl_job_last_duration_seconds gauge")
        lines.append(f"iaai_crawl_job_last_duration_seconds {last_duration_s:.3f}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def default_crawl_runner(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a real Ontario crawl via ``Crawler`` (used by the CLI worker)."""
    from .crawler import Crawler

    settings = config.CrawlSettings(
        page_size=int(payload.get("page_size") or config.ONTARIO_PAGE_SIZE),
        max_list_pages=int(payload.get("max_list_pages") or config.MAX_LIST_PAGES),
        enrich_details=bool(payload.get("enrich_details", config.ENRICH_DETAILS)),
        ontario_at_source=bool(payload.get("ontario_at_source", True)),
        branch_ids=str(payload.get("branch_ids") or config.ONTARIO_BRANCH_IDS_CSV),
    )
    report = asyncio.run(Crawler(settings).run())
    result = {
        "crawl_status": report.status,
        "pages": report.pages,
        "ontario_seen": report.ontario_seen,
        "total_expected": report.total_canada,
        "total_seen": report.canada_rows_seen,
        "inserted": report.inserted,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "archived": report.archived,
        "note": report.note,
        "ontario_by_branch": report.ontario_by_branch,
    }
    if report.status != "completed":
        raise RuntimeError(report.note or f"crawl status={report.status}")
    return result


def _default_alert(message: str) -> None:
    hook = os.getenv("IAAI_SCHED_ALERT_HOOK", "").strip()
    log.error("ALERT: %s", message)
    if not hook:
        return
    import subprocess

    try:
        subprocess.run(
            ["sh", "-c", hook],
            input=message + "\n",
            text=True,
            check=False,
            timeout=60,
        )
    except Exception:  # noqa: BLE001
        log.exception("alert hook failed")


class DurableWorker:
    """Claim and execute durable jobs; safe across process restarts."""

    def __init__(
        self,
        queue: Optional[JobQueue] = None,
        *,
        runner: Optional[JobRunner] = None,
        alert: Optional[AlertHook] = None,
        worker_id: Optional[str] = None,
        lease_seconds: float = 3600.0,
        metrics_path: Optional[Path] = None,
    ) -> None:
        self.queue = queue or JobQueue()
        self.runner = runner or default_crawl_runner
        self.alert = alert or _default_alert
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = float(lease_seconds)
        self.metrics_path = Path(metrics_path) if metrics_path else default_metrics_path()

    def close(self) -> None:
        self.queue.close()

    def run_once(self) -> Optional[Job]:
        """Process at most one job. Returns the job, or None if queue empty."""
        job = self.queue.claim_next(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            write_metrics(self.queue, self.metrics_path)
            return None

        log.info(
            "claimed job id=%s kind=%s attempt=%s/%s",
            job.id,
            job.kind,
            job.attempt,
            job.max_attempts,
        )
        t0 = time.monotonic()
        try:
            if job.kind != "crawl":
                raise RuntimeError(f"unsupported job kind: {job.kind}")
            result = self.runner(job.payload)
            done = self.queue.complete(job.id, result)
            duration = time.monotonic() - t0
            write_metrics(self.queue, self.metrics_path, last_duration_s=duration)
            log.info("job completed id=%s duration_s=%.1f", done.id, duration)
            return done
        except Exception as exc:  # noqa: BLE001
            duration = time.monotonic() - t0
            err = f"{type(exc).__name__}: {exc}"
            can_retry = job.attempt < job.max_attempts
            if can_retry:
                done = self.queue.fail(job.id, err, requeue=True)
                log.warning(
                    "job failed id=%s attempt=%s/%s; re-queued: %s",
                    job.id,
                    job.attempt,
                    job.max_attempts,
                    err,
                )
            else:
                done = self.queue.fail(job.id, err, requeue=False)
                self.alert(
                    f"durable worker job {job.id} failed after {job.attempt} "
                    f"attempt(s): {err}"
                )
                log.error("job permanently failed id=%s: %s", job.id, err)
            write_metrics(self.queue, self.metrics_path, last_duration_s=duration)
            return done
