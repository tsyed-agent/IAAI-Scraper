"""Offline tests for post-crawl thumbnail warmer."""
from __future__ import annotations

from pathlib import Path

from iaai_scraper.images import ThumbnailCache
from iaai_scraper.parser import parse_row
from iaai_scraper.storage import SqliteStore
from iaai_scraper.thumb_warmer import warm_active_thumbs
from tests.conftest import make_row


def test_warm_active_thumbs_fetches_and_caches(tmp_path: Path):
    db = tmp_path / "lots.db"
    cache_dir = tmp_path / "thumbs"
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row(
        "100",
        ImageUrl="https://anvis.iaai.com/thumbnail?imageKeys=100",
        ItemStatusDesc="",
    )))
    store.upsert_lot(parse_row(make_row(
        "200",
        ImageUrl="https://anvis.iaai.com/thumbnail?imageKeys=200",
        ItemStatusDesc="Sold",
        HighPrebidValue=100,
    )))
    store.upsert_lot(parse_row(make_row(
        "300",
        # no image
        ItemStatusDesc="",
    )))
    store.commit()
    # clear image for 300
    store.conn.execute("UPDATE lots SET image_url = NULL WHERE stock_number = '300'")
    store.commit()
    store.close()

    fetches: list[str] = []

    def fake_fetch(url: str, timeout_s: float):
        fetches.append(url)
        return b"\xff\xd8\xffx", "image/jpeg"

    cache = ThumbnailCache(
        cache_dir=cache_dir,
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=fake_fetch,
    )
    report = warm_active_thumbs(limit=50, db_path=db, cache=cache, workers=2)
    assert report.requested == 1  # only active with image_url
    assert report.warmed == 1
    assert report.failed == 0
    assert len(fetches) == 1

    # Second warm is cache hit (no new fetch)
    report2 = warm_active_thumbs(limit=50, db_path=db, cache=cache, workers=2)
    assert report2.cache_hits == 1
    assert len(fetches) == 1


def test_warm_respects_limit(tmp_path: Path):
    db = tmp_path / "lots.db"
    store = SqliteStore(db_path=db)
    for i in range(5):
        store.upsert_lot(parse_row(make_row(
            str(1000 + i),
            ImageUrl=f"https://anvis.iaai.com/thumbnail?imageKeys={1000 + i}",
            ItemStatusDesc="",
        )))
    store.commit()
    store.close()
    fetches: list[str] = []

    def fake_fetch(url: str, timeout_s: float):
        fetches.append(url)
        return b"\xff\xd8\xff", "image/jpeg"

    cache = ThumbnailCache(
        cache_dir=tmp_path / "c",
        enabled=True,
        allowed_hosts=frozenset({"anvis.iaai.com"}),
        fetch=fake_fetch,
    )
    report = warm_active_thumbs(limit=2, db_path=db, cache=cache, workers=2)
    assert report.requested == 2
    assert len(fetches) == 2
