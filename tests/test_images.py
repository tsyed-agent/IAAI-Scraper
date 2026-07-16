"""Unit tests for on-demand thumbnail cache (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from iaai_scraper.images import (
    ImageCacheDisabled,
    ImageFetchError,
    ImageSourceRejected,
    ThumbnailCache,
    is_allowed_image_url,
)


def test_allowlist_rejects_non_https_and_unknown_hosts():
    assert is_allowed_image_url(
        "https://anvis.iaai.com/thumbnail?imageKeys=1",
        allowed_hosts=frozenset({"anvis.iaai.com"}),
    )
    assert not is_allowed_image_url(
        "http://anvis.iaai.com/thumbnail?imageKeys=1",
        allowed_hosts=frozenset({"anvis.iaai.com"}),
    )
    assert not is_allowed_image_url(
        "https://evil.example/x",
        allowed_hosts=frozenset({"anvis.iaai.com"}),
    )
    assert not is_allowed_image_url(
        "https://user:pass@anvis.iaai.com/x",
        allowed_hosts=frozenset({"anvis.iaai.com"}),
    )


def test_cache_miss_then_hit(tmp_path: Path):
    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: float) -> tuple[bytes, str]:
        calls.append(url)
        return b"\xff\xd8\xfffakejpeg", "image/jpeg"

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=fake_fetch,
    )
    url = "https://anvis.iaai.com/thumbnail?imageKeys=abc"
    first = cache.get_or_fetch("100", url)
    second = cache.get_or_fetch("100", url)

    assert first.from_cache is False
    assert second.from_cache is True
    assert first.body == second.body == b"\xff\xd8\xfffakejpeg"
    assert first.content_type == "image/jpeg"
    assert calls == [url]
    meta = json.loads((tmp_path / "100" / "meta.json").read_text(encoding="utf-8"))
    assert meta["source_url"] == url


def test_source_url_change_refetches(tmp_path: Path):
    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: float) -> tuple[bytes, str]:
        calls.append(url)
        return url.encode(), "image/png"

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=fake_fetch,
    )
    u1 = "https://anvis.iaai.com/thumbnail?imageKeys=1"
    u2 = "https://anvis.iaai.com/thumbnail?imageKeys=2"
    cache.get_or_fetch("100", u1)
    again = cache.get_or_fetch("100", u2)
    assert again.from_cache is False
    assert again.body == u2.encode()
    assert calls == [u1, u2]


def test_disabled_and_rejected_sources(tmp_path: Path):
    cache = ThumbnailCache(cache_dir=tmp_path, enabled=False, fetch=lambda u, t: (b"x", "image/jpeg"))
    with pytest.raises(ImageCacheDisabled):
        cache.get_or_fetch("100", "https://anvis.iaai.com/x")

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=lambda u, t: (b"x", "image/jpeg"),
    )
    with pytest.raises(ImageSourceRejected):
        cache.get_or_fetch("100", "https://evil.example/x")
    with pytest.raises(ImageSourceRejected):
        cache.get_or_fetch("../etc", "https://anvis.iaai.com/x")


def test_fetch_errors_propagate(tmp_path: Path):
    def boom(url: str, timeout_s: float) -> tuple[bytes, str]:
        raise ImageFetchError("upstream HTTP 500")

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=boom,
    )
    with pytest.raises(ImageFetchError):
        cache.get_or_fetch("100", "https://anvis.iaai.com/thumbnail?x=1")


def test_default_fetch_refuses_redirects():
    """A 3xx from an allowlisted host must not be followed off-allowlist."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from iaai_scraper.images import _default_fetch

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://evil.example/steal")
            self.end_headers()

        def log_message(self, *args):  # keep test output quiet
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ImageFetchError, match="redirect refused"):
            _default_fetch(f"http://127.0.0.1:{server.server_port}/thumb", 5.0)
    finally:
        server.shutdown()


def test_concurrent_misses_fetch_upstream_once(tmp_path: Path):
    import threading
    import time as _time

    calls: list[str] = []
    release = threading.Event()

    def slow_fetch(url: str, timeout_s: float) -> tuple[bytes, str]:
        calls.append(url)
        release.wait(2)
        return b"\xff\xd8\xffonce", "image/jpeg"

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=slow_fetch,
    )
    url = "https://anvis.iaai.com/thumbnail?imageKeys=sf"
    results: list = []
    workers = [
        threading.Thread(target=lambda: results.append(cache.get_or_fetch("100", url)))
        for _ in range(2)
    ]
    for w in workers:
        w.start()
    _time.sleep(0.1)
    release.set()
    for w in workers:
        w.join(timeout=5)
    assert len(calls) == 1
    assert len(results) == 2
    assert all(r.body == b"\xff\xd8\xffonce" for r in results)


def test_negative_cache_short_circuits_refetch(tmp_path: Path):
    calls: list[str] = []

    def boom(url: str, timeout_s: float) -> tuple[bytes, str]:
        calls.append(url)
        raise ImageFetchError("upstream HTTP 500")

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        negative_ttl_s=60,
        fetch=boom,
    )
    url = "https://anvis.iaai.com/thumbnail?imageKeys=nc"
    with pytest.raises(ImageFetchError):
        cache.get_or_fetch("100", url)
    with pytest.raises(ImageFetchError, match="negative cache"):
        cache.get_or_fetch("100", url)
    assert len(calls) == 1


def test_stale_copy_served_when_refetch_fails(tmp_path: Path):
    hosts = frozenset({"anvis.iaai.com"})
    good = ThumbnailCache(
        cache_dir=tmp_path, enabled=True, allowed_hosts=hosts,
        fetch=lambda u, t: (b"old-bytes", "image/jpeg"),
    )
    u1 = "https://anvis.iaai.com/thumbnail?imageKeys=1"
    u2 = "https://anvis.iaai.com/thumbnail?imageKeys=2"
    good.get_or_fetch("100", u1)

    def boom(url: str, timeout_s: float) -> tuple[bytes, str]:
        raise ImageFetchError("upstream HTTP 502")

    failing = ThumbnailCache(
        cache_dir=tmp_path, enabled=True, allowed_hosts=hosts,
        negative_ttl_s=60, fetch=boom,
    )
    first = failing.get_or_fetch("100", u2)
    assert first.from_cache is True
    assert first.body == b"old-bytes"
    # Second request hits the negative cache but still serves the stale copy.
    second = failing.get_or_fetch("100", u2)
    assert second.body == b"old-bytes"


def test_evict_and_sweep(tmp_path: Path):
    import os
    import time as _time

    cache = ThumbnailCache(
        cache_dir=tmp_path,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=lambda u, t: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    for stock in ("100", "200", "300"):
        cache.get_or_fetch(stock, f"https://anvis.iaai.com/thumbnail?imageKeys={stock}")

    assert cache.evict("100") is True
    assert not (tmp_path / "100").exists()
    assert cache.evict("100") is False  # already gone
    assert cache.evict("../etc") is False  # unsafe names never touch disk

    assert cache.sweep(evict_stocks=["200"]) == 1
    assert not (tmp_path / "200").exists()

    old = _time.time() - 10_000
    os.utime(tmp_path / "300", (old, old))
    assert cache.sweep(ttl_s=100) == 1
    assert not (tmp_path / "300").exists()
