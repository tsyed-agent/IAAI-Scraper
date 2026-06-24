"""
Phase 0 spike #6: lock down a RELIABLE crawl method.
  A) Read Sort-By options + confirm a stable sort kills pagination overlap.
  B) Confirm what id-space BranchIds uses (StockBranchId? facet id?) and get a
     branch->id map with names so we can crawl per Ontario branch.
"""
import asyncio, json, re
from collections import Counter
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
START_URL = "https://ca.iaai.com/Search"
STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

# Branch filter-list probe + a search helper that accepts sort + branchIds.
JS = r"""
async (opts) => {
  function form(o){
    const base = {Url:'',Keyword:'',PageLoading:'false',PageSize:String(o.pageSize||100),
      IsKeywordSearch:'false',IsNewSearch:'true',YearFrom:'',YearTo:'',VehicleBrand:'',StockFilter:'',
      IsBuyNow:'false',ShowOnlyPublicAuctions:'false',VehicleTypeIds:'',VehicleMakeIds:'',VehicleModelIds:'',
      BranchIds:String(o.branchIds||''),AuctionTypeIds:'',AuctionIds:'',IsManagerPick:'false',AuctionMode:'',
      IsSiteSale:'',RunlistSort:String(o.sort||''),VehicleTypeListPageIndex:'1',BranchListPageIndex:'1',
      MakeListPageIndex:'1',ModelListPageIndex:'1',VehicleProvinceIds:'',VehicleProvincePageIndex:'1',
      RunlistPageIndex:String(o.page||1),AttributePageSize:'100',Caller:'getVehicleResults',
      RunlistPageSize:String(o.pageSize||100),IsRepossession:'false',page:String(o.page||1)};
    return new URLSearchParams(base).toString();
  }
  async function search(o){
    const r = await fetch('/Search/GetSearchResult/', {method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
      body:form(o), credentials:'include'});
    const j = await r.json();
    const total=(j.SummaryList||[]).find(s=>s.Description==='TOTAL_COUNT');
    return {total: total&&total.Count, rows:(j.RunList||[]).map(v=>({stocknum:v.StockNum,
      sbid:v.StockBranchId, sbdesc:v.StockBranchDescription}))};
  }

  const out = {};

  // A) branch filter list (try a few keys) to map branch ids -> names
  out.branchFilterLists = {};
  for (const k of ['SEARCH_FILTER_BRANCH','BRANCH','SEARCH_FILTER_BRANCHES','SEARCH_FILTER_STOCK_BRANCH']) {
    try {
      const r = await fetch(`/Search/GetSearchFilterList/?key=${k}&selected=false&_=${Date.now()}`,
                            {headers:{'X-Requested-With':'XMLHttpRequest'}, credentials:'include'});
      const j = await r.json();
      out.branchFilterLists[k] = (j.AttributeList||[]).map(x=>({Id:x.Id,Description:x.Description,
        StockBranchId:x.StockBranchId, StockBranchDescription:x.StockBranchDescription, Count:x.Count}));
    } catch(e){ out.branchFilterLists[k] = {error:String(e)}; }
  }

  // B) stability test: fetch page1 & page2 with NO sort, twice; then WITH a sort guess.
  out.stability = {};
  for (const sort of ['', 'AuctionDateAsc', 'StockNumberAsc', 'StockNum', 'lot_number_asc']) {
    const p1 = await search({page:1, pageSize:100, sort});
    const p2 = await search({page:2, pageSize:100, sort});
    const s1 = new Set(p1.rows.map(r=>r.stocknum));
    const overlap = p2.rows.filter(r=>s1.has(r.stocknum)).length;
    out.stability[sort||'(none)'] = {total:p1.total, p1:p1.rows.length, p2:p2.rows.length, overlap};
  }

  // C) confirm BranchIds id-space: try StockBranchId values for ON branches.
  out.branchIdTest = {};
  for (const bid of [10, 52, 56, 70, 71]) {
    const r = await search({page:1, pageSize:100, branchIds:bid});
    const descs = {}; r.rows.forEach(x=>{descs[x.sbdesc]=(descs[x.sbdesc]||0)+1;});
    out.branchIdTest[bid] = {total:r.total, returned:r.rows.length, descs};
  }
  return out;
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
            if len(await page.content()) > 100000: break

        # Read Sort-By <select> options from DOM.
        sorts = await page.evaluate("""() => {
            const out=[];
            document.querySelectorAll('select').forEach(sel=>{
              const id=sel.id||sel.name||'';
              if(/sort/i.test(id)) sel.querySelectorAll('option').forEach(o=>out.push({sel:id,value:o.value,text:o.innerText}));
            });
            return out;
        }""")
        print("=== Sort-By options ===")
        for s in sorts: print("   ", s)

        res = await page.evaluate(JS, {})
        (OUT/"explore6.json").write_text(json.dumps(res, indent=2))

        print("\n=== branch filter lists (counts) ===")
        for k,v in res["branchFilterLists"].items():
            n = len(v) if isinstance(v,list) else v
            print("  ", k, "->", n)
            if isinstance(v,list) and v:
                for it in v[:3]: print("        sample:", it)

        print("\n=== pagination stability (overlap should be 0) ===")
        for k,v in res["stability"].items():
            print("  sort", k, "->", v)

        print("\n=== BranchIds id-space test ===")
        for k,v in res["branchIdTest"].items():
            print("  BranchIds=",k,"->",v)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
