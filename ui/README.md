# Yardline (Phase 3 storefront)

React + TypeScript + Vite + Framer Motion catalog UI for Ontario lots.

Talks **only** to the local FastAPI (`/lots`, `/filters`, `/commands/crawl`, signed thumbs).

```bash
npm install
npm run build   # → ../iaai_scraper/static/ui/
npm run dev     # proxies API to :8000
```

Served at `http://127.0.0.1:8000/ui/` after `python -m iaai_scraper.cli serve`.
