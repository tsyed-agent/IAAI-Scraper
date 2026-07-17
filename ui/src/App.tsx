import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, LayoutGroup } from 'framer-motion'
import {
  fetchCrawlStatus,
  fetchFilters,
  fetchLots,
  fetchStats,
  startCrawl,
} from './api'
import type { CrawlStatus, FiltersResponse, Lot, LotFilters, StatsResponse } from './types'
import { DEFAULT_FILTERS } from './types'
import { FilterBar } from './components/FilterBar'
import { LotCard, SkeletonCard } from './components/LotCard'
import { LotDetail } from './components/LotDetail'
import { SettingsDrawer } from './components/SettingsDrawer'
import { SyncRibbon } from './components/SyncRibbon'
import './index.css'

const PAGE = 24

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return v
}

export default function App() {
  const [filters, setFilters] = useState<LotFilters>(DEFAULT_FILTERS)
  const debouncedFilters = useDebounced(filters, 220)
  const [facets, setFacets] = useState<FiltersResponse | null>(null)
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [lots, setLots] = useState<Lot[]>([])
  const [total, setTotal] = useState(0)
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Lot | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [crawlStatus, setCrawlStatus] = useState<CrawlStatus | null>(null)
  const [syncStarting, setSyncStarting] = useState(false)
  const knownIds = useRef<Set<string>>(new Set())
  const sentinelRef = useRef<HTMLDivElement>(null)
  const requestGen = useRef(0)
  const filtersRef = useRef(debouncedFilters)
  filtersRef.current = debouncedFilters

  const syncRunning = Boolean(crawlStatus?.running || syncStarting)

  const refreshMeta = useCallback(async () => {
    try {
      const [f, s, c] = await Promise.all([
        fetchFilters().catch(() => null),
        fetchStats().catch(() => null),
        fetchCrawlStatus().catch(() => null),
      ])
      if (f) setFacets(f)
      if (s) setStats(s)
      if (c) setCrawlStatus(c)
    } catch {
      /* ignore meta errors */
    }
  }, [])

  const loadFirstPage = useCallback(async (opts: { soft?: boolean } = {}) => {
    const gen = ++requestGen.current
    if (!opts.soft) setLoading(true)
    setError(null)
    try {
      const data = await fetchLots(filtersRef.current, { limit: PAGE })
      if (gen !== requestGen.current) return
      const next = data.results || []
      if (opts.soft) {
        setLots((prev) => {
          const pageIds = new Set(next.map((l) => l.stock_number))
          const rest = prev.filter((l) => !pageIds.has(l.stock_number))
          for (const lot of next) knownIds.current.add(lot.stock_number)
          return [...next, ...rest]
        })
      } else {
        knownIds.current = new Set(next.map((l) => l.stock_number))
        setLots(next)
      }
      setTotal(data.total)
      setCursor(data.next_cursor)
    } catch (e) {
      if (gen !== requestGen.current) return
      setError(e instanceof Error ? e.message : String(e))
      if (!opts.soft) setLots([])
    } finally {
      if (gen === requestGen.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    knownIds.current.clear()
    setCursor(null)
    void loadFirstPage({ soft: false })
  }, [debouncedFilters, loadFirstPage])

  useEffect(() => {
    void refreshMeta()
  }, [refreshMeta])

  useEffect(() => {
    if (!syncRunning) return
    const tick = async () => {
      try {
        const c = await fetchCrawlStatus()
        setCrawlStatus(c)
        setSyncStarting(false)
        await loadFirstPage({ soft: true })
        const s = await fetchStats().catch(() => null)
        if (s) setStats(s)
        if (!c.running) {
          const f = await fetchFilters().catch(() => null)
          if (f) setFacets(f)
        }
      } catch {
        /* keep UI alive during sync */
      }
    }
    void tick()
    const id = window.setInterval(() => void tick(), 2200)
    return () => window.clearInterval(id)
  }, [syncRunning, loadFirstPage])

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore || loading) return
    setLoadingMore(true)
    try {
      const data = await fetchLots(filtersRef.current, { limit: PAGE, cursor })
      const incoming = (data.results || []).filter((l) => !knownIds.current.has(l.stock_number))
      for (const l of incoming) knownIds.current.add(l.stock_number)
      setLots((prev) => [...prev, ...incoming])
      setCursor(data.next_cursor)
      setTotal(data.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingMore(false)
    }
  }, [cursor, loadingMore, loading])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void loadMore()
      },
      { rootMargin: '600px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [loadMore])

  const onSync = async () => {
    setError(null)
    setSyncStarting(true)
    try {
      await startCrawl({ max_list_pages: 50 })
      const c = await fetchCrawlStatus()
      setCrawlStatus(c)
    } catch (e) {
      setSyncStarting(false)
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const empty = !loading && lots.length === 0
  const showing = useMemo(() => lots.length, [lots])

  return (
    <LayoutGroup>
      <div className="app-shell">
        <header className="site-header">
          <div className="brand">
            <div className="brand-mark">Yardline</div>
            <div className="brand-sub">Ontario auction lots</div>
          </div>
          <div className="header-actions">
            <span className="stat-pill">
              Inventory <strong>{stats?.total_lots ?? total}</strong>
            </span>
            <button type="button" className="btn btn-ghost" onClick={() => setSettingsOpen(true)}>
              API
            </button>
          </div>
        </header>

        <SyncRibbon status={crawlStatus} onSync={onSync} syncing={syncStarting} />

        <FilterBar filters={filters} facets={facets} onChange={setFilters} />

        {error && <div className="error-banner">{error}</div>}

        <div className="results-meta">
          <span>
            Showing <strong>{showing}</strong>
            {total ? (
              <>
                {' '}
                of <strong>{total}</strong>
              </>
            ) : null}
            {syncRunning ? ' · live' : ''}
          </span>
          <span>{loading ? 'Loading catalog…' : 'Scroll for more'}</span>
        </div>

        {empty ? (
          <div className="empty-state">
            <h2>{syncRunning ? 'Lots are on the way' : 'No lots yet'}</h2>
            <p>
              {syncRunning
                ? 'The catalog is empty while the sync starts. Cards will flow in as soon as the first page lands.'
                : 'Start a sync to pull Ontario inventory into the private API. The grid stays interactive the whole time.'}
            </p>
            {!syncRunning && (
              <button type="button" className="btn btn-primary" onClick={onSync}>
                Start sync
              </button>
            )}
          </div>
        ) : (
          <div className="lot-grid">
            {loading && lots.length === 0
              ? Array.from({ length: 8 }, (_, i) => <SkeletonCard key={i} />)
              : lots.map((lot, i) => (
                  <LotCard key={lot.stock_number} lot={lot} index={i} onOpen={setSelected} />
                ))}
            {loadingMore &&
              Array.from({ length: 3 }, (_, i) => <SkeletonCard key={`more-${i}`} />)}
          </div>
        )}

        <div ref={sentinelRef} className="sentinel" aria-hidden />
      </div>

      <AnimatePresence mode="wait">
        {selected && (
          <LotDetail key={selected.stock_number} lot={selected} onClose={() => setSelected(null)} />
        )}
      </AnimatePresence>

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </LayoutGroup>
  )
}
