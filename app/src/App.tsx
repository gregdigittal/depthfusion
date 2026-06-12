import { useEffect, useState } from 'react'
import { getAppInfo, type AppInfo } from './lib/ipc'
import { SettingsPage } from './SettingsPage'
import { SearchPage } from './SearchPage'
import { DocumentViewer } from './DocumentViewer'
import { GraphPage } from './GraphPage'
import { DashboardPage } from './DashboardPage'

type Route =
  | 'home'
  | 'settings'
  | 'search'
  | 'document-viewer'
  | 'graph'
  | 'dashboard'

function App() {
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null)
  const [route, setRoute] = useState<Route>('home')
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null)

  useEffect(() => {
    getAppInfo().then(setAppInfo).catch(console.error)
  }, [])

  function openDocument(id: string) {
    setOpenDocumentId(id)
    setRoute('document-viewer')
  }

  if (route === 'settings') {
    return <SettingsPage onBack={() => setRoute('home')} />
  }

  const navItems: { route: Route; icon: string; label: string }[] = [
    { route: 'search', icon: '🔍', label: 'Search' },
    { route: 'graph', icon: '🕸', label: 'Graph' },
    { route: 'dashboard', icon: '📊', label: 'Dashboard' },
  ]

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-3 flex items-center gap-3 shrink-0">
        <button
          onClick={() => setRoute('home')}
          className="flex items-center gap-2 hover:opacity-80 transition-opacity"
          aria-label="Home"
        >
          <div className="w-7 h-7 bg-indigo-500 rounded-lg flex items-center justify-center font-bold text-white text-xs">
            DF
          </div>
          <h1 className="text-lg font-semibold tracking-tight hidden sm:block">DepthFusion</h1>
        </button>

        {/* Nav */}
        <nav className="flex items-center gap-1 ml-4">
          {navItems.map((item) => (
            <button
              key={item.route}
              onClick={() => setRoute(item.route)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                route === item.route
                  ? 'bg-indigo-500/20 text-indigo-300'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
              }`}
              aria-current={route === item.route ? 'page' : undefined}
            >
              <span>{item.icon}</span>
              <span className="hidden sm:block">{item.label}</span>
            </button>
          ))}
        </nav>

        {/* Right side */}
        <div className="ml-auto flex items-center gap-2">
          {appInfo && (
            <span className="text-xs text-gray-600 hidden sm:block">v{appInfo.version}</span>
          )}
          <button
            onClick={() => setRoute('settings')}
            className="text-gray-400 hover:text-gray-200 transition-colors p-1.5 rounded-lg hover:bg-gray-800"
            aria-label="Settings"
            title="Settings"
          >
            ⚙
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {route === 'home' && (
          <div className="flex-1 flex items-center justify-center px-6">
            <div className="text-center max-w-md">
              <p className="text-4xl mb-4">🔬</p>
              <h2 className="text-2xl font-bold mb-2">DepthFusion Desktop</h2>
              <p className="text-gray-400 text-sm mb-8">
                AI-powered context retrieval and knowledge management.
              </p>
              <div className="grid grid-cols-3 gap-3">
                {navItems.map((item) => (
                  <button
                    key={item.route}
                    onClick={() => setRoute(item.route)}
                    className="flex flex-col items-center gap-2 p-4 rounded-xl bg-gray-900 border border-gray-800 hover:border-indigo-500/50 hover:bg-gray-800 transition-colors"
                  >
                    <span className="text-2xl">{item.icon}</span>
                    <span className="text-sm text-gray-300">{item.label}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {route === 'search' && (
          <SearchPage onOpenDocument={openDocument} />
        )}

        {route === 'document-viewer' && (
          <DocumentViewer
            documentId={openDocumentId}
            onClose={() => setRoute('search')}
          />
        )}

        {route === 'graph' && (
          <GraphPage onOpenDocument={openDocument} />
        )}

        {route === 'dashboard' && <DashboardPage />}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 px-6 py-2.5 text-xs text-gray-600 text-center shrink-0">
        DepthFusion — Tauri 2 + React + Tailwind CSS (E-57)
      </footer>
    </div>
  )
}

export default App
