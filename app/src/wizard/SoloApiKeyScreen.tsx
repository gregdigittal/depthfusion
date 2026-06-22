/**
 * SoloApiKeyScreen — step 3 of the Solo setup flow (E-65 / S-215).
 *
 * Collects the user's Anthropic API key. Enforces the `sk-ant-` prefix client-side
 * before calling `setupSoloAuth`. On success, calls `onNext` to advance to the
 * success screen. The key is always rendered as a password field.
 */

import { useState } from 'react'
import { Key } from 'lucide-react'
import { setupSoloAuth } from '../lib/ipc'

const SK_ANT_PREFIX = 'sk-ant-'

interface SoloApiKeyScreenProps {
  onNext: () => void
}

export function SoloApiKeyScreen({ onNext }: SoloApiKeyScreenProps) {
  const [apiKey, setApiKey] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isValidPrefix = apiKey.startsWith(SK_ANT_PREFIX) && apiKey.length > SK_ANT_PREFIX.length

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (!isValidPrefix) {
      setError(`API key must start with "${SK_ANT_PREFIX}".`)
      return
    }

    setSubmitting(true)
    try {
      await setupSoloAuth(apiKey)
      onNext()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg || 'Failed to save API key. Please try again.')
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
          Add your Anthropic API key
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
          DepthFusion uses Claude for AI features in solo mode. Your key is
          stored securely in the macOS Keychain and never leaves your device.
        </p>
      </div>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}
      >
        {/* Key input */}
        <div
          className="df-card__field"
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}
        >
          <label
            htmlFor="api-key-input"
            style={{
              fontSize: 'var(--fs-label)',
              fontWeight: 'var(--fw-medium)',
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            Anthropic API Key
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
              id="api-key-input"
              type="password"
              className="df-input df-input--icon"
              placeholder="sk-ant-…"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value)
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

          <p
            style={{
              fontSize: 'var(--fs-label)',
              color: 'var(--faint)',
              margin: 0,
              lineHeight: 1.4,
            }}
          >
            Get your key at{' '}
            <a
              href="https://console.anthropic.com/account/keys"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent)' }}
            >
              console.anthropic.com
            </a>
          </p>
        </div>

        <button
          type="submit"
          className="df-btn df-btn--primary"
          disabled={!isValidPrefix || submitting}
          style={{ width: '100%' }}
        >
          {submitting ? 'Saving…' : 'Save and continue'}
        </button>
      </form>
    </div>
  )
}
