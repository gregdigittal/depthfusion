/**
 * S-247 — useStorageUsage: fetch aggregate recall stats from /query/aggregate.
 *
 * Mirrors the pattern from useStats.ts / useCognitiveStatus.ts.
 */
import { useEffect, useState } from 'react'
import { getServerUrl, loadTokens } from '../lib/ipc'

export interface AggregateData {
  total_events: number
  total_latency_ms: number
  avg_latency_ms: number | null
  p95_latency_ms: number | null
  avg_result_count: number | null
  modes: Record<string, number>
  config_versions: Record<string, number>
}

interface UseStorageUsageResult {
  data: AggregateData | null
  loading: boolean
  error: string | null
  retry: () => void
}

export function useStorageUsage(): UseStorageUsageResult {
  const [data, setData] = useState<AggregateData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  const retry = () => setRetryCount((n) => n + 1)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    async function fetchAggregate() {
      try {
        const [serverUrl, tokens] = await Promise.all([getServerUrl(), loadTokens()])
        if (cancelled) return
        const resp = await fetch(`${serverUrl}/query/aggregate`, {
          headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {},
        })
        if (!resp.ok) {
          if (!cancelled) setError(`Aggregate fetch failed: ${resp.status}`)
          return
        }
        const json = (await resp.json()) as AggregateData
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

    void fetchAggregate()
    const interval = setInterval(() => void fetchAggregate(), 60_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [retryCount])

  return { data, loading, error, retry }
}
