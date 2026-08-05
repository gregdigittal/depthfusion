/**
 * S-247 — useRecentSessions: fetch recent recall sessions from /query/sessions.
 *
 * Mirrors the pattern from useStats.ts / useCognitiveStatus.ts.
 */
import { useEffect, useState } from 'react'
import { getServerUrl, loadTokens } from '../lib/ipc'

export interface SessionEvent {
  timestamp: string
  mode: string
  config_version_id?: string
  total_latency_ms?: number
  result_count?: number
}

export interface RecentSessionsData {
  items: SessionEvent[]
  total: number
  count: number
  next_cursor: string | null
}

interface UseRecentSessionsResult {
  data: RecentSessionsData | null
  loading: boolean
  error: string | null
  retry: () => void
}

export function useRecentSessions(limit = 10): UseRecentSessionsResult {
  const [data, setData] = useState<RecentSessionsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  const retry = () => setRetryCount((n) => n + 1)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    async function fetchSessions() {
      try {
        const [serverUrl, tokens] = await Promise.all([getServerUrl(), loadTokens()])
        if (cancelled) return
        const resp = await fetch(`${serverUrl}/query/sessions?limit=${limit}`, {
          headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {},
        })
        if (!resp.ok) {
          if (!cancelled) setError(`Sessions fetch failed: ${resp.status}`)
          return
        }
        const json = (await resp.json()) as RecentSessionsData
        if (!cancelled) {
          setData(json)
          setError(null)
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void fetchSessions()
    const interval = setInterval(() => void fetchSessions(), 60_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [limit, retryCount])

  return { data, loading, error, retry }
}
