"""Playwright-based session manager that gets us past Imperva/Incapsula.

Strategy (the cost-efficient, reliable pattern validated in Phase 0):
  1. Launch a real Chromium browser and load the Search page. Chromium executes
     Imperva's JavaScript challenge (reese84/utmvc) and is issued the valid
     ``incap_ses_*`` / ``visid_incap_*`` session cookies. A real browser passes
     the canvas/audio/navigator fingerprint checks that plain HTTP clients fail.
  2. Issue all data requests with ``fetch()`` *from inside the page*. Because the
     request originates from the genuine browser, its TLS/JA3 fingerprint,
     cookies, and Client Hints all stay consistent with the solved session —
     which is exactly what Imperva validates on every subsequent request.

Light stealth tweaks hide the most obvious headless tells. For sustained
crawling from a datacenter IP, configure a residential proxy (see config).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from . import config

log = logging.getLogger("iaai.session")

# Injected before any page script runs. Removes the clearest automation signals.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""

# Markers that identify the Incapsula interstitial/block page.
_BLOCK_MARKERS = ("Incapsula incident ID", "_Incapsula_Resource?SWUDNSAI")


class BlockedError(RuntimeError):
    """Raised when the session appears blocked and could not be recovered."""


class IaaiSession:
    """Owns a browser + a page parked on the Search app, ready to run fetches."""

    def __init__(self, settings: Optional[config.CrawlSettings] = None):
        self.settings = settings or config.CrawlSettings()
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def __aenter__(self) -> "IaaiSession":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": config.HEADLESS,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if config.PROXY_SERVER:
            launch_kwargs["proxy"] = {"server": config.PROXY_SERVER}
            safe = re.sub(r"//[^@]+@", "//***@", config.PROXY_SERVER)
            log.info("Using proxy %s", safe)

        self._browser = await self._pw.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            locale=config.LOCALE,
            timezone_id=config.TIMEZONE,
            viewport={"width": 1366, "height": 900},
        )
        await self._context.add_init_script(_STEALTH_JS)
        self._page = await self._context.new_page()
        await self._open_search()

    async def close(self) -> None:
        for closer in (self._browser, self._pw):
            try:
                if closer is self._browser and self._browser:
                    await self._browser.close()
                elif closer is self._pw and self._pw:
                    await self._pw.stop()
            except Exception:  # pragma: no cover - best-effort teardown
                pass

    # ------------------------------------------------------------------ #
    # Challenge handling
    # ------------------------------------------------------------------ #
    async def _open_search(self) -> None:
        """Navigate to the Search page and wait for the Incapsula challenge to clear."""
        assert self._page is not None
        log.info("Loading %s and solving challenge...", config.SEARCH_PAGE_URL)
        await self._page.goto(config.SEARCH_PAGE_URL, wait_until="domcontentloaded",
                              timeout=60000)

        deadline = config.CHALLENGE_TIMEOUT_S
        waited = 0.0
        while waited < deadline:
            await self._page.wait_for_timeout(2500)
            waited += 2.5
            html = await self._page.content()
            # The real app page is ~1 MB; the block/interstitial page is tiny.
            if len(html) > 100_000 and not self._looks_blocked(html):
                log.info("Challenge cleared after %.0fs (page %d bytes)", waited, len(html))
                # Let the app settle so the anti-CSRF token cookie is present.
                await self._page.wait_for_timeout(1500)
                return
            log.debug("Still on challenge page after %.0fs (%d bytes)", waited, len(html))
        raise BlockedError("Incapsula challenge did not clear within timeout")

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        return any(m in html for m in _BLOCK_MARKERS)

    async def _resolve_session(self) -> None:
        """Re-open the Search page to mint a fresh session after a soft block."""
        log.warning("Re-solving session after suspected block")
        await self._open_search()

    # ------------------------------------------------------------------ #
    # In-page requests (the actual data path)
    # ------------------------------------------------------------------ #
    async def post_json(self, path: str, form: dict[str, str]) -> dict[str, Any]:
        """POST form-encoded data via in-page fetch; return parsed JSON.

        Retries with exponential backoff. On a suspected block it re-solves the
        session once per attempt before retrying.
        """
        body_pairs = form
        last_err: Optional[Exception] = None
        for attempt in range(config.MAX_RETRIES):
            try:
                result = await self._page.evaluate(_FETCH_JSON_JS,
                                                   {"path": path, "form": body_pairs,
                                                    "timeoutMs": max(1, int(self.settings.fetch_timeout_s * 1000))})
                if result.get("ok") and isinstance(result.get("json"), (dict, list)):
                    return result["json"]
                # Non-JSON or non-200 => likely a block/interstitial.
                snippet = (result.get("text") or "")[:200]
                log.warning("post_json non-JSON/timeout (status=%s) attempt %d: %s",
                            result.get("status"), attempt, snippet)
                last_err = BlockedError(f"non-JSON response: {snippet}")
                await self._resolve_session()
            except Exception as e:  # network hiccup, navigation, etc.
                last_err = e
                log.warning("post_json error attempt %d: %s", attempt, e)
            await self._backoff(attempt)
        raise BlockedError(f"post_json failed after retries: {last_err}")

    async def get_html(self, url: str) -> str:
        """Navigate to a URL (e.g. a detail page) and return its HTML, with retries."""
        last_err: Optional[Exception] = None
        for attempt in range(config.MAX_RETRIES):
            try:
                resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # Let any client-side rendering settle.
                await self._page.wait_for_timeout(1500)
                html = await self._page.content()
                if resp and resp.status == 200 and not self._looks_blocked(html):
                    return html
                log.warning("get_html status=%s blocked=%s attempt %d",
                            getattr(resp, "status", "?"), self._looks_blocked(html), attempt)
                last_err = BlockedError(f"bad detail response: {getattr(resp, 'status', '?')}")
                await self._resolve_session()
            except Exception as e:
                last_err = e
                log.warning("get_html error attempt %d: %s", attempt, e)
            await self._backoff(attempt)
        raise BlockedError(f"get_html failed after retries: {last_err}")

    @staticmethod
    async def _backoff(attempt: int) -> None:
        delay = config.BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, 1.5)
        log.info("Backoff %.1fs", delay)
        await asyncio.sleep(delay)

    async def polite_delay(self) -> None:
        """Human-like jittered pause between requests (anti behavioural-detection)."""
        await asyncio.sleep(random.uniform(self.settings.delay_min_s, self.settings.delay_max_s))


# JS executed inside the page: POST form-encoded body, return {ok,status,json|text}.
_FETCH_JSON_JS = """
async ({path, form, timeoutMs}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const body = new URLSearchParams(form).toString();
    const resp = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body,
      credentials: 'include',
      signal: controller.signal,
    });
    const text = await resp.text();
    try { return {ok: resp.ok, status: resp.status, json: JSON.parse(text)}; }
    catch (e) { return {ok: false, status: resp.status, text: text.slice(0, 500)}; }
  } catch (e) {
    return {ok: false, status: 0,
      timeout: e && e.name === 'AbortError', text: String(e)};
  } finally {
    clearTimeout(timer);
  }
}
"""
