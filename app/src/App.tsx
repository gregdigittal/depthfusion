import { useEffect, useState } from 'react'
import { getAppInfo, type AppInfo } from './lib/ipc'
import { SettingsPage } from './SettingsPage'
import {
  getAuthState,
  onAuthStateChange,
  startLogin,
  logout,
  type AuthState,
} from './lib/auth'

type Route = 'home' | 'settings'

function App() {
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null)
  const [route, setRoute] = useState<Route>('home')
  const [authState, setAuthState] = useState<AuthState>(getAuthState)
  const [signingIn, setSigningIn] = useState(false)

  useEffect(() => {
    getAppInfo().then(setAppInfo).catch(console.error)
  }, [])

  // Subscribe to auth state transitions (deep-link callback drives this)
  useEffect(() => {
    return onAuthStateChange(setAuthState)
  }, [])

  async function handleSignIn() {
    setSigningIn(true)
    try {
      await startLogin()
      // State is driven by deep-link → onAuthStateChange; no need to poll here
    } catch {
      // error state is set by startLogin() → setState internally
    } finally {
      setSigningIn(false)
    }
  }

  async function handleSignOut() {
    await logout(() => setRoute('home'))
  }

  if (route === 'settings') {
    return <SettingsPage onBack={() => setRoute('home')} />
  }

  // ── Unauthenticated shell ────────────────────────────────────────────────
  if (authState.status !== 'authenticated') {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col items-center justify-center gap-6 px-6">
        <div className="w-16 h-16 bg-indigo-500 rounded-2xl flex items-center justify-center font-bold text-white text-2xl">
          DF
        </div>
        <h1 className="text-3xl font-bold tracking-tight">DepthFusion</h1>
        <p className="text-gray-400 text-sm text-center max-w-xs">
          AI-powered context retrieval and knowledge management.
          Sign in with your identity provider to continue.
        </p>

        {authState.status === 'error' && (
          <p className="text-red-400 text-sm" role="alert">
            {authState.error ?? 'Authentication failed. Please try again.'}
          </p>
        )}

        <button
          onClick={handleSignIn}
          disabled={signingIn || authState.status === 'pending'}
          className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors"
        >
          {signingIn || authState.status === 'pending'
            ? 'Opening browser…'
            : 'Sign in'}
        </button>

        {appInfo && (
          <span className="text-xs text-gray-600 absolute bottom-4">
            v{appInfo.version}
          </span>
        )}
      </div>
    )
  }

  // ── Authenticated shell ──────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-3">
        <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center font-bold text-white text-sm">
          DF
        </div>
        <h1 className="text-xl font-semibold tracking-tight">DepthFusion</h1>
        {appInfo && (
          <span className="text-xs text-gray-500 ml-auto">v{appInfo.version}</span>
        )}
        <button
          onClick={() => setRoute('settings')}
          className="text-gray-400 hover:text-gray-200 transition-colors p-1 rounded"
          aria-label="Settings"
          title="Settings"
        >
          ⚙
        </button>
        <button
          onClick={handleSignOut}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors ml-1"
        >
          Sign out
        </button>
      </header>

      {/* Main content */}
      <main className="flex-1 flex items-center justify-center px-6">
        <div className="text-center max-w-md">
          <p className="text-4xl mb-4">🔬</p>
          <h2 className="text-2xl font-bold mb-2">DepthFusion Desktop</h2>
          <p className="text-gray-400 text-sm">
            AI-powered context retrieval and knowledge management.
            Built with Tauri 2, React, and Tailwind CSS.
          </p>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 px-6 py-3 text-xs text-gray-600 text-center">
        DepthFusion — Tauri 2 + React + Tailwind CSS
      </footer>
    </div>
  )
}

export default App
