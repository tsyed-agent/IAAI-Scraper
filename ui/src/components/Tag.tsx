import { motion } from 'framer-motion'

type TagTone =
  | 'status-active'
  | 'status-sold'
  | 'status-if_bid'
  | 'status-passed'
  | 'status-removed'
  | 'damage'
  | 'title'
  | 'branch'
  | 'runs'
  | 'starts'
  | 'keys'
  | 'muted'

const STATUS_TONE: Record<string, TagTone> = {
  active: 'status-active',
  sold: 'status-sold',
  if_bid: 'status-if_bid',
  passed: 'status-passed',
  removed: 'status-removed',
}

export function statusTone(status?: string | null): TagTone {
  if (!status) return 'muted'
  return STATUS_TONE[status] || 'muted'
}

export function Tag({
  label,
  tone = 'muted',
}: {
  label: string
  tone?: TagTone
}) {
  return (
    <motion.span
      className={`tag tag-${tone}`}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
    >
      {label}
    </motion.span>
  )
}
