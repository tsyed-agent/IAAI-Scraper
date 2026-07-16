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
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import config

log = logging.getLogger("iaai.images")

_STOCK_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DEFAULT_UA = "IAAI-Ontario-API/0.5 (+private thumbnail cache)"


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


def _default_fetch(url: str, timeout_s: float) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": _DEFAULT_UA, "Accept": "image/*,*/*;q=0.8"})
    try:
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - host allowlisted
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

        cached = self._read_if_valid(meta_path, body_path, source_url)
        if cached is not None:
            return cached

        body, content_type = self._fetch(source_url, self.fetch_timeout_s)
        self._write_atomic(lot_dir, meta_path, body_path, source_url, content_type, body)
        return CachedThumbnail(
            body=body,
            content_type=content_type,
            from_cache=False,
            source_url=source_url,
        )

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
