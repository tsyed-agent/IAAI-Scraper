"""
Phase 0 spike: discover how ca.iaai.com serves search + lot data and confirm we
can pass the Imperva/Incapsula challenge with a real Chromium browser.

This is throwaway exploration code (NOT the production scraper). It:
  1. Launches Chromium with light stealth tweaks.
  2. Loads the Search page and waits for the Incapsula JS challenge to resolve.
  3. Records every JSON/XHR response so we can find the data endpoint(s).
  4. Dumps the captured network log + page HTML to spike/out/ for inspection.

Run: python spike/explore.py
"""
import asyncio
import json
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

# A real, current Chrome UA. Client Hints must stay consistent with this; using a
# stock Chromium build keeps Sec-CH-UA aligned automatically.
START_URL = "https://ca.iaai.com/Search"

# Light stealth: hide the most obvious headless tells before any page script runs.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
"""


def is_block_page(html: str) -> bool:
    return "Incapsula incident ID" in html or "_Incapsula_Resource" in html


async def main():
    captured = []  # list of {url, method, status, ctype, post_data, body_preview}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            locale="en-CA",
            timezone_id="America/Toronto",
            viewport={"width": 1366, "height": 900},
        )
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()

        async def on_response(resp):
            try:
                ctype = resp.headers.get("content-type", "")
                url = resp.url
                # Capture JSON responses and anything that looks like a data API.
                if "json" in ctype or re.search(r"(Search|Vehicle|Filter|Lot|api)", url, re.I):
                    body_preview = ""
                    try:
                        if "json" in ctype:
                            txt = await resp.text()
                            body_preview = txt[:2000]
                    except Exception:
                        pass
                    req = resp.request
                    post = None
                    try:
                        post = req.post_data
                    except Exception:
                        pass
                    captured.append({
                        "url": url,
                        "method": req.method,
                        "status": resp.status,
                        "ctype": ctype,
                        "post_data": post,
                        "req_headers": dict(req.headers),
                        "body_preview": body_preview,
                    })
            except Exception as e:
                print("capture err", e)

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        print(f"[spike] navigating to {START_URL}")
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)

        # Give Incapsula's JS challenge time to run + the page to hydrate.
        for attempt in range(6):
            await page.wait_for_timeout(3000)
            html = await page.content()
            if not is_block_page(html):
                print(f"[spike] challenge cleared on attempt {attempt}")
                break
            print(f"[spike] still on challenge page, attempt {attempt}")
        else:
            print("[spike] WARNING: still appears blocked")

        # Let any lazy search requests fire.
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        html = await page.content()
        (OUT / "search_page.html").write_text(html)
        print(f"[spike] saved search_page.html ({len(html)} bytes), blocked={is_block_page(html)}")

        # Dump cookies so we can see the Incapsula/ASP.NET session set.
        cookies = await context.cookies()
        (OUT / "cookies.json").write_text(json.dumps(cookies, indent=2))
        print(f"[spike] cookies: {[c['name'] for c in cookies]}")

        (OUT / "network.json").write_text(json.dumps(captured, indent=2))
        print(f"[spike] captured {len(captured)} json/api responses")
        for c in captured:
            print(f"  - {c['method']} {c['status']} {c['url'][:120]}")

        await browser.close()


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print(f"[spike] done in {time.time()-t0:.1f}s")
