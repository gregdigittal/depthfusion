function App() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-3">
        <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center font-bold text-white text-sm">
          DF
        </div>
        <h1 className="text-xl font-semibold tracking-tight">DepthFusion</h1>
        <span className="text-xs text-gray-500 ml-auto">v2.0.0</span>
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
        DepthFusion — Tauri 2 + React + Tailwind CSS scaffold (S-180)
      </footer>
    </div>
  )
}

export default App
