import { money, odometerLabel, titleLine } from '../api'
import type { Lot } from '../types'
import { LazyThumb } from './LazyThumb'
import { Tag, statusTone } from './Tag'
import { motion } from 'framer-motion'

export function LotCard({
  lot,
  index,
  onOpen,
}: {
  lot: Lot
  index: number
  onOpen: (lot: Lot) => void
}) {
  const price =
    money(lot.final_price, lot.currency || 'CAD') ||
    money(lot.high_prebid, lot.currency || 'CAD') ||
    money(lot.timed_high_bid, lot.currency || 'CAD') ||
    money(lot.buy_now_price, lot.currency || 'CAD')

  const odo = odometerLabel(lot)

  return (
    <motion.button
      type="button"
      className="lot-card"
      layout
      layoutId={`card-${lot.stock_number}`}
      initial={{ opacity: 0, y: 18, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        duration: 0.42,
        delay: Math.min(index % 12, 8) * 0.035,
        ease: [0.22, 1, 0.36, 1],
      }}
      onClick={() => onOpen(lot)}
      aria-label={`Open ${titleLine(lot)}`}
    >
      <motion.div layoutId={`thumb-${lot.stock_number}`}>
        <LazyThumb href={lot.thumbnail_href} alt={titleLine(lot)} />
      </motion.div>
      <div className="card-body">
        <motion.h3 className="card-title" layoutId={`title-${lot.stock_number}`}>
          {titleLine(lot)}
        </motion.h3>
        <div className="card-meta">
          <span>#{lot.stock_number}</span>
          <span className="card-price">{price || '—'}</span>
        </div>
        <div className="tag-row">
          {lot.status && (
            <Tag label={lot.status.replace('_', ' ')} tone={statusTone(lot.status)} />
          )}
          {lot.primary_damage && <Tag label={lot.primary_damage} tone="damage" />}
          {lot.title_brand_type && <Tag label={lot.title_brand_type} tone="title" />}
          {lot.branch_name && <Tag label={lot.branch_name} tone="branch" />}
          {lot.runs && <Tag label="Runs" tone="runs" />}
          {lot.starts && <Tag label="Starts" tone="starts" />}
          {lot.has_keys && <Tag label="Keys" tone="keys" />}
          {odo && <Tag label={odo} tone="muted" />}
        </div>
      </div>
    </motion.button>
  )
}

export function SkeletonCard() {
  return (
    <div className="lot-card skeleton-card" aria-hidden>
      <div className="thumb-wrap">
        <div className="thumb-shimmer" />
      </div>
      <div className="card-body">
        <div className="skeleton-line" style={{ width: '72%', height: 16 }} />
        <div className="skeleton-line" style={{ width: '48%' }} />
        <div className="tag-row">
          <div className="skeleton-line" style={{ width: 64, height: 22, borderRadius: 999 }} />
          <div className="skeleton-line" style={{ width: 84, height: 22, borderRadius: 999 }} />
        </div>
      </div>
    </div>
  )
}
