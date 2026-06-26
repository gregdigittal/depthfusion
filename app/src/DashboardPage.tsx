import { useDashboard } from './hooks/useDashboard'
import { useStats } from './hooks/useStats'
import type { StatsData } from './hooks/useStats'
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

function SearchStats({ stats, error }: { stats: StatsData | null; error: string | null }) {
  if (error) {
    return (
      <div style={{ color: 'var(--danger-soft)', fontSize: 'var(--fs-small)' }}>
        {error}
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div>
        <div className="df-stat-big">{stats ? stats.context_files.toLocaleString() : '—'}</div>
        <div className="df-stat-label">Files indexed</div>
      </div>
      <div>
        <div className="df-stat-med">{stats ? stats.project_count : '—'}</div>
        <div className="df-stat-label">Projects tracked</div>
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

function SyncStatus({ stats, error }: { stats: StatsData | null; error: string | null }) {
  if (error) {
    return (
      <div style={{ color: 'var(--danger-soft)', fontSize: 'var(--fs-small)' }}>
        {error}
      </div>
    )
  }
  const lastSync = stats?.last_synced
    ? new Date(stats.last_synced).toLocaleString()
    : '—'
  const docCount = stats ? stats.context_files.toLocaleString() : '—'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
        <span className="df-dot df-dot--ok df-dot--pip" />
        <span style={{ color: 'var(--text)', fontSize: 'var(--fs-body)' }}>Synced</span>
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>Last sync: {lastSync}</div>
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-micro)' }}>{docCount} files indexed</div>
    </div>
  )
}

export function DashboardPage() {
  const { tiles } = useDashboard()
  const { data: stats, error } = useStats()

  const tileContent: Record<string, JSX.Element> = {
    'recent-activity': <RecentActivity />,
    'search-stats': <SearchStats stats={stats} error={error} />,
    'storage-usage': <StorageUsage />,
    'sync-status': <SyncStatus stats={stats} error={error} />,
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <TileGrid tiles={tiles}>
        {tiles.map((t) => tileContent[t.id] ?? null)}
      </TileGrid>
    </div>
  )
}
