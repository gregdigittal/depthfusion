/**
 * OidcSignInScreen — OIDC sign-in step shared by the VPS and Connect flows
 * (E-65 / S-216).
 *
 * Calls `startLogin()` from auth.ts to open the system browser with a PKCE
 * OIDC URL. Subscribes to `onAuthStateChange` and advances to the success
 * screen as soon as the auth state becomes 'authenticated'. Also surfaces any
 * auth errors inline so the user can retry.
 */

import { useEffect, useState } from 'react'
import { LogIn, Loader2, AlertCircle } from 'lucide-react'
import { startLogin, onAuthStateChange } from '../lib/auth'

interface OidcSignInScreenProps {
  /** Called when the user has authenticated successfully. */
  onNext: () => void
}

type SignInState = 'idle' | 'pending' | 'error'

export function OidcSignInScreen({ onNext }: OidcSignInScreenProps) {
  const [signInState, setSignInState] = useState<SignInState>('idle')
  const [error, setError] = useState<string | null>(null)

  // Subscribe to auth state changes on mount; unsubscribe on unmount.
  useEffect(() => {
    const unsubscribe = onAuthStateChange((state) => {
      if (state.status === 'authenticated') {
        unsubscribe()
        onNext()
      } else if (state.status === 'error') {
        setSignInState('error')
        setError(state.error ?? 'Authentication failed. Please try again.')
      } else if (state.status === 'pending') {
        setSignInState('pending')
        setError(null)
      }
    })

    return () => {
      unsubscribe()
    }
  }, [onNext])

  async function handleSignIn() {
    setError(null)
    setSignInState('pending')
    try {
      await startLogin()
      // The auth state listener above will fire when the callback completes.
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setSignInState('error')
      setError(msg || 'Failed to open the sign-in window. Please try again.')
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
        alignItems: 'center',
        textAlign: 'center',
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
          Sign in to your server
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
          {signInState === 'pending'
            ? 'Your browser opened. Complete sign-in there, then return here.'
            : 'Click the button below to open a sign-in window in your browser.'}
        </p>
      </div>

      {/* Status / error feedback */}
      {signInState === 'pending' && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--sp-3)',
            padding: 'var(--sp-4)',
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-lg)',
            width: '100%',
          }}
        >
          <Loader2
            size={18}
            style={{
              color: 'var(--muted)',
              animation: 'spin 1s linear infinite',
              flexShrink: 0,
            }}
          />
          <span style={{ fontSize: 'var(--fs-body)', color: 'var(--muted)' }}>
            Waiting for authentication…
          </span>
        </div>
      )}

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
            width: '100%',
            textAlign: 'left',
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

      <button
        className="df-btn df-btn--primary"
        onClick={() => void handleSignIn()}
        disabled={signInState === 'pending'}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 'var(--sp-2)',
        }}
      >
        <LogIn size={16} />
        {signInState === 'pending' ? 'Sign-in in progress…' : 'Sign in'}
      </button>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
