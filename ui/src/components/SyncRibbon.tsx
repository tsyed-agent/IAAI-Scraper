import { motion } from 'framer-motion'
import type { CrawlStatus } from '../types'

export function SyncRibbon({
  status,
  onSync,
  syncing,
}: {
  status: CrawlStatus | null
  onSync: () => void
  syncing: boolean
}) {
  const running = Boolean(status?.running || syncing)
  const job = status?.job
  const report = job?.report
  const inserted = report?.inserted ?? 0
  const updated = report?.updated ?? 0
  const seen = report?.ontario_seen ?? 0

  return (
    <motion.section
      className={`sync-ribbon ${running ? '' : 'idle'}`}
      layout
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      aria-live="polite"
    >
      <div className="sync-ribbon-row">
        <div>
          <div className="sync-title">
            {running
              ? 'Syncing Ontario lots'
              : job?.status === 'completed'
                ? 'Catalog up to date'
                : job?.status === 'failed'
                  ? 'Last sync failed'
                  : 'Ready to sync'}
          </div>
          <div className="sync-meta">
            {running
              ? 'Listings appear as they land — keep scrolling.'
              : job?.error
                ? job.error
                : 'Private API only. Thumbs load as you scroll.'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          {(running || seen > 0) && (
            <div className="sync-counts">
              <span className="stat-pill">
                Seen <strong>{seen}</strong>
              </span>
              <span className="stat-pill">
                New <strong>{inserted}</strong>
              </span>
              <span className="stat-pill">
                Updated <strong>{updated}</strong>
              </span>
            </div>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={onSync}
            disabled={running}
          >
            {running ? 'Syncing…' : 'Start sync'}
          </button>
        </div>
      </div>
      {running && (
        <div className="sync-bar" aria-hidden>
          <i />
        </div>
      )}
    </motion.section>
  )
}
