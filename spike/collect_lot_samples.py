"""
Manual live investigation: fetch full RunList rows + VDP text for ~10 diverse lots.

Does NOT use the production scraper. Uses Playwright in-page fetch against the same
endpoints the site uses. Output is written to spike/out/lot_samples_reference.json
for field-name / status / price research.

Run: . .venv/bin/activate && python spike/collect_lot_samples.py
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
OUT_FILE = OUT / "lot_samples_reference.json"

START_URL = "https://ca.iaai.com/Search"
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || { runtime: {} };
"""

SEARCH_JS = """
async (opts) => {
  const base = {
    Url:'', Keyword:'', PageLoading:'false', PageSize:String(opts.pageSize||100),
    IsKeywordSearch:'false', IsNewSearch: opts.page>1?'false':'true',
    RunlistSort:'STOCK ASC', IgnoreKeywordYear:'false', YearFrom:'', YearTo:'', VehicleAge:'',
    AuctionToday:'false', AuctionTomorrow:'false', VehicleBrand:'', StockFilter:'',
    IsBuyNow:'false', IsVehicleRemarketing:'false', ShowOnlyPublicAuctions:'false',
    VehicleTypeIds:'', VehicleMakeIds:'', VehicleModelIds:'', VehicleBrandCodeIds:'',
    BranchIds:'', AuctionTypeIds:'', AuctionIds:'', RequestSource:'',
    IsManagerPick:'false', IsWatching:'false', IsPreBidWinning:'false', IsPreBidOutBid:'false',
    AuctionMode:'', IsSiteSale:'', IsBuyNowOffer:'false',
    VehicleTypeListPageIndex:'1', BranchListPageIndex:'1', MakeListPageIndex:'1',
    ModelListPageIndex:'1', VehicleProvinceIds:'', VehicleProvincePageIndex:'1',
    RunlistPageIndex:String(opts.page||1), AttributePageSize:'100',
    Caller:'getVehicleResults', RunlistPageSize:String(opts.pageSize||100),
    IsRepossession:'false', page:String(opts.page||1),
  };
  const body = new URLSearchParams(base).toString();
  const r = await fetch('/Search/GetSearchResult/', {
    method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
    body, credentials:'include',
  });
  const text = await r.text();
  let j;
  try { j = JSON.parse(text); } catch (e) { return {ok:false, status:r.status, text:text.slice(0,500)}; }
  return {ok:true, status:r.status, json:j};
}
"""

VDP_SPECS_JS = r"""
() => {
  const out = {};
  document.querySelectorAll('li, tr, dl > div, .data-list__item, span, div').forEach(el => {
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    const m = t.match(/^([A-Za-z][A-Za-z \/().#-]{2,40})\s*:\s*(.+)$/);
    if (m && m[2].length < 120) out[m[1].trim()] = m[2].trim();
  });
  return out;
}
"""

PRICE_STATUS_KEYS = [
    "PrebidItemStatusID", "PrebidItemStatusDesc", "WinningbidAmount", "WinningBidAmount",
    "HighPrebidValue", "HighPrebid", "BuyNowPrice", "TimedAuctionHighestBidAmountValue",
    "TimedAuctionBuyNowPrice", "BuyNowOfferPrice", "BidItemClosingDateUTC",
    "ServerCurrentDateUTC", "AuctionDateUTC", "AuctionDate", "IsTimedAuction",
    "SaleStatus", "SaleStatusDesc", "ItemStatus", "ItemStatusDesc", "Status",
    "StatusDesc", "BidStatus", "BidStatusDesc", "IsSold", "Sold", "SoldPrice",
    "CurrentBid", "CurrentBidAmount", "PreBidAmount", "MinimumBid",
]


def _ontario_branch_ids() -> set[int]:
    return {10, 52, 56, 61, 64, 70, 71}


def _pick_diverse(rows: list[dict], target: int = 10) -> list[dict]:
    on = [r for r in rows if r.get("StockBranchId") in _ontario_branch_ids()]
    if not on:
        on = rows[:target]
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in on:
        bid = r.get("PrebidItemStatusDesc") or r.get("SaleStatusDesc") or "unknown_status"
        branch = str(r.get("StockBranchId"))
        timed = "timed" if r.get("IsTimedAuction") in (True, "True", "true", 1) else "lane"
        win = r.get("WinningbidAmount") or r.get("WinningBidAmount") or 0
        has_prebid = bool(r.get("HighPrebidValue") or r.get("HighPrebid"))
        key = f"{branch}|{timed}|{bid}|win={bool(win)}|prebid={has_prebid}"
        buckets[key].append(r)
    picked: list[dict] = []
    for bucket_rows in buckets.values():
        if len(picked) >= target:
            break
        picked.append(bucket_rows[0])
    seen_branches = {p.get("StockBranchId") for p in picked}
    for r in on:
        if len(picked) >= target:
            break
        if r.get("StockBranchId") not in seen_branches and r not in picked:
            picked.append(r)
            seen_branches.add(r.get("StockBranchId"))
    for r in on:
        if len(picked) >= target:
            break
        if r not in picked:
            picked.append(r)
    return picked[:target]


def _price_status_slice(row: dict) -> dict:
    out = {}
    for k in PRICE_STATUS_KEYS:
        if k in row and row[k] not in (None, "", 0, "0", "$0.00"):
            out[k] = row[k]
    return out


def _all_keys_union(rows: list[dict]) -> list[str]:
    keys: set[str] = set()
    for r in rows:
        keys.update(r.keys())
    return sorted(keys)


async def main() -> None:
    all_rows: list[dict] = []
    collected_pages: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="en-CA", timezone_id="America/Toronto", viewport={"width": 1366, "height": 900},
        )
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        for _ in range(20):
            await page.wait_for_timeout(2500)
            html = await page.content()
            if len(html) > 100_000 and "Incapsula incident ID" not in html:
                break
        else:
            raise RuntimeError("Incapsula challenge did not clear")

        for pg in range(1, 6):
            result = await page.evaluate(SEARCH_JS, {"page": pg, "pageSize": 100})
            if not result.get("ok"):
                raise RuntimeError(f"search page {pg} failed: {result}")
            payload = result["json"]
            rows = payload.get("RunList") or []
            all_rows.extend(rows)
            total = None
            for item in payload.get("SummaryList") or []:
                if item.get("Description") == "TOTAL_COUNT":
                    total = item.get("Count")
            collected_pages.append({"page": pg, "total": total, "returned": len(rows)})
            await page.wait_for_timeout(1200)

        samples = _pick_diverse(all_rows, target=10)
        enriched_samples = []

        for row in samples:
            stock = row.get("StockNum")
            vdp_url = f"https://ca.iaai.com/Vehicles/VehicleDetails?stockno={stock}"
            vdp_specs: dict = {}
            vdp_html_len = 0
            try:
                await page.goto(vdp_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                vdp_html_len = len(html)
                if "Incapsula incident ID" not in html:
                    vdp_specs = await page.evaluate(VDP_SPECS_JS)
            except Exception as e:
                vdp_specs = {"_error": str(e)}
            await page.wait_for_timeout(1500)

            enriched_samples.append({
                "stock_number": stock,
                "selection_reason": {
                    "branch_id": row.get("StockBranchId"),
                    "branch_name": row.get("StockBranchDescription"),
                    "is_timed_auction": row.get("IsTimedAuction"),
                    "prebid_status": row.get("PrebidItemStatusDesc"),
                    "price_status_slice": _price_status_slice(row),
                },
                "runlist_row_full": row,
                "vdp_url": vdp_url,
                "vdp_html_bytes": vdp_html_len,
                "vdp_label_value_specs": vdp_specs,
            })

        status_values = Counter()
        for r in all_rows:
            for key in ("PrebidItemStatusDesc", "SaleStatusDesc", "ItemStatusDesc", "StatusDesc", "BidStatusDesc"):
                val = r.get(key)
                if val:
                    status_values[f"{key}={val}"] += 1

        price_key_presence = Counter()
        for r in all_rows:
            for k in PRICE_STATUS_KEYS:
                if k in r:
                    price_key_presence[k] += 1

        report = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "method": "manual_playwright_in_page_fetch",
            "pages_fetched": collected_pages,
            "total_rows_scanned": len(all_rows),
            "ontario_rows_scanned": sum(1 for r in all_rows if r.get("StockBranchId") in _ontario_branch_ids()),
            "all_runlist_keys": _all_keys_union(all_rows),
            "price_status_key_presence_in_scan": dict(price_key_presence),
            "status_value_counts_in_scan": dict(status_values.most_common(50)),
            "samples": enriched_samples,
        }

        OUT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"Wrote {OUT_FILE} ({len(enriched_samples)} samples, {len(all_rows)} rows scanned)")
        print("Status values seen:", dict(status_values.most_common(15)))
        print("Price/status keys present:", {k: price_key_presence[k] for k in sorted(price_key_presence) if price_key_presence[k]})

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
