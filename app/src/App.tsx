import { useEffect, useState } from 'react'
import { getAppInfo, getWizardCompleted, setWizardCompleted, type AppInfo } from './lib/ipc'
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
import { SetupWizardPage } from './wizard/SetupWizardPage'
import { LayoutDashboard, Search, GitFork, Settings } from 'lucide-react'

type Route = 'dashboard' | 'search' | 'graph' | 'settings'

const NAV_TABS: { id: Route; label: string; Icon: React.ComponentType<{ size?: number }> }[] = [
  { id: 'dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { id: 'search',    label: 'Search',    Icon: Search },
  { id: 'graph',     label: 'Graph',     Icon: GitFork },
]

function App() {
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null)
  const [route, setRoute] = useState<Route>('dashboard')
  const [authState, setAuthState] = useState<AuthState>(getAuthState)
  const [openDocId, setOpenDocId] = useState<string | null>(null)
  // null = loading, true = show wizard, false = wizard already done
  const [wizardNeeded, setWizardNeeded] = useState<boolean | null>(null)

  useEffect(() => {
    getAppInfo().then(setAppInfo).catch(console.error)
  }, [])

  // Determine whether the first-run wizard is needed before rendering anything
  useEffect(() => {
    getWizardCompleted()
      .then((completed) => setWizardNeeded(!completed))
      .catch(() => setWizardNeeded(false)) // on error default to no wizard
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

  // ── Loading gate (wizard check in flight) ───────────────────────────────
  if (wizardNeeded === null) {
    return (
      <div className="df df-auth" style={{ minHeight: '100vh' }}>
        <LogoMark size={48} animation="breathe pulse" />
      </div>
    )
  }

  // ── First-run setup wizard ───────────────────────────────────────────────
  if (wizardNeeded) {
    return (
      <SetupWizardPage
        onComplete={async () => {
          await setWizardCompleted(true).catch(console.error)
          setWizardNeeded(false)
        }}
      />
    )
  }

  // ── Unauthenticated shell ────────────────────────────────────────────────
  if (authState.status !== 'authenticated') {
    return (
      <div className="df df-auth" style={{ minHeight: '100vh' }}>
        <LogoMark size={64} animation="breathe pulse" />
        <h1 className="df-auth__title">DepthFusion</h1>
        <p className="df-auth__desc">
          AI-powered context retrieval and knowledge management.
          Sign in with your identity provider to continue.
        </p>

        {authState.status === 'error' && (
          <p style={{ color: 'var(--danger)', fontSize: 'var(--fs-body)' }} role="alert">
            {authState.error ?? 'Authentication failed. Please try again.'}
          </p>
        )}

        <SignInButton authPending={authState.status === 'pending'} />

        {appInfo && (
          <span className="df-auth__version">v{appInfo.version}</span>
        )}
      </div>
    )
  }

  // ── Authenticated shell ──────────────────────────────────────────────────
  return (
    <div className="df df-window">
      {/* Title bar */}
      <div className="df-titlebar">
        <div className="df-titlebar__dots">
          <span className="df-titlebar__dot df-titlebar__dot--close" />
          <span className="df-titlebar__dot df-titlebar__dot--min" />
          <span className="df-titlebar__dot df-titlebar__dot--expand" />
        </div>
        <span className="df-titlebar__title">DepthFusion</span>
      </div>

      {/* Header */}
      <header className="df-header">
        <div className="df-brand">
          <LogoMark size={23} flat animation="breathe" />
          <span className="df-brand__name">DepthFusion</span>
        </div>

        {/* Nav tabs */}
        <nav className="df-tabs">
          {NAV_TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              onClick={() => setRoute(id)}
              className={`df-tab${route === id ? ' df-tab--active' : ''}`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </nav>

        <div className="df-header__right">
          {appInfo && (
            <span className="df-header__version">v{appInfo.version}</span>
          )}
          <button
            onClick={() => setRoute('settings')}
            className={`df-iconbtn${route === 'settings' ? ' df-tab--active' : ''}`}
            aria-label="Settings"
            title="Settings"
          >
            <Settings size={16} />
          </button>
          <button
            onClick={() => void handleSignOut()}
            className="df-signout"
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
