import type { ReactNode } from 'react'
import { useDashboard } from './hooks/useDashboard'
import { useStats } from './hooks/useStats'
import type { StatsData } from './hooks/useStats'
import { useCognitiveStatus } from './hooks/useCognitiveStatus'
import { useRecentSessions } from './hooks/useRecentSessions'
import type { SessionEvent } from './hooks/useRecentSessions'
import { useStorageUsage } from './hooks/useStorageUsage'
import { TileGrid } from './components/TileGrid'
import { SkeletonRow, TileError, TileLoading } from './components/TilePrimitives'
import { CheckpointTimeline } from './components/CheckpointTimeline'

// ---------------------------------------------------------------------------
// RecentActivity — S-247: fetches /query/sessions?limit=10
// ---------------------------------------------------------------------------

function formatRelativeTime(timestamp: string): string {
  try {
    const diff = Date.now() - new Date(timestamp).getTime()
    const mins = Math.floor(diff / 60_000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins} min ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs} hr ago`
    return `${Math.floor(hrs / 24)} d ago`
  } catch {
    return ''
  }
}

function formatSessionLabel(ev: SessionEvent): string {
  const mode = ev.mode ?? 'recall'
  return `${mode.charAt(0).toUpperCase()}${mode.slice(1)} session`
}

function RecentActivity() {
  const { data, loading, error, retry } = useRecentSessions(10)

  if (loading) {
    return (
      <div style={{ width: '100%' }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonRow key={i} />
        ))}
      </div>
    )
  }

  if (error) {
    return <TileError message={`Failed to load recent sessions: ${error}`} onRetry={retry} />
  }

  const items = data?.items ?? []

  if (items.length === 0) {
    return (
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>
        No recent sessions found.
      </div>
    )
  }

  return (
    <ul className="df-activity">
      {items.map((ev, i) => (
        <li key={i} className="df-activity__row">
          <span>{formatSessionLabel(ev)}</span>
          <span className="df-activity__time">{formatRelativeTime(ev.timestamp)}</span>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------
// SearchStats (unchanged, driven by useStats)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// StorageUsage — S-247: fetches /query/aggregate
// ---------------------------------------------------------------------------

function StorageUsage() {
  const { data, loading, error, retry } = useStorageUsage()

  if (loading) {
    // Identical output to the previous three inline <SkeletonRow /> calls.
    return <TileLoading rows={3} />
  }

  if (error) {
    return <TileError message={`Failed to load usage stats: ${error}`} onRetry={retry} />
  }

  const totalEvents = data?.total_events ?? 0
  const avgLatency = data?.avg_latency_ms ?? null
  const modes = data?.modes ?? {}
  const topMode = Object.entries(modes).sort((a, b) => b[1] - a[1])[0]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div>
        <div className="df-stat-big">{totalEvents.toLocaleString()}</div>
        <div className="df-stat-label">Total recall events</div>
      </div>
      {avgLatency !== null && (
        <div>
          <div className="df-stat-med">{Math.round(avgLatency)} ms</div>
          <div className="df-stat-label">Avg latency</div>
        </div>
      )}
      {topMode && (
        <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-micro)' }}>
          Top mode: {topMode[0]} ({topMode[1]})
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SyncStatus (unchanged, driven by useStats)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// CognitiveSummary (unchanged, driven by useCognitiveStatus)
// ---------------------------------------------------------------------------

function CognitiveSummary() {
  const { data, loading, error } = useCognitiveStatus()
  if (loading) return <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>Loading…</div>
  if (error || !data) return <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>—</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ color: 'var(--text)', fontSize: 'var(--fs-body)' }}>
        {data.persona.memory_count_at_last_generation ?? 0} memories
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>
        {data.offload.refs_count} offloaded refs
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-micro)' }}>
        Backend: {data.distillation.resolved_backend}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DashboardPage
// ---------------------------------------------------------------------------

export function DashboardPage() {
  const { tiles } = useDashboard()
  const { data: stats, error } = useStats()

  const tileContent: Record<string, ReactNode> = {
    'recent-activity': <RecentActivity />,
    'search-stats': <SearchStats stats={stats} error={error} />,
    'storage-usage': <StorageUsage />,
    'sync-status': <SyncStatus stats={stats} error={error} />,
    'cognitive-summary': <CognitiveSummary />,
    // Key must match the DEFAULT_TILES id in hooks/useDashboard.ts exactly.
    'checkpoint-timeline': <CheckpointTimeline />,
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <TileGrid tiles={tiles}>
        {tiles.map((t) => tileContent[t.id] ?? null)}
      </TileGrid>
    </div>
  )
}
