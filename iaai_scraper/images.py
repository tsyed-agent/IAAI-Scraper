"""On-demand thumbnail cache for the private API.

The crawl stores ``image_url`` pointers only. This module fetches allowlisted
thumbnail URLs once on first API request, writes them under the image cache
directory, and serves subsequent hits from disk. It is never invoked by the
crawler hot path.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import config

log = logging.getLogger("iaai.images")

_STOCK_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DEFAULT_UA = "IAAI-Ontario-API/0.5 (+private thumbnail cache)"

# Per-cache-key locks so N concurrent first viewers of the same lot trigger a
# single upstream fetch. Module-level because the API builds a ThumbnailCache
# per request.
_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = _KEY_LOCKS[key] = threading.Lock()
        return lock


class ImageCacheError(Exception):
    """Base error for thumbnail cache failures."""


class ImageCacheDisabled(ImageCacheError):
    """Raised when ``IAAI_IMAGE_CACHE`` is off."""


class ImageSourceRejected(ImageCacheError):
    """Raised when the source URL fails the allowlist / scheme checks."""


class ImageFetchError(ImageCacheError):
    """Raised when the upstream thumbnail cannot be retrieved."""


@dataclass(frozen=True)
class CachedThumbnail:
    body: bytes
    content_type: str
    from_cache: bool
    source_url: str


FetchFn = Callable[[str, float], tuple[bytes, str]]


def is_allowed_image_url(
    url: str,
    *,
    allowed_hosts: Optional[frozenset[str]] = None,
) -> bool:
    """True when ``url`` is HTTPS and its host is on the allowlist."""
    hosts = allowed_hosts if allowed_hosts is not None else config.IMAGE_ALLOWED_HOSTS
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return host in hosts


class _RefuseRedirects(HTTPRedirectHandler):
    """Refuse all HTTP redirects.

    The allowlist is only checked against the URL we were asked to fetch, so a
    redirect could otherwise silently move the request off-allowlist (SSRF).
    Returning None makes urllib raise the original 3xx as an HTTPError.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = build_opener(_RefuseRedirects())


def _default_fetch(url: str, timeout_s: float) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": _DEFAULT_UA, "Accept": "image/*,*/*;q=0.8"})
    try:
        with _OPENER.open(req, timeout=timeout_s) as resp:  # noqa: S310 - host allowlisted, redirects refused
            content_type = (resp.headers.get("Content-Type") or "application/octet-stream")
            content_type = content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
            # Bound body size while streaming.
            chunks: list[bytes] = []
            total = 0
            limit = config.IMAGE_CACHE_MAX_BYTES
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ImageFetchError(
                        f"thumbnail exceeds max size ({limit} bytes)"
                    )
                chunks.append(chunk)
            body = b"".join(chunks)
    except ImageFetchError:
        raise
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ImageFetchError(
                f"upstream redirect refused (HTTP {exc.code})"
            ) from exc
        raise ImageFetchError(f"upstream HTTP {exc.code}") from exc
    except URLError as exc:
        raise ImageFetchError(f"upstream error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ImageFetchError("upstream timeout") from exc
    except OSError as exc:
        raise ImageFetchError(f"upstream I/O error: {exc}") from exc

    if not body:
        raise ImageFetchError("empty thumbnail body")
    if not content_type.startswith("image/"):
        raise ImageFetchError(f"non-image content-type: {content_type}")
    # Reject obvious HTML error pages mislabeled as images.
    if body[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ImageFetchError("upstream returned HTML instead of an image")
    return body, content_type


class ThumbnailCache:
    """Disk-backed thumbnail cache keyed by stock number + source URL."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        *,
        enabled: Optional[bool] = None,
        allowed_hosts: Optional[frozenset[str]] = None,
        fetch_timeout_s: Optional[float] = None,
        negative_ttl_s: Optional[float] = None,
        fetch: Optional[FetchFn] = None,
    ):
        self.cache_dir = Path(cache_dir or config.IMAGE_CACHE_DIR)
        self.enabled = config.IMAGE_CACHE_ENABLED if enabled is None else enabled
        self.allowed_hosts = (
            allowed_hosts if allowed_hosts is not None else config.IMAGE_ALLOWED_HOSTS
        )
        self.fetch_timeout_s = (
            fetch_timeout_s if fetch_timeout_s is not None else config.IMAGE_FETCH_TIMEOUT_S
        )
        self.negative_ttl_s = (
            negative_ttl_s if negative_ttl_s is not None else config.IMAGE_NEGATIVE_TTL_S
        )
        self._fetch = fetch or _default_fetch

    def get_or_fetch(self, stock_number: str, source_url: str) -> CachedThumbnail:
        if not self.enabled:
            raise ImageCacheDisabled("thumbnail cache is disabled")
        if not stock_number or not _STOCK_SAFE.match(stock_number):
            raise ImageSourceRejected("invalid stock number for cache key")
        if not source_url or not is_allowed_image_url(
            source_url, allowed_hosts=self.allowed_hosts
        ):
            raise ImageSourceRejected("image_url host/scheme is not allowlisted")

        lot_dir = self.cache_dir / stock_number
        meta_path = lot_dir / "meta.json"
        body_path = lot_dir / "thumb.bin"
        fail_path = lot_dir / "fail.json"

        # Single-flight per lot: concurrent cold misses wait for one fetch and
        # then read it from disk instead of each hitting the upstream CDN.
        with _key_lock(f"{self.cache_dir.resolve()}::{stock_number}"):
            cached = self._read_if_valid(meta_path, body_path, source_url)
            if cached is not None:
                return cached

            # A stale copy (older source_url) is better than an error page.
            stale = self._read_stale(meta_path, body_path)
            if self._negative_cached(fail_path, source_url):
                if stale is not None:
                    return stale
                raise ImageFetchError(
                    "upstream fetch failed recently (negative cache); retry later"
                )

            try:
                body, content_type = self._fetch(source_url, self.fetch_timeout_s)
            except ImageFetchError as exc:
                self._write_failure(lot_dir, fail_path, source_url, exc)
                if stale is not None:
                    log.warning(
                        "thumbnail refetch failed for stock=%s (%s); serving stale copy",
                        stock_number, exc,
                    )
                    return stale
                raise
            self._write_atomic(lot_dir, meta_path, body_path, source_url, content_type, body)
            fail_path.unlink(missing_ok=True)
            return CachedThumbnail(
                body=body,
                content_type=content_type,
                from_cache=False,
                source_url=source_url,
            )

    def evict(self, stock_number: str) -> bool:
        """Remove a lot's cached thumbnail (e.g. when the lot is removed)."""
        if not stock_number or not _STOCK_SAFE.match(stock_number):
            return False
        lot_dir = self.cache_dir / stock_number
        if not lot_dir.is_dir():
            return False
        shutil.rmtree(lot_dir, ignore_errors=True)
        return True

    def sweep(
        self,
        *,
        ttl_s: Optional[float] = None,
        evict_stocks: Optional[Iterable[str]] = None,
    ) -> int:
        """Bound cache growth: drop entries for given stocks and/or older than ttl_s.

        Returns the number of evicted cache entries. Safe to call while the API
        serves traffic (eviction is per-lot-directory and lazily repopulated).
        """
        removed = 0
        targets = {s for s in (evict_stocks or ()) if s and _STOCK_SAFE.match(s)}
        cutoff = time.time() - ttl_s if ttl_s and ttl_s > 0 else None
        if not self.cache_dir.is_dir():
            return 0
        for lot_dir in self.cache_dir.iterdir():
            if not lot_dir.is_dir():
                continue
            expired = False
            if cutoff is not None:
                try:
                    expired = lot_dir.stat().st_mtime < cutoff
                except OSError:
                    continue
            if lot_dir.name in targets or expired:
                shutil.rmtree(lot_dir, ignore_errors=True)
                removed += 1
        return removed

    @staticmethod
    def _read_stale(meta_path: Path, body_path: Path) -> Optional[CachedThumbnail]:
        """Read whatever cached copy exists, regardless of source URL."""
        if not meta_path.is_file() or not body_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(meta, dict) or not body:
            return None
        return CachedThumbnail(
            body=body,
            content_type=str(meta.get("content_type") or "application/octet-stream"),
            from_cache=True,
            source_url=str(meta.get("source_url") or ""),
        )

    def _negative_cached(self, fail_path: Path, source_url: str) -> bool:
        """True when a recent failure for this source URL should short-circuit."""
        if self.negative_ttl_s <= 0 or not fail_path.is_file():
            return False
        try:
            marker = json.loads(fail_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(marker, dict) or marker.get("source_url") != source_url:
            return False
        try:
            failed_at = float(marker.get("failed_at", 0))
        except (TypeError, ValueError):
            return False
        return (time.time() - failed_at) < self.negative_ttl_s

    @staticmethod
    def _write_failure(
        lot_dir: Path, fail_path: Path, source_url: str, error: Exception
    ) -> None:
        try:
            lot_dir.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(
                json.dumps(
                    {
                        "source_url": source_url,
                        "error": str(error),
                        "failed_at": time.time(),
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError:  # marker is best-effort; the fetch error still propagates
            log.debug("could not write negative-cache marker %s", fail_path)

    def _read_if_valid(
        self, meta_path: Path, body_path: Path, source_url: str
    ) -> Optional[CachedThumbnail]:
        if not meta_path.is_file() or not body_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(meta, dict) or meta.get("source_url") != source_url:
            return None
        content_type = str(meta.get("content_type") or "application/octet-stream")
        try:
            body = body_path.read_bytes()
        except OSError:
            return None
        if not body:
            return None
        return CachedThumbnail(
            body=body,
            content_type=content_type,
            from_cache=True,
            source_url=source_url,
        )

    @staticmethod
    def _write_atomic(
        lot_dir: Path,
        meta_path: Path,
        body_path: Path,
        source_url: str,
        content_type: str,
        body: bytes,
    ) -> None:
        lot_dir.mkdir(parents=True, exist_ok=True)
        meta = {"source_url": source_url, "content_type": content_type}
        fd, tmp_name = tempfile.mkstemp(prefix=".thumb.", suffix=".tmp", dir=lot_dir)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, body_path)
            meta_fd, meta_tmp = tempfile.mkstemp(
                prefix=".meta.", suffix=".tmp", dir=lot_dir
            )
            try:
                with os.fdopen(meta_fd, "w", encoding="utf-8") as fh:
                    json.dump(meta, fh, separators=(",", ":"))
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(meta_tmp, meta_path)
            finally:
                Path(meta_tmp).unlink(missing_ok=True)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
