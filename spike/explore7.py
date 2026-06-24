"""
Phase 0 spike #7: inspect a lot DETAIL page to see if it adds structured fields
beyond the list row (and whether VIN is fuller). Captures the detail page's data
XHRs and any embedded JSON.
"""
import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True,
            args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-dev-shm-usage"])
        context = await browser.new_context(locale="en-CA", timezone_id="America/Toronto",
            viewport={"width":1366,"height":900})
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()

        # First, get one real Ontario lot's StockNum via the search API.
        await page.goto("https://ca.iaai.com/Search", wait_until="domcontentloaded", timeout=60000)
        for _ in range(8):
            await page.wait_for_timeout(2500)
            if len(await page.content()) > 100000: break
        row = await page.evaluate(r"""async () => {
          const base={Url:'',Keyword:'',PageLoading:'false',PageSize:'25',IsKeywordSearch:'false',
            IsNewSearch:'true',RunlistSort:'STOCK ASC',VehicleProvinceIds:'',BranchIds:'',
            RunlistPageIndex:'1',RunlistPageSize:'25',AttributePageSize:'100',Caller:'getVehicleResults',page:'1'};
          const r=await fetch('/Search/GetSearchResult/',{method:'POST',
            headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
            body:new URLSearchParams(base).toString(),credentials:'include'});
          const j=await r.json();
          const on=(j.RunList||[]).find(v=>[10,52,56,61,64,70,71].includes(v.StockBranchId)) || (j.RunList||[])[0];
          return on ? {StockNum:on.StockNum, StockId:on.StockId, Make:on.Make, Model:on.Model, Vin:on.Vin} : null;
        }""")
        print("[spike7] picked lot:", row)
        if not row:
            print("no lot"); await browser.close(); return

        captured = []
        async def on_resp(resp):
            try:
                ct = resp.headers.get("content-type","")
                if "json" in ct and re.search(r"(Vehicle|Detail|Lot|Stock|api|Bid)", resp.url, re.I):
                    body = (await resp.text())[:3000]
                    captured.append({"url":resp.url,"status":resp.status,"body":body})
            except Exception: pass
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        # Try the detail URL with stock number.
        detail_url = f"https://ca.iaai.com/VehicleDetails/{row['StockNum']}"
        print("[spike7] navigating detail:", detail_url)
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(6):
            await page.wait_for_timeout(2500)
            if len(await page.content()) > 80000: break
        try: await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception: pass

        html = await page.content()
        (OUT/"detail_page.html").write_text(html)
        print(f"[spike7] detail html {len(html)} bytes, url={page.url}")

        (OUT/"detail_network.json").write_text(json.dumps(captured, indent=2))
        print(f"[spike7] data XHRs captured: {len(captured)}")
        for c in captured:
            print("   ", c["status"], c["url"][:130])

        # Pull labelled spec rows from the DOM (IAAI uses dt/dd or label/value pairs).
        specs = await page.evaluate(r"""() => {
          const out={};
          document.querySelectorAll('li, tr, .data-list__item, .vehicle-details__item').forEach(el=>{
            const t=(el.innerText||'').trim();
            const m=t.match(/^([A-Za-z][A-Za-z \/().#-]{2,40})\s*[:\n]\s*(.+)$/);
            if(m && m[2].length<80) out[m[1].trim()]=m[2].trim();
          });
          return out;
        }""")
        (OUT/"detail_specs.json").write_text(json.dumps(specs, indent=2))
        print(f"[spike7] DOM spec pairs: {len(specs)}")
        for k,v in list(specs.items())[:40]:
            print(f"     {k} = {v}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
