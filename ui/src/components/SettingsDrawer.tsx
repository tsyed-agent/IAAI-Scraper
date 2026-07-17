import { useState } from 'react'
import { getToken, setToken } from '../api'

export function SettingsDrawer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [value, setValue] = useState(getToken)

  if (!open) return null

  return (
    <div className="settings-drawer" onClick={onClose} role="presentation">
      <div
        className="settings-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="API settings"
      >
        <h3>API access</h3>
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: '0.92rem' }}>
          Optional Bearer token for authenticated hosts. Leave blank for local open access.
        </p>
        <label>
          API token
          <input
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="IAAI_API_TOKEN"
          />
        </label>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setToken(value.trim())
              onClose()
            }}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
