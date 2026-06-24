"""Optional lot detail-page enrichment.

The search row is already comprehensive, so detail enrichment is OFF by default
(it adds one request per lot => more runtime and anti-bot exposure). When enabled,
this pulls the VDP HTML and scrapes labelled spec rows that aren't in the list row.

We intentionally do NOT download images (per the brief) — only text fields.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import config
from .models import Lot
from .session import IaaiSession

log = logging.getLogger("iaai.detail")

# Extract simple "Label: Value" pairs from visible text in the DOM.
_SPECS_JS = r"""
() => {
  const out = {};
  document.querySelectorAll('li, tr, dl > div, .data-list__item').forEach(el => {
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    const m = t.match(/^([A-Za-z][A-Za-z \/().#-]{2,40})\s*:\s*(.+)$/);
    if (m && m[2].length < 100) out[m[1].trim()] = m[2].trim();
  });
  return out;
}
"""


async def enrich(session: IaaiSession, lot: Lot) -> Lot:
    """Fetch the VDP for a lot and merge any extra spec fields into lot.raw.

    Failures are swallowed (best-effort): enrichment must never abort a crawl.
    """
    url = config.DETAIL_URL_TEMPLATE.format(stock_num=lot.stock_number)
    try:
        await session.get_html(url)  # navigates the page to the VDP
        specs: dict[str, Any] = await session._page.evaluate(_SPECS_JS)  # type: ignore[attr-defined]
        if specs:
            lot.raw["_detail_specs"] = specs
            # Promote a few high-value fields if present and missing on the lot.
            if not lot.vin and specs.get("VIN") and "*" not in specs["VIN"]:
                lot.vin = specs["VIN"]
        log.debug("enriched %s with %d spec fields", lot.stock_number, len(specs))
    except Exception as e:  # noqa: BLE001 - best-effort
        log.warning("enrich failed for %s: %s", lot.stock_number, e)
    return lot
