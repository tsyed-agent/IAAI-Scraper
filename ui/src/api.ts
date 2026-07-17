import type {
  CrawlStatus,
  FiltersResponse,
  Lot,
  LotFilters,
  LotsListResponse,
  PriceHistoryEntry,
  StatsResponse,
} from './types'

const TOKEN_KEY = 'yardline_api_token'

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

function authHeaders(): HeadersInit {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...authHeaders(),
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function filtersToParams(f: LotFilters, extras?: Record<string, string>): URLSearchParams {
  const p = new URLSearchParams()
  if (f.status && f.status !== 'all') p.set('status', f.status)
  if (f.make) p.set('make', f.make)
  if (f.branch_id) p.set('branch_id', f.branch_id)
  if (f.primary_damage) p.set('primary_damage', f.primary_damage)
  if (f.title_brand_type) p.set('title_brand_type', f.title_brand_type)
  if (f.keyword) p.set('keyword', f.keyword)
  if (f.year_min) p.set('year_min', f.year_min)
  if (f.year_max) p.set('year_max', f.year_max)
  if (f.runs) p.set('runs', f.runs)
  if (f.starts) p.set('starts', f.starts)
  if (f.has_keys) p.set('has_keys', f.has_keys)
  if (f.sort) p.set('sort', f.sort)
  p.set('descending', String(f.descending))
  if (extras) {
    for (const [k, v] of Object.entries(extras)) {
      if (v) p.set(k, v)
    }
  }
  return p
}

export function fetchLots(
  filters: LotFilters,
  opts: { limit?: number; cursor?: string | null; offset?: number } = {},
): Promise<LotsListResponse> {
  const extras: Record<string, string> = {
    limit: String(opts.limit ?? 24),
  }
  if (opts.cursor) extras.cursor = opts.cursor
  else if (opts.offset) extras.offset = String(opts.offset)
  return api(`/lots?${filtersToParams(filters, extras)}`)
}

export function fetchLot(stock: string): Promise<Lot> {
  return api(`/lots/${encodeURIComponent(stock)}`)
}

export function fetchFilters(): Promise<FiltersResponse> {
  return api('/filters')
}

export function fetchStats(): Promise<StatsResponse> {
  return api('/stats')
}

export function fetchCrawlStatus(): Promise<CrawlStatus> {
  return api('/commands/crawl/status')
}

export function startCrawl(body: { max_list_pages?: number } = {}): Promise<{ job_id: string }> {
  return api('/commands/crawl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function fetchPriceHistory(stock: string): Promise<{ history: PriceHistoryEntry[] }> {
  return api(`/lots/${encodeURIComponent(stock)}/price-history?limit=40`)
}

export function money(amount?: number | null, currency = 'CAD'): string | null {
  if (amount == null || Number.isNaN(amount)) return null
  try {
    return new Intl.NumberFormat('en-CA', {
      style: 'currency',
      currency: currency || 'CAD',
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `$${Math.round(amount).toLocaleString()}`
  }
}

export function titleLine(lot: Lot): string {
  const parts = [lot.year, lot.make, lot.model].filter(Boolean)
  return parts.length ? parts.join(' ') : `Stock ${lot.stock_number}`
}

export function odometerLabel(lot: Lot): string | null {
  if (lot.odometer == null) return null
  const unit = lot.odometer_unit || 'km'
  return `${lot.odometer.toLocaleString()} ${unit}`
}
