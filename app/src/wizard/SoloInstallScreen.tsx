/**
 * SoloInstallScreen — step 2 of the Solo setup flow (E-65 / S-215).
 *
 * Shows the copyable one-liner that installs and starts the local DepthFusion
 * server. Polls `http://localhost:7300/health` every 3 s via `checkServerHealth`.
 * Transitions through:
 *   idle → polling ("Waiting for server…" spinner)
 *      → detected ("Server detected ✓")
 *      → auto-advance after 1 s
 *
 * The polling interval is always cleared on unmount to prevent memory leaks.
 */

import { useEffect, useRef, useState } from 'react'
import { Copy, Check, Loader2 } from 'lucide-react'
import { checkServerHealth } from '../lib/ipc'

const SOLO_SERVER_URL = 'http://localhost:7300'
const POLL_INTERVAL_MS = 3_000
const ADVANCE_DELAY_MS = 1_000

const INSTALL_CMD =
  'curl -fsSL https://get.depthfusion.ai/install-mac-solo.sh | bash'

interface SoloInstallScreenProps {
  onNext: () => void
}

type PollState = 'idle' | 'polling' | 'detected'

export function SoloInstallScreen({ onNext }: SoloInstallScreenProps) {
  const [copied, setCopied] = useState(false)
  const [pollState, setPollState] = useState<PollState>('polling')
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const advanceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Start polling immediately on mount and clean up on unmount.
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      void checkServerHealth(SOLO_SERVER_URL)
        .then((healthy) => {
          if (!healthy) return

          // Server is up — stop the poll and schedule the auto-advance.
          if (intervalRef.current !== null) {
            clearInterval(intervalRef.current)
            intervalRef.current = null
          }

          setPollState('detected')

          advanceTimerRef.current = setTimeout(() => {
            onNext()
          }, ADVANCE_DELAY_MS)
        })
        .catch(() => {
          // Network errors are expected while server isn't running yet; ignore.
        })
    }, POLL_INTERVAL_MS)

    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      if (advanceTimerRef.current !== null) {
        clearTimeout(advanceTimerRef.current)
        advanceTimerRef.current = null
      }
    }
  }, [onNext])

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD)
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
          Install the solo server
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
          Run this command in your terminal. It will install the DepthFusion
          server and configure it to start on login.
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
          {INSTALL_CMD}
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

      {/* Status indicator */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sp-3)',
          padding: 'var(--sp-4)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-lg)',
        }}
      >
        {pollState === 'polling' && (
          <>
            <Loader2
              size={18}
              style={{
                color: 'var(--muted)',
                animation: 'spin 1s linear infinite',
                flexShrink: 0,
              }}
            />
            <span style={{ fontSize: 'var(--fs-body)', color: 'var(--muted)' }}>
              Waiting for server…
            </span>
          </>
        )}

        {pollState === 'detected' && (
          <>
            <Check size={18} style={{ color: 'var(--accent)', flexShrink: 0 }} />
            <span style={{ fontSize: 'var(--fs-body)', color: 'var(--text)' }}>
              Server detected ✓
            </span>
          </>
        )}

        {pollState === 'idle' && (
          <span style={{ fontSize: 'var(--fs-body)', color: 'var(--muted)' }}>
            Run the command above, then return here.
          </span>
        )}
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
