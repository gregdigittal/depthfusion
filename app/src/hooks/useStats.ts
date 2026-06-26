import { useEffect, useState } from 'react'
import { getServerUrl, loadTokens } from '../lib/ipc'

export interface StatsData {
  context_files: number
  projects: string[]
  project_count: number
  last_synced: string | null
}

export function useStats() {
  const [data, setData] = useState<StatsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchStats() {
      try {
        const [serverUrl, tokens] = await Promise.all([getServerUrl(), loadTokens()])
        console.log('[useStats] serverUrl:', serverUrl, 'hasToken:', !!tokens)
        if (cancelled) return
        const resp = await fetch(`${serverUrl}/api/v1/stats`, {
          headers: tokens ? { 'Authorization': `Bearer ${tokens.access_token}` } : {},
        })
        console.log('[useStats] response status:', resp.status)
        if (!resp.ok) {
          setError(`Stats fetch failed: ${resp.status}`)
          return
        }
        const json = await resp.json() as StatsData
        console.log('[useStats] data:', json)
        if (!cancelled) {
          setData(json)
          setError(null)
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error('[useStats] error:', msg)
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void fetchStats()
    const interval = setInterval(() => void fetchStats(), 60_000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  return { data, loading, error }
}
