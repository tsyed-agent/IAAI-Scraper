"""
Phase 0 spike #5: confirm the crawl strategy.
  - Does PageSize=100 work? How many pages for all of Canada?
  - Province distribution from VehicleLocation suffix.
  - Ontario branch ids (StockBranchId) from ON lots.
  - Does source-side BranchIds filtering work (efficiency option)?
"""
import asyncio
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
START_URL = "https://ca.iaai.com/Search"
STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

SEARCH_JS = """
async (opts) => {
  const base = {Url:'',Keyword:'',PageLoading:'false',PageSize:String(opts.pageSize||100),
    IsKeywordSearch:'false',IsNewSearch: opts.page>1?'false':'true',YearFrom:'',YearTo:'',
    VehicleBrand:'',StockFilter:'',IsBuyNow:'false',ShowOnlyPublicAuctions:'false',
    VehicleTypeIds:'',VehicleMakeIds:'',VehicleModelIds:'',BranchIds:String(opts.branchIds||''),
    AuctionTypeIds:'',AuctionIds:'',IsManagerPick:'false',AuctionMode:'',IsSiteSale:'',
    VehicleTypeListPageIndex:'1',BranchListPageIndex:'1',MakeListPageIndex:'1',ModelListPageIndex:'1',
    VehicleProvinceIds:'',VehicleProvincePageIndex:'1',RunlistPageIndex:String(opts.page||1),
    AttributePageSize:'100',Caller:'getVehicleResults',RunlistPageSize:String(opts.pageSize||100),
    IsRepossession:'false',page:String(opts.page||1)};
  const body = new URLSearchParams(base).toString();
  const r = await fetch('/Search/GetSearchResult/', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
    body, credentials:'include'});
  const j = await r.json();
  const total = (j.SummaryList||[]).find(s=>s.Description==='TOTAL_COUNT');
  return {status:r.status, total: total && total.Count, returned:(j.RunList||[]).length,
    rows:(j.RunList||[]).map(v=>({loc:v.VehicleLocation, sbid:v.StockBranchId,
      sbdesc:v.StockBranchDescription, stocknum:v.StockNum}))};
}
"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True,
            args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-dev-shm-usage"])
        context = await browser.new_context(locale="en-CA", timezone_id="America/Toronto",
            viewport={"width":1366,"height":900})
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        for _ in range(8):
            await page.wait_for_timeout(2500)
            if len(await page.content()) > 100000:
                break

        # First page with PageSize=100.
        r1 = await page.evaluate(SEARCH_JS, {"pageSize":100, "page":1})
        print("[spike5] PageSize=100 -> status", r1["status"], "total", r1["total"], "returned", r1["returned"])

        # Crawl a few pages to sample province distribution + ON branch ids.
        prov = Counter()
        on_branches = defaultdict(set)
        seen_stock = set()
        dupes = 0
        pages_to_sample = 6
        for pg in range(1, pages_to_sample+1):
            r = await page.evaluate(SEARCH_JS, {"pageSize":100, "page":pg})
            for row in r["rows"]:
                loc = row["loc"] or ""
                m = re.search(r",\s*([A-Z]{2})\s*$", loc)
                province = m.group(1) if m else "??"
                prov[province] += 1
                if province == "ON":
                    on_branches[row["sbid"]].add(row["sbdesc"])
                if row["stocknum"] in seen_stock:
                    dupes += 1
                seen_stock.add(row["stocknum"])
            print(f"  page {pg}: returned={r['returned']}")
            await page.wait_for_timeout(800)

        print("[spike5] province distribution (6 pages):", dict(prov))
        print("[spike5] duplicate stocknums across pages:", dupes)
        print("[spike5] ON StockBranchId -> descriptions:")
        for sbid, descs in on_branches.items():
            print("    ", sbid, descs)

        # Test source-side branch filter using one ON branch id (if found).
        if on_branches:
            test_bid = next(iter(on_branches))
            rb = await page.evaluate(SEARCH_JS, {"pageSize":100, "page":1, "branchIds":test_bid})
            locs = Counter(re.search(r",\s*([A-Z]{2})\s*$", (x["loc"] or "")).group(1)
                           if re.search(r",\s*([A-Z]{2})\s*$", (x["loc"] or "")) else "??"
                           for x in rb["rows"])
            print(f"[spike5] BranchIds={test_bid} -> total={rb['total']} returned={rb['returned']} provinces={dict(locs)}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
