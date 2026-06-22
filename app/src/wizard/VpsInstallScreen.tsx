/**
 * VpsInstallScreen — step 2 of the VPS setup flow (E-65 / S-216).
 *
 * Shows a copyable curl command that installs DepthFusion on the user's VPS.
 * A confirmation checkbox ("I've run the install script") must be ticked before
 * the Next button is enabled.
 */

import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

const VPS_INSTALL_CMD =
  'curl -fsSL https://get.depthfusion.ai/install-vps.sh | sudo bash'

interface VpsInstallScreenProps {
  onNext: () => void
}

export function VpsInstallScreen({ onNext }: VpsInstallScreenProps) {
  const [copied, setCopied] = useState(false)
  const [confirmed, setConfirmed] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(VPS_INSTALL_CMD)
      setCopied(true)
      setTimeout(() => setCopied(false), 2_000)
    } catch {
      // Clipboard API unavailable — silently ignore.
    }
  }

  return (
    <div
      className="df-emerge"
      style={{
        width: '100%',
        maxWidth: 520,
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sp-6)',
      }}
    >
      <div>
        <h2
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 'var(--display-weight)',
            fontSize: 'var(--fs-h2)',
            color: 'var(--text)',
            margin: 0,
          }}
        >
          Install on your server
        </h2>
        <p
          style={{
            fontSize: 'var(--fs-body)',
            color: 'var(--muted)',
            marginTop: 'var(--sp-2)',
            marginBottom: 0,
            lineHeight: 1.5,
          }}
        >
          SSH into your server and run this command as a user with sudo access.
        </p>
      </div>

      {/* Command block */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sp-2)',
          background: 'var(--surface-3)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-lg)',
          padding: 'var(--sp-3) var(--sp-4)',
        }}
      >
        <code
          style={{
            flex: 1,
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--fs-snippet)',
            color: 'var(--text)',
            wordBreak: 'break-all',
            lineHeight: 1.6,
          }}
        >
          {VPS_INSTALL_CMD}
        </code>
        <button
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={() => void handleCopy()}
          title={copied ? 'Copied!' : 'Copy to clipboard'}
          style={{ flexShrink: 0 }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>

      {/* Confirmation checkbox */}
      <label
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 'var(--sp-3)',
          cursor: 'pointer',
          padding: 'var(--sp-4)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-lg)',
        }}
      >
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
          style={{
            marginTop: 3,
            flexShrink: 0,
            accentColor: 'var(--accent)',
            width: 16,
            height: 16,
            cursor: 'pointer',
          }}
        />
        <span
          style={{
            fontSize: 'var(--fs-body)',
            color: 'var(--text)',
            lineHeight: 1.5,
          }}
        >
          I've run the install script and it completed successfully.
        </span>
      </label>

      <button
        className="df-btn df-btn--primary"
        onClick={onNext}
        disabled={!confirmed}
        style={{ width: '100%' }}
      >
        Continue
      </button>
    </div>
  )
}
