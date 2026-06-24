"""
Phase 0 spike #3: find how to filter the search to Ontario at the source.

Tries the GetSearchFilterList endpoint with candidate province keys to discover
the province option ids, then runs a search filtered by Ontario and reports the
Total count (from SummaryList TOTAL_COUNT).
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
START_URL = "https://ca.iaai.com/Search"
STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

PROBE_JS = """
async () => {
  const keys = ['SEARCH_FILTER_PROVINCE','SEARCH_FILTER_PROVINCES','PROVINCE',
                'SEARCH_FILTER_VEHICLE_PROVINCE','SEARCH_FILTER_STATE'];
  const out = {};
  for (const k of keys) {
    try {
      const r = await fetch(`/Search/GetSearchFilterList/?key=${k}&selected=false&_=${Date.now()}`,
                            {headers:{'X-Requested-With':'XMLHttpRequest'}, credentials:'include'});
      const t = await r.text();
      let j; try { j = JSON.parse(t); } catch(e){ j = {parseError:true, raw:t.slice(0,200)}; }
      const list = j.AttributeList || [];
      out[k] = {status:r.status, count:list.length,
                items:list.map(x=>({Id:x.Id, Description:x.Description, ValueType:x.ValueType, Count:x.Count}))};
    } catch(e) { out[k] = {error:String(e)}; }
  }
  return out;
}
"""

SEARCH_JS = """
async (provinceIds) => {
  const base = {Url:'',Keyword:'',PageLoading:'false',PageSize:'25',IsKeywordSearch:'false',
    IsNewSearch:'true',YearFrom:'',YearTo:'',VehicleBrand:'',StockFilter:'',IsBuyNow:'false',
    ShowOnlyPublicAuctions:'false',VehicleTypeIds:'',VehicleMakeIds:'',VehicleModelIds:'',
    BranchIds:'',AuctionTypeIds:'',AuctionIds:'',IsManagerPick:'false',AuctionMode:'',IsSiteSale:'',
    VehicleTypeListPageIndex:'1',BranchListPageIndex:'1',MakeListPageIndex:'1',ModelListPageIndex:'1',
    VehicleProvinceIds:provinceIds,VehicleProvincePageIndex:'1',RunlistPageIndex:'1',
    AttributePageSize:'100',Caller:'dataDataSource_quickSearchUserSelection',RunlistPageSize:'25',
    IsRepossession:'false',page:'1'};
  const body = new URLSearchParams(base).toString();
  const r = await fetch('/Search/GetSearchResult/', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
    body, credentials:'include'});
  const j = await r.json();
  const total = (j.SummaryList||[]).find(s=>s.Description==='TOTAL_COUNT');
  const locs = (j.RunList||[]).map(v=>v.VehicleLocation);
  return {status:r.status, total: total && total.Count, sampleLocations: locs.slice(0,10),
          provinceFacet:(j.VehicleProvinceList||[]).map(p=>({Id:p.Id,Description:p.Description,Count:p.Count}))};
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

        probe = await page.evaluate(PROBE_JS)
        (OUT / "province_probe.json").write_text(json.dumps(probe, indent=2))
        print("=== province filter-key probe ===")
        for k, v in probe.items():
            print(k, "->", v.get("status"), "count=", v.get("count"))
            for it in (v.get("items") or [])[:20]:
                print("    ", it)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
