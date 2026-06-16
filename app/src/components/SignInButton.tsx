import { useState } from 'react'
import { startLogin } from '../lib/auth'

interface SignInButtonProps {
  authPending: boolean
}

export function SignInButton({ authPending }: SignInButtonProps) {
  const [signingIn, setSigningIn] = useState(false)

  async function handleClick() {
    setSigningIn(true)
    try {
      await startLogin()
    } catch {
      // startLogin throws if the IPC call fails; auth state machine handles the rest
    } finally {
      setSigningIn(false)
    }
  }

  const busy = signingIn || authPending

  return (
    <button
      onClick={() => void handleClick()}
      disabled={busy}
      className="df-btn df-btn--primary"
    >
      {busy ? 'Opening browser…' : 'Sign in'}
    </button>
  )
}
