import { useDashboard } from './hooks/useDashboard'
import { TileGrid } from './components/TileGrid'

// Tile content components

function RecentActivity() {
  const items = [
    { icon: '🔍', label: 'Searched "vector embeddings"', time: '2m ago' },
    { icon: '📄', label: 'Opened "Architecture Overview"', time: '15m ago' },
    { icon: '🔄', label: 'Synced 24 new documents', time: '1h ago' },
    { icon: '⚙️', label: 'Updated server URL', time: '2h ago' },
  ]
  return (
    <ul className="space-y-3">
      {items.map((item, i) => (
        <li key={i} className="flex items-center gap-3">
          <span className="text-base">{item.icon}</span>
          <span className="text-sm text-gray-300 flex-1 truncate">{item.label}</span>
          <span className="text-xs text-gray-600 shrink-0">{item.time}</span>
        </li>
      ))}
    </ul>
  )
}

function SearchStats() {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">Queries today</span>
        <span className="text-lg font-bold text-white">42</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">Avg latency</span>
        <span className="text-lg font-bold text-indigo-400">87ms</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">Cache hit rate</span>
        <span className="text-lg font-bold text-emerald-400">64%</span>
      </div>
    </div>
  )
}

function StorageUsage() {
  const usedPct = 38
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-400">3.8 GB used</span>
        <span className="text-gray-500">of 10 GB</span>
      </div>
      <div className="w-full h-2.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-500 rounded-full transition-all"
          style={{ width: `${usedPct}%` }}
        />
      </div>
      <p className="text-xs text-gray-600">{usedPct}% of quota used</p>
    </div>
  )
}

function SyncStatus() {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
        <span className="text-sm text-gray-300">Sync active</span>
      </div>
      <p className="text-xs text-gray-500">Last synced: just now</p>
      <p className="text-xs text-gray-500">1,284 documents indexed</p>
    </div>
  )
}

const TILE_CONTENTS = [
  <RecentActivity key="recent" />,
  <SearchStats key="stats" />,
  <StorageUsage key="storage" />,
  <SyncStatus key="sync" />,
]

export function DashboardPage() {
  const { tiles, resetToDefaults } = useDashboard()

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300">Dashboard</h2>
        <button
          onClick={resetToDefaults}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          Reset layout
        </button>
      </div>

      {/* Tiles */}
      <div className="flex-1 overflow-y-auto">
        <TileGrid tiles={tiles}>{TILE_CONTENTS}</TileGrid>
      </div>
    </div>
  )
}
