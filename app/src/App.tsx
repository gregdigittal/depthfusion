import { useEffect, useState } from 'react'
import { getAppInfo, type AppInfo } from './lib/ipc'
import {
  getAuthState,
  onAuthStateChange,
  logout,
  pollAuthState,
  type AuthState,
} from './lib/auth'
import { LogoMark } from './components/LogoMark'
import { SignInButton } from './components/SignInButton'
import { DashboardPage } from './DashboardPage'
import { SearchPage } from './SearchPage'
import { GraphPage } from './GraphPage'
import { DocumentViewer } from './DocumentViewer'
import { SettingsPage } from './SettingsPage'

type Route = 'dashboard' | 'search' | 'graph' | 'settings'

const NAV_TABS: { id: Route; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'search', label: 'Search' },
  { id: 'graph', label: 'Graph' },
]

function App() {
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null)
  const [route, setRoute] = useState<Route>('dashboard')
  const [authState, setAuthState] = useState<AuthState>(getAuthState)
  const [openDocId, setOpenDocId] = useState<string | null>(null)

  useEffect(() => {
    getAppInfo().then(setAppInfo).catch(console.error)
  }, [])

  // Recover tokens already in vault (e.g. app restart after successful login)
  useEffect(() => {
    void pollAuthState(5_000).catch(() => {})
  }, [])

  // Subscribe to auth state transitions driven by deep-link callback
  useEffect(() => {
    return onAuthStateChange(setAuthState)
  }, [])

  async function handleSignOut() {
    await logout(() => setRoute('dashboard'))
  }

  // ── Unauthenticated shell ────────────────────────────────────────────────
  if (authState.status !== 'authenticated') {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col items-center justify-center gap-6 px-6">
        <LogoMark size={64} />
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

        <SignInButton authPending={authState.status === 'pending'} />

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
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="border-b border-gray-800 px-5 py-3 flex items-center gap-3 shrink-0">
        <LogoMark size={24} />
        <span className="text-base font-semibold tracking-tight">DepthFusion</span>

        {/* Nav tabs */}
        <nav className="flex items-center gap-1 ml-4">
          {NAV_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setRoute(tab.id)}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                route === tab.id
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-400 hover:text-white hover:bg-gray-800/50'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="flex items-center gap-2 ml-auto">
          {appInfo && (
            <span className="text-xs text-gray-600">v{appInfo.version}</span>
          )}
          <button
            onClick={() => setRoute('settings')}
            className={`text-gray-400 hover:text-gray-200 transition-colors p-1.5 rounded-md ${
              route === 'settings' ? 'text-white bg-gray-800' : ''
            }`}
            aria-label="Settings"
            title="Settings"
          >
            ⚙
          </button>
          <button
            onClick={() => void handleSignOut()}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors px-2 py-1 rounded-md hover:bg-gray-800/50"
          >
            Sign out
          </button>
        </div>
      </header>

      {/* Page content + optional DocumentViewer panel */}
      <div className="flex flex-1 overflow-hidden min-h-0">
        <main className="flex-1 overflow-hidden">
          {route === 'dashboard' && <DashboardPage />}
          {route === 'search' && (
            <SearchPage onOpenDocument={(id) => setOpenDocId(id)} />
          )}
          {route === 'graph' && (
            <GraphPage onOpenDocument={(id) => setOpenDocId(id)} />
          )}
          {route === 'settings' && (
            <SettingsPage onBack={() => setRoute('dashboard')} />
          )}
        </main>

        {/* Document viewer drawer */}
        {openDocId !== null && (
          <div className="w-[640px] shrink-0 overflow-hidden">
            <DocumentViewer
              documentId={openDocId}
              onClose={() => setOpenDocId(null)}
            />
          </div>
        )}
      </div>
    </div>
  )
}

export default App
