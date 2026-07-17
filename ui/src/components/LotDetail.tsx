import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { fetchLot, fetchPriceHistory, money, odometerLabel, titleLine } from '../api'
import type { Lot, PriceHistoryEntry } from '../types'
import { LazyThumb } from './LazyThumb'
import { Tag, statusTone } from './Tag'

export function LotDetail({
  lot,
  onClose,
}: {
  lot: Lot
  onClose: () => void
}) {
  const [detail, setDetail] = useState<Lot>(lot)
  const [history, setHistory] = useState<PriceHistoryEntry[]>([])
  const [loadingExtra, setLoadingExtra] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoadingExtra(true)
    Promise.all([
      fetchLot(lot.stock_number).catch(() => lot),
      fetchPriceHistory(lot.stock_number).catch(() => ({ history: [] as PriceHistoryEntry[] })),
    ]).then(([d, h]) => {
      if (cancelled) return
      setDetail(d)
      setHistory(h.history || [])
      setLoadingExtra(false)
    })
    return () => {
      cancelled = true
    }
  }, [lot])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [onClose])

  const price =
    money(detail.final_price, detail.currency || 'CAD') ||
    money(detail.high_prebid, detail.currency || 'CAD') ||
    money(detail.timed_high_bid, detail.currency || 'CAD') ||
    money(detail.buy_now_price, detail.currency || 'CAD')

  const facts: { label: string; value: string }[] = [
    { label: 'Stock', value: detail.stock_number },
    { label: 'Branch', value: detail.branch_name || '—' },
    { label: 'Location', value: detail.location || detail.location_name || '—' },
    { label: 'Auction', value: detail.auction_date || detail.auction_datetime_display || '—' },
    { label: 'Odometer', value: odometerLabel(detail) || '—' },
    { label: 'Damage', value: detail.primary_damage || '—' },
    { label: 'Title', value: detail.title_brand_type || detail.title_brand || '—' },
    {
      label: 'Estimate',
      value: money(detail.damage_estimate, detail.currency || 'CAD') || '—',
    },
    { label: 'Engine', value: detail.engine || '—' },
    { label: 'Transmission', value: detail.transmission || '—' },
    { label: 'Fuel', value: detail.fuel_type || '—' },
    { label: 'VIN', value: detail.vin || '—' },
  ]

  return (
    <motion.div
      className="detail-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.28 }}
      onClick={onClose}
    >
      <motion.article
        className="detail-panel"
        layoutId={`card-${lot.stock_number}`}
        initial={{ y: 40, opacity: 0.85 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 28, opacity: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 32 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={titleLine(detail)}
      >
        <div className="detail-hero">
          <button type="button" className="detail-close" onClick={onClose} aria-label="Close">
            ×
          </button>
          <motion.div layoutId={`thumb-${lot.stock_number}`} style={{ height: '100%' }}>
            <LazyThumb
              href={detail.thumbnail_href || lot.thumbnail_href}
              alt={titleLine(detail)}
              className="detail-hero-thumb"
            />
          </motion.div>
        </div>

        <div className="detail-body">
          <div className="detail-head">
            <div>
              <motion.h2 layoutId={`title-${lot.stock_number}`}>{titleLine(detail)}</motion.h2>
              <div className="tag-row" style={{ marginTop: 10 }}>
                {detail.status && (
                  <Tag label={detail.status.replace('_', ' ')} tone={statusTone(detail.status)} />
                )}
                {detail.primary_damage && <Tag label={detail.primary_damage} tone="damage" />}
                {detail.title_brand_type && <Tag label={detail.title_brand_type} tone="title" />}
                {detail.branch_name && <Tag label={detail.branch_name} tone="branch" />}
                {detail.runs && <Tag label="Runs" tone="runs" />}
                {detail.starts && <Tag label="Starts" tone="starts" />}
                {detail.has_keys && <Tag label="Keys" tone="keys" />}
              </div>
            </div>
            <div className="detail-price">{price || 'No bid yet'}</div>
          </div>

          <dl className="detail-grid">
            {facts.map((f) => (
              <div className="fact" key={f.label}>
                <dt>{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
          </dl>

          <section>
            <h3
              style={{
                margin: '0 0 10px',
                fontFamily: 'var(--font-display)',
                fontSize: '1.05rem',
              }}
            >
              Price history
            </h3>
            {loadingExtra && history.length === 0 ? (
              <div className="skeleton-line" style={{ height: 42 }} />
            ) : history.length === 0 ? (
              <p style={{ color: 'var(--muted)', margin: 0 }}>No price changes recorded yet.</p>
            ) : (
              <ul className="history-list">
                {history.slice(0, 12).map((h) => (
                  <li key={h.id}>
                    <span>
                      {h.price_type}
                      <span style={{ color: 'var(--muted)', marginLeft: 8 }}>
                        {new Date(h.observed_at).toLocaleString()}
                      </span>
                    </span>
                    <strong>{money(h.amount, h.currency || 'CAD') || '—'}</strong>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </motion.article>
    </motion.div>
  )
}
