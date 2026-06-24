"""
Phase 0 spike #2: now that we can pass the challenge, learn the real data shapes.

Technique (this is the production-safe pattern): after the browser solves the
Incapsula challenge, we issue the API POST with `fetch()` *from inside the page*
via page.evaluate. The request then uses the browser's own network stack, so
TLS/JA3, cookies, and Client Hints all stay consistent with the solved session.

Goals:
  1. Pull a small page of search results (PageSize=25) with NO filters to learn
     the per-vehicle object schema and the available facet lists.
  2. Locate the Province facet list and find Ontario's id.
  3. Re-query filtered to Ontario and report the Total count.
Outputs go to spike/out/.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
START_URL = "https://ca.iaai.com/Search"
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || { runtime: {} };
"""

# JS run inside the page: POST the search endpoint with form-encoded params and
# return the parsed JSON. `extra` lets us override/add fields per call.
FETCH_JS = """
async (extra) => {
  const base = {
    Url: '', Keyword: '', PageLoading: 'false', PageSize: '25',
    IsKeywordSearch: 'false', IsNewSearch: 'true', RunlistSort: '',
    IgnoreKeywordYear: 'false', YearFrom: '', YearTo: '', VehicleAge: '',
    AuctionToday: 'false', AuctionTomorrow: 'false', VehicleBrand: '',
    StockFilter: '', IsBuyNow: 'false', IsVehicleRemarketing: 'false',
    ShowOnlyPublicAuctions: 'false', VehicleTypeIds: '', VehicleMakeIds: '',
    VehicleModelIds: '', VehicleBrandCodeIds: '', BranchIds: '',
    AuctionTypeIds: '', AuctionIds: '', RequestSource: '', IsManagerPick: 'false',
    IsWatching: 'false', IsPreBidWinning: 'false', IsPreBidOutBid: 'false',
    AuctionMode: '', IsSiteSale: '', IsBuyNowOffer: 'false',
    VehicleTypeListPageIndex: '1', BranchListPageIndex: '1', MakeListPageIndex: '1',
    ModelListPageIndex: '1', VehicleProvinceIds: '', VehicleProvincePageIndex: '1',
    RunlistPageIndex: '1', AttributePageSize: '100',
    Caller: 'dataDataSource_quickSearchUserSelection',
    RunlistPageSize: '25', IsRepossession: 'false', page: '1',
  };
  const params = Object.assign(base, extra || {});
  const body = new URLSearchParams(params).toString();
  const resp = await fetch('/Search/GetSearchResult/', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-Requested-With': 'XMLHttpRequest'},
    body,
    credentials: 'include',
  });
  const text = await resp.text();
  try { return {status: resp.status, json: JSON.parse(text)}; }
  catch (e) { return {status: resp.status, text: text.slice(0, 1000)}; }
}
"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="en-CA", timezone_id="America/Toronto",
            viewport={"width": 1366, "height": 900},
        )
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        # Wait until the real app is present (challenge cleared) by checking size.
        for _ in range(8):
            await page.wait_for_timeout(2500)
            if len(await page.content()) > 100000:
                break

        # 1) Unfiltered small page -> learn schema + facets.
        res = await page.evaluate(FETCH_JS, {"PageSize": "25", "page": "1"})
        (OUT / "search_unfiltered.json").write_text(json.dumps(res, indent=2))
        j = res.get("json", {})
        print("[spike2] unfiltered status", res.get("status"), "Total=", j.get("Total"))
        print("[spike2] top-level keys:", list(j.keys()))

        # Find which keys hold lists, and look for a province facet.
        for k, v in j.items():
            if isinstance(v, list) and v:
                print(f"  list key '{k}' len={len(v)} sample_keys={list(v[0].keys())[:8] if isinstance(v[0],dict) else v[0]}")

        # 2) Locate province facet list + Ontario id.
        prov_key = None
        for k, v in j.items():
            if "Province" in k and isinstance(v, list) and v:
                prov_key = k
                break
        if prov_key:
            print(f"[spike2] province facet key = {prov_key}")
            for item in j[prov_key]:
                desc = item.get("Description")
                print("   province:", item.get("Id"), desc, "count=", item.get("Count"))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
