/**
 * ServerUrlScreen — shared by the VPS and Connect flows (E-65 / S-216).
 *
 * Presents a URL text input pre-filled with the current default server URL.
 * On submit it calls `checkServerHealth`; if the server is unreachable it
 * surfaces an inline error and keeps the URL editable (the user can retry or
 * change the URL). When the health check succeeds, `onNext` is called to
 * advance the wizard.
 */

import { useState } from 'react'
import { Loader2, AlertCircle } from 'lucide-react'
import { checkServerHealth, setServerUrl } from '../lib/ipc'

const DEFAULT_SERVER_URL = 'https://localhost:8000'

interface ServerUrlScreenProps {
  /** Called when the server health check succeeds. */
  onNext: () => void
  /**
   * URL to pre-fill the input. Defaults to `DEFAULT_SERVER_URL` when omitted.
   */
  initialUrl?: string
}

export function ServerUrlScreen({ onNext, initialUrl }: ServerUrlScreenProps) {
  const [url, setUrl] = useState(initialUrl ?? DEFAULT_SERVER_URL)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const trimmed = url.trim()
    if (!trimmed) {
      setError('Please enter a server URL.')
      return
    }

    setChecking(true)
    try {
      const healthy = await checkServerHealth(trimmed)
      if (!healthy) {
        setError(
          'Could not reach the server. Check the URL and make sure the server is running, then try again.',
        )
        return
      }

      // Persist the URL so subsequent app sessions start with the right default.
      await setServerUrl(trimmed)
      onNext()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg || 'An unexpected error occurred. Please try again.')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div
      className="df-emerge"
      style={{
        width: '100%',
        maxWidth: 480,
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
          Enter your server URL
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
          DepthFusion will connect to this address. Make sure the server is
          running before continuing.
        </p>
      </div>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
          <label
            htmlFor="server-url-input"
            style={{
              fontSize: 'var(--fs-label)',
              fontWeight: 'var(--fw-medium)',
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            Server URL
          </label>

          <input
            id="server-url-input"
            type="url"
            className="df-input"
            placeholder="https://your-server.example.com"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              if (error) setError(null)
            }}
            disabled={checking}
            autoComplete="off"
            spellCheck={false}
            style={{ width: '100%' }}
          />

          {error && (
            <div
              role="alert"
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 'var(--sp-2)',
                padding: 'var(--sp-3)',
                background: 'var(--danger-wash)',
                border: '1px solid var(--danger-soft)',
                borderRadius: 'var(--r-md)',
              }}
            >
              <AlertCircle
                size={16}
                style={{ color: 'var(--danger-soft)', flexShrink: 0, marginTop: 1 }}
              />
              <p
                style={{
                  fontSize: 'var(--fs-body)',
                  color: 'var(--danger-soft)',
                  margin: 0,
                  lineHeight: 1.4,
                }}
              >
                {error}
              </p>
            </div>
          )}
        </div>

        <button
          type="submit"
          className="df-btn df-btn--primary"
          disabled={checking || !url.trim()}
          style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 'var(--sp-2)' }}
        >
          {checking && (
            <Loader2
              size={16}
              style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}
            />
          )}
          {checking ? 'Checking…' : 'Connect'}
        </button>
      </form>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
