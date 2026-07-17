"""Off-host backup upload + restore drill (Phase 2 · 2.2).

Local ``cli backup`` still writes atomic SQLite snapshots under
``data/backups/``. This module:

1. Takes the latest DB snapshot (+ newest raw ``*.jsonl.gz`` when present)
2. Uploads them to a versioned object store (filesystem or S3-compatible)
3. Writes a SHA-256 manifest
4. Prunes local copies (keep N DB backups; raw hot window in days)
5. Runs a restore drill: download → ``PRAGMA integrity_check`` → lot-count floor

CI uses the filesystem backend — no cloud credentials required.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from . import config
from .storage import SqliteStore, backup_sqlite

log = logging.getLogger("iaai.offsite_backup")

DEFAULT_KEEP_LOCAL_DB = 5
DEFAULT_RAW_HOT_DAYS = 90
DEFAULT_PREFIX = "iaai-ontario"


# --------------------------------------------------------------------------- #
# Object store backends
# --------------------------------------------------------------------------- #


class ObjectStore(Protocol):
    def put(self, key: str, source: Path) -> None: ...
    def get(self, key: str, destination: Path) -> Path: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...
    def exists(self, key: str) -> bool: ...


class FilesystemObjectStore:
    """Local directory that mimics versioned object storage (CI + lab)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        key = key.lstrip("/")
        if ".." in Path(key).parts:
            raise ValueError(f"unsafe object key: {key!r}")
        return self.root / key

    def put(self, key: str, source: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copy2(source, tmp)
        os.replace(tmp, dest)

    def get(self, key: str, destination: Path) -> Path:
        source = self._path(key)
        if not source.is_file():
            raise FileNotFoundError(f"object not found: {key}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def list_keys(self, prefix: str = "") -> list[str]:
        prefix = prefix.lstrip("/")
        root = self.root
        if not root.exists():
            return []
        keys: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if prefix and not rel.startswith(prefix):
                continue
            keys.append(rel)
        return sorted(keys)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ObjectStore:
    """S3-compatible object store via boto3 (optional runtime dependency)."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
    ):
        try:
            import boto3
            from botocore.client import Config as BotoConfig
        except ImportError as exc:  # pragma: no cover - exercised when boto3 missing
            raise RuntimeError(
                "boto3 is required for S3 backups; pip install boto3 "
                "or set IAAI_BACKUP_BACKEND=filesystem"
            ) from exc

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region_name or None,
            config=BotoConfig(signature_version="s3v4"),
        )

    def _key(self, key: str) -> str:
        key = key.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{key}"
        return key

    def put(self, key: str, source: Path) -> None:
        self._client.upload_file(str(source), self.bucket, self._key(key))

    def get(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, self._key(key), str(destination))
        return destination

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = self._key(prefix)
        keys: list[str] = []
        token: Optional[str] = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": full_prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kwargs)
            for item in resp.get("Contents") or []:
                raw = item["Key"]
                if self.prefix and raw.startswith(self.prefix + "/"):
                    raw = raw[len(self.prefix) + 1 :]
                keys.append(raw)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception:
            return False


def object_store_from_env(
    *,
    backend: Optional[str] = None,
    filesystem_root: Optional[Path] = None,
) -> ObjectStore:
    """Build an object store from env / explicit overrides."""
    kind = (backend or os.getenv("IAAI_BACKUP_BACKEND", "filesystem")).strip().lower()
    if kind in ("filesystem", "file", "fs", "local"):
        root = filesystem_root or Path(
            os.getenv(
                "IAAI_BACKUP_DIR",
                str(config.DATA_DIR / "offsite"),
            )
        )
        return FilesystemObjectStore(root)
    if kind in ("s3", "minio", "r2", "b2"):
        bucket = os.getenv("IAAI_BACKUP_S3_BUCKET", "").strip()
        if not bucket:
            raise ValueError("IAAI_BACKUP_S3_BUCKET is required for S3 backend")
        return S3ObjectStore(
            bucket,
            prefix=os.getenv("IAAI_BACKUP_S3_PREFIX", DEFAULT_PREFIX),
            endpoint_url=os.getenv("IAAI_BACKUP_S3_ENDPOINT") or None,
            region_name=os.getenv("IAAI_BACKUP_S3_REGION") or None,
        )
    raise ValueError(f"unknown IAAI_BACKUP_BACKEND={kind!r} (use filesystem|s3)")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backups_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "backups"


def raw_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "raw"


def latest_db_backup(directory: Path) -> Optional[Path]:
    if not directory.is_dir():
        return None
    candidates = sorted(
        (p for p in directory.glob("*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def newest_raw_snapshot(directory: Path) -> Optional[Path]:
    """Newest ``*.jsonl.gz`` under ``raw/`` (including dated subdirs)."""
    if not directory.is_dir():
        return None
    candidates = sorted(
        (p for p in directory.rglob("*.jsonl.gz") if p.is_file() and ".dlq." not in p.name),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def prune_local_db_backups(directory: Path, keep: int = DEFAULT_KEEP_LOCAL_DB) -> list[Path]:
    """Keep the newest ``keep`` ``*.db`` files; delete older ones."""
    if keep < 0:
        raise ValueError("keep must be nonnegative")
    if not directory.is_dir():
        return []
    candidates = sorted(
        (p for p in directory.glob("*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for path in candidates[keep:]:
        path.unlink(missing_ok=True)
        removed.append(path)
        log.info("pruned local DB backup: %s", path)
    return removed


def prune_local_raw(directory: Path, hot_days: int = DEFAULT_RAW_HOT_DAYS) -> list[Path]:
    """Delete local raw ``*.jsonl.gz`` older than the hot window."""
    if hot_days < 0:
        raise ValueError("hot_days must be nonnegative")
    if not directory.is_dir():
        return []
    cutoff = time.time() - (hot_days * 86400)
    removed: list[Path] = []
    for path in directory.rglob("*.jsonl.gz"):
        if not path.is_file() or ".dlq." in path.name:
            continue
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path)
            log.info("pruned local raw snapshot: %s", path)
    return removed


@dataclass(frozen=True)
class Artifact:
    kind: str
    local_path: Path
    object_key: str
    sha256: str
    size: int
    name: str


def _artifact(kind: str, path: Path, object_key: str) -> Artifact:
    return Artifact(
        kind=kind,
        local_path=path,
        object_key=object_key,
        sha256=sha256_file(path),
        size=path.stat().st_size,
        name=path.name,
    )


# --------------------------------------------------------------------------- #
# Offsite upload
# --------------------------------------------------------------------------- #


def _safe_artifact_name(name: str) -> str:
    """Reject path traversal / absolute names from manifests."""
    if not name or name != Path(name).name:
        raise ValueError(f"unsafe artifact name: {name!r}")
    if name in (".", ".."):
        raise ValueError(f"unsafe artifact name: {name!r}")
    return name


def _safe_object_key(key: str, *, allowed_prefix: str) -> str:
    key = key.lstrip("/")
    if ".." in Path(key).parts:
        raise ValueError(f"unsafe object key: {key!r}")
    if not key.startswith(allowed_prefix.rstrip("/") + "/"):
        raise ValueError(f"object key outside snapshot prefix: {key!r}")
    return key


def run_offsite_backup(
    data_dir: Path,
    store: ObjectStore,
    *,
    db_path: Optional[Path] = None,
    create_fresh_snapshot: bool = True,
    keep_local_db: int = DEFAULT_KEEP_LOCAL_DB,
    raw_hot_days: int = DEFAULT_RAW_HOT_DAYS,
    snapshot_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create/upload a versioned snapshot with SHA-256 manifest."""
    data_dir = Path(data_dir)
    db_path = Path(db_path) if db_path else data_dir / "iaai_ontario.db"
    bdir = backups_dir(data_dir)
    rdir = raw_dir(data_dir)
    bdir.mkdir(parents=True, exist_ok=True)

    db_backup: Optional[Path] = None
    if create_fresh_snapshot:
        if not db_path.is_file():
            raise FileNotFoundError(f"SQLite database not found: {db_path}")
        stamp = snapshot_id or _utc_stamp()
        dest = bdir / f"iaai-ontario-{stamp}.db"
        db_backup = backup_sqlite(db_path, dest)
        snapshot_id = stamp
    else:
        snapshot_id = snapshot_id or _utc_stamp()
        db_backup = latest_db_backup(bdir)

    if db_backup is None:
        raise FileNotFoundError(f"no local DB backups under {bdir}")

    raw_file = newest_raw_snapshot(rdir)
    base = f"snapshots/{snapshot_id}"
    artifacts: list[Artifact] = [
        _artifact("db", db_backup, f"{base}/iaai-ontario.db"),
    ]
    if raw_file is not None:
        safe_raw_name = _safe_artifact_name(raw_file.name)
        artifacts.append(_artifact("raw", raw_file, f"{base}/{safe_raw_name}"))

    for art in artifacts:
        store.put(art.object_key, art.local_path)
        log.info("uploaded %s → %s (%s)", art.name, art.object_key, art.sha256[:12])

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "snapshot_id": snapshot_id,
        "artifacts": [
            {
                "kind": a.kind,
                "name": a.name,
                "object_key": a.object_key,
                "sha256": a.sha256,
                "size": a.size,
            }
            for a in artifacts
        ],
    }
    manifest_path = bdir / f"manifest-{snapshot_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_key = f"{base}/manifest.json"
    store.put(manifest_key, manifest_path)

    latest = {"snapshot_id": snapshot_id, "manifest_key": manifest_key}
    latest_path = bdir / "latest-offsite.json"
    latest_path.write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    store.put("latest.json", latest_path)

    pruned_db = prune_local_db_backups(bdir, keep=keep_local_db)
    pruned_raw = prune_local_raw(rdir, hot_days=raw_hot_days)

    return {
        "snapshot_id": snapshot_id,
        "manifest_key": manifest_key,
        "artifacts": [
            {**{k: v for k, v in asdict(a).items() if k != "local_path"},
             "local_path": str(a.local_path)}
            for a in artifacts
        ],
        "pruned_db": [str(p) for p in pruned_db],
        "pruned_raw": [str(p) for p in pruned_raw],
    }


# --------------------------------------------------------------------------- #
# Restore drill
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RestoreDrillResult:
    ok: bool
    snapshot_id: str
    db_path: Path
    integrity_check: str
    lot_count: int
    min_lots: int
    verified_sha256: dict[str, bool]
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "snapshot_id": self.snapshot_id,
            "db_path": str(self.db_path),
            "integrity_check": self.integrity_check,
            "lot_count": self.lot_count,
            "min_lots": self.min_lots,
            "verified_sha256": self.verified_sha256,
            "detail": self.detail,
        }


def _resolve_snapshot_id(store: ObjectStore, snapshot_id: Optional[str]) -> str:
    if snapshot_id:
        return snapshot_id
    if not store.exists("latest.json"):
        raise FileNotFoundError("no latest.json in object store; run offsite backup first")

    with tempfile.TemporaryDirectory(prefix="iaai-latest-") as tmp:
        path = Path(tmp) / "latest.json"
        store.get("latest.json", path)
        data = json.loads(path.read_text(encoding="utf-8"))
    sid = data.get("snapshot_id")
    if not sid:
        raise ValueError("latest.json missing snapshot_id")
    return str(sid)


def run_restore_drill(
    store: ObjectStore,
    restore_dir: Path,
    *,
    min_lots: int = 1,
    snapshot_id: Optional[str] = None,
) -> RestoreDrillResult:
    """Download a snapshot, verify checksums + SQLite integrity + lot floor."""
    if min_lots < 0:
        raise ValueError("min_lots must be nonnegative")

    restore_dir = Path(restore_dir)
    restore_dir.mkdir(parents=True, exist_ok=True)
    sid = _resolve_snapshot_id(store, snapshot_id)
    base = f"snapshots/{sid}"

    manifest_path = restore_dir / "manifest.json"
    store.get(f"{base}/manifest.json", manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts") or []
    if not artifacts:
        raise ValueError(f"manifest for {sid} has no artifacts")

    verified: dict[str, bool] = {}
    db_local: Optional[Path] = None
    for art in artifacts:
        name = _safe_artifact_name(str(art["name"]))
        key = _safe_object_key(str(art["object_key"]), allowed_prefix=base)
        expected = art["sha256"]
        dest = restore_dir / name
        # Ensure resolved path stays under restore_dir even on exotic platforms.
        if not dest.resolve().is_relative_to(restore_dir.resolve()):
            raise ValueError(f"artifact path escapes restore dir: {name!r}")
        store.get(key, dest)
        actual = sha256_file(dest)
        verified[name] = actual == expected
        if not verified[name]:
            return RestoreDrillResult(
                ok=False,
                snapshot_id=sid,
                db_path=dest,
                integrity_check="skipped",
                lot_count=-1,
                min_lots=min_lots,
                verified_sha256=verified,
                detail=f"sha256 mismatch for {name}",
            )
        if art.get("kind") == "db" or name.endswith(".db"):
            db_local = dest

    if db_local is None:
        raise FileNotFoundError(f"no db artifact in snapshot {sid}")

    conn = sqlite3.connect(db_local)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    if integrity != "ok":
        return RestoreDrillResult(
            ok=False,
            snapshot_id=sid,
            db_path=db_local,
            integrity_check=str(integrity),
            lot_count=-1,
            min_lots=min_lots,
            verified_sha256=verified,
            detail="integrity_check failed",
        )

    store_db = SqliteStore(db_path=db_local)
    try:
        lot_count = int(store_db.stats()["total_lots"])
    finally:
        store_db.close()

    ok = lot_count >= min_lots
    detail = "" if ok else f"lot_count {lot_count} < min_lots {min_lots}"
    return RestoreDrillResult(
        ok=ok,
        snapshot_id=sid,
        db_path=db_local,
        integrity_check=integrity,
        lot_count=lot_count,
        min_lots=min_lots,
        verified_sha256=verified,
        detail=detail,
    )
