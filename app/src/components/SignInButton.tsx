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
      className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors"
    >
      {busy ? 'Opening browser…' : 'Sign in'}
    </button>
  )
}
