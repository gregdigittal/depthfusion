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
    <ul className="df-activity">
      {activities.map((a, i) => (
        <li key={i} className="df-activity__row">
          <span>{a.label}</span>
          <span className="df-activity__time">{a.time}</span>
        </li>
      ))}
    </ul>
  )
}

function SearchStats() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div>
        <div className="df-stat-big">247</div>
        <div className="df-stat-label">Queries this week</div>
      </div>
      <div>
        <div className="df-stat-med">142ms</div>
        <div className="df-stat-label">Avg. latency</div>
      </div>
    </div>
  )
}

function StorageUsage() {
  const usedGb = 4.2
  const totalGb = 20
  const pct = Math.round((usedGb / totalGb) * 100)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div>
        <div className="df-stat-big">{usedGb} GB</div>
        <div className="df-stat-label">of {totalGb} GB used</div>
      </div>
      <div>
        <div className="df-progress">
          <div className="df-progress__fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="df-stat-label" style={{ marginTop: 'var(--sp-1)' }}>{pct}% used</div>
      </div>
    </div>
  )
}

function SyncStatus() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
        <span className="df-dot df-dot--ok df-dot--pip" />
        <span style={{ color: 'var(--text)', fontSize: 'var(--fs-body)' }}>Synced</span>
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>Last sync: 3 min ago</div>
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-micro)' }}>1,204 documents indexed</div>
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
    <div className="flex-1 overflow-y-auto">
      <TileGrid tiles={tiles}>
        {tiles.map((t) => TILE_CONTENT[t.id] ?? null)}
      </TileGrid>
    </div>
  )
}
