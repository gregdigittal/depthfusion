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

  useEffect(() => {
    let cancelled = false

    async function fetchStats() {
      try {
        const [serverUrl, tokens] = await Promise.all([getServerUrl(), loadTokens()])
        if (cancelled) return
        const resp = await fetch(`${serverUrl}/api/v1/stats`, {
          headers: tokens ? { 'Authorization': `Bearer ${tokens.access_token}` } : {},
        })
        if (!resp.ok) return
        const json = await resp.json() as StatsData
        if (!cancelled) setData(json)
      } catch {
        // stats are best-effort — silently degrade
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void fetchStats()
    const interval = setInterval(() => void fetchStats(), 60_000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  return { data, loading }
}
