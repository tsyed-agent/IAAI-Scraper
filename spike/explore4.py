"""
Phase 0 spike #4: read the Province filter options straight from the rendered DOM
to learn Ontario's province id, and confirm an Ontario-filtered search Total.
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "out"
START_URL = "https://ca.iaai.com/Search"
STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

# Scan the DOM for anything province-related: inputs, labels, data-* attrs.
DOM_JS = r"""
() => {
  const results = {inputs: [], labelsWithON: [], anyProvinceAttr: []};
  // checkboxes / radios whose id/name/value mention province
  document.querySelectorAll('input').forEach(el => {
    const blob = (el.id+' '+el.name+' '+el.value+' '+(el.getAttribute('data-key')||'')).toLowerCase();
    if (blob.includes('province')) {
      results.inputs.push({id:el.id, name:el.name, value:el.value,
        dataset: Object.assign({}, el.dataset), label: (el.closest('label')||{}).innerText});
    }
  });
  // labels that look like Ontario / ON
  document.querySelectorAll('label, span, li, a').forEach(el => {
    const t = (el.innerText||'').trim();
    if (/^Ontario/i.test(t) || t === 'ON') {
      const inp = el.querySelector('input') || (el.parentElement||{}).querySelector?.('input');
      results.labelsWithON.push({text:t, html: el.outerHTML.slice(0,300)});
    }
  });
  // elements carrying a province id in data attributes
  document.querySelectorAll('[data-province-id],[data-provinceid],[data-id]').forEach(el => {
    results.anyProvinceAttr.push({tag:el.tagName, text:(el.innerText||'').trim().slice(0,40),
      html: el.outerHTML.slice(0,200)});
  });
  return results;
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

        # Try to expand the Province filter section so its options render.
        for sel in ['text=Province', '[data-key*="Province"]', 'a:has-text("Province")']:
            try:
                el = page.locator(sel).first
                if await el.count():
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(1500)
                    print(f"[spike4] clicked {sel}")
                    break
            except Exception as e:
                print(f"[spike4] click {sel} failed: {e}")

        await page.wait_for_timeout(2000)
        dom = await page.evaluate(DOM_JS)
        (OUT / "province_dom.json").write_text(json.dumps(dom, indent=2))
        print("inputs(province):", len(dom["inputs"]))
        for i in dom["inputs"][:30]:
            print("   ", i)
        print("labelsWithON:", len(dom["labelsWithON"]))
        for i in dom["labelsWithON"][:10]:
            print("   ", i["text"], "|", i["html"][:160])

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
