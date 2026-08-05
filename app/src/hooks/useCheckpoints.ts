/**
 * S-253 T-861 — useCheckpoints: fetch session checkpoints from /query/checkpoints.
 *
 * Mirrors the useRecentSessions.ts idiom exactly: data/loading/error/retryCount
 * state, a cancelled-flag useEffect, `e instanceof Error` catch narrowing, a
 * 60_000ms setInterval refresh torn down with clearInterval, and
 * [project, limit, retryCount] deps.
 *
 * Two deliberate divergences from useRecentSessions:
 *   1. `project` defaults to '' (interview answer 3). DashboardPage has no
 *      project selector and no project context today, so the tile asks for
 *      "all projects" until one exists. That default is only meaningful because
 *      the server honours it: an empty/absent `project` makes
 *      `core.event_store.list_checkpoints` scan every project subdirectory of
 *      the checkpoint root and merge the results newest-first. If that server
 *      contract ever narrows back to per-project-only, this default must change
 *      with it or the tile silently renders an empty timeline.
 *   2. The hook does NOT Promise.all `loadTokens()` alongside `getServerUrl()`.
 *      `fetchCheckpoints` in ../lib/checkpoints owns token loading and URL
 *      construction, so duplicating either here would be a second source of
 *      truth for the request shape.
 */
import { useEffect, useState } from 'react'
import { getServerUrl } from '../lib/ipc'
import { fetchCheckpoints } from '../lib/checkpoints'
import type { CheckpointRecord } from '../lib/checkpoints'

interface UseCheckpointsResult {
  checkpoints: CheckpointRecord[]
  loading: boolean
  error: string | null
  retry: () => void
}

export function useCheckpoints(project = '', limit = 20): UseCheckpointsResult {
  const [checkpoints, setCheckpoints] = useState<CheckpointRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  const retry = () => setRetryCount((n) => n + 1)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    async function loadCheckpoints() {
      try {
        const serverUrl = await getServerUrl()
        if (cancelled) return
        const items = await fetchCheckpoints(serverUrl, project, limit)
        if (!cancelled) {
          setCheckpoints(items)
          setError(null)
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadCheckpoints()
    const interval = setInterval(() => void loadCheckpoints(), 60_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [project, limit, retryCount])

  return { checkpoints, loading, error, retry }
}
