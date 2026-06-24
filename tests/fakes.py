"""Test doubles that let the crawler run without Playwright or network."""
from __future__ import annotations


class FakeSession:
    """Stands in for IaaiSession as an async context manager."""

    def __init__(self, settings=None):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def polite_delay(self):
        return None


class FakeSearchClient:
    """Returns pre-baked pages. pages = list of (rows, total)."""

    def __init__(self, pages):
        self._pages = pages

    async def fetch_page(self, page: int):
        idx = page - 1
        if idx < 0 or idx >= len(self._pages):
            return [], (self._pages[0][1] if self._pages else None)
        return self._pages[idx]
