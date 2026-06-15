import { useDashboard } from './hooks/useDashboard'
import { TileGrid } from './components/TileGrid'

function RecentActivity() {
  const activities = [
    { label: 'Searched "microservices patterns"', time: '2 min ago' },
    { label: 'Opened "Architecture ADR-003"', time: '14 min ago' },
    { label: 'Viewed graph: auth concepts', time: '1 hr ago' },
    { label: 'Downloaded "Q1 Report.pdf"', time: '3 hr ago' },
  ]
  return (
    <ul className="space-y-2.5">
      {activities.map((a, i) => (
        <li key={i} className="flex justify-between items-start gap-2">
          <span className="text-sm text-gray-300 leading-snug">{a.label}</span>
          <span className="text-xs text-gray-600 whitespace-nowrap shrink-0 pt-px">
            {a.time}
          </span>
        </li>
      ))}
    </ul>
  )
}

function SearchStats() {
  return (
    <div className="space-y-3">
      <div>
        <div className="text-2xl font-bold text-white">247</div>
        <div className="text-xs text-gray-500 mt-0.5">Queries this week</div>
      </div>
      <div>
        <div className="text-lg font-semibold text-indigo-400">142ms</div>
        <div className="text-xs text-gray-500 mt-0.5">Avg. latency</div>
      </div>
    </div>
  )
}

function StorageUsage() {
  const usedGb = 4.2
  const totalGb = 20
  const pct = Math.round((usedGb / totalGb) * 100)
  return (
    <div className="space-y-3">
      <div>
        <div className="text-2xl font-bold text-white">{usedGb} GB</div>
        <div className="text-xs text-gray-500 mt-0.5">of {totalGb} GB used</div>
      </div>
      <div>
        <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="text-xs text-gray-600 mt-1">{pct}% used</div>
      </div>
    </div>
  )
}

function SyncStatus() {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
        <span className="text-sm text-gray-300">Synced</span>
      </div>
      <div className="text-xs text-gray-500">Last sync: 3 min ago</div>
      <div className="text-xs text-gray-600">1,204 documents indexed</div>
    </div>
  )
}

const TILE_CONTENT: Record<string, React.ReactNode> = {
  'recent-activity': <RecentActivity />,
  'search-stats': <SearchStats />,
  'storage-usage': <StorageUsage />,
  'sync-status': <SyncStatus />,
}

export function DashboardPage() {
  const { tiles } = useDashboard()

  return (
    <div className="overflow-y-auto h-full">
      <div className="px-6 py-4 border-b border-gray-800">
        <h1 className="text-base font-semibold text-white">Dashboard</h1>
      </div>
      <TileGrid tiles={tiles}>
        {tiles.map((t) => TILE_CONTENT[t.id] ?? null)}
      </TileGrid>
    </div>
  )
}
