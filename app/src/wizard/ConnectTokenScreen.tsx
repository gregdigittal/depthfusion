/**
 * ConnectTokenScreen — step 2 of the Connect setup flow (S-218).
 *
 * Collects the static bearer token issued by the DepthFusion VPS admin.
 * Calls `setupConnectAuth` to store the token in the keychain, set
 * deployment_mode='connect', and mark the wizard complete. Then advances
 * to the success screen via `onNext`.
 */

import { useState } from 'react'
import { Key } from 'lucide-react'
import { setupConnectAuth } from '../lib/ipc'

interface ConnectTokenScreenProps {
  onNext: () => void
}

export function ConnectTokenScreen({ onNext }: ConnectTokenScreenProps) {
  const [token, setToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (!token.trim()) {
      setError('Bearer token must not be empty.')
      return
    }

    setSubmitting(true)
    try {
      await setupConnectAuth(token.trim())
      onNext()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg || 'Failed to save token. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="df-emerge"
      style={{
        width: '100%',
        maxWidth: 440,
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
          Enter your access token
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
          Paste the bearer token provided by your DepthFusion server admin.
          It is stored securely in the macOS Keychain and never leaves your device.
        </p>
      </div>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}
      >
        <div
          className="df-card__field"
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}
        >
          <label
            htmlFor="bearer-token-input"
            style={{
              fontSize: 'var(--fs-label)',
              fontWeight: 'var(--fw-medium)',
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            Bearer Token
          </label>

          <div style={{ position: 'relative' }}>
            <span
              style={{
                position: 'absolute',
                left: 'var(--sp-3)',
                top: '50%',
                transform: 'translateY(-50%)',
                color: 'var(--muted)',
                pointerEvents: 'none',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <Key size={14} />
            </span>
            <input
              id="bearer-token-input"
              type="password"
              className="df-input df-input--icon"
              placeholder="Paste token…"
              value={token}
              onChange={(e) => {
                setToken(e.target.value)
                if (error) setError(null)
              }}
              disabled={submitting}
              autoComplete="off"
              spellCheck={false}
              style={{ width: '100%' }}
            />
          </div>

          {error && (
            <p
              role="alert"
              style={{
                fontSize: 'var(--fs-body)',
                color: 'var(--danger-soft)',
                margin: 0,
              }}
            >
              {error}
            </p>
          )}
        </div>

        <button
          type="submit"
          className="df-btn df-btn--primary"
          disabled={!token.trim() || submitting}
          style={{ width: '100%' }}
        >
          {submitting ? 'Saving…' : 'Save and continue'}
        </button>
      </form>
    </div>
  )
}
