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
