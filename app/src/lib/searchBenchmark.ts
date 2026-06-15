export interface BenchmarkResult {
  query: string
  latencyMs: number
  resultCount: number
  error?: string
}

interface SearchResponse {
  results: unknown[]
}

/** Run each query sequentially against the server and collect latency metrics. */
export async function runSearchBenchmark(
  serverUrl: string,
  queries: string[]
): Promise<BenchmarkResult[]> {
  const results: BenchmarkResult[] = []

  for (const query of queries) {
    const t0 = performance.now()
    try {
      const resp = await fetch(`${serverUrl}/api/v1/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q: query, limit: 20 }),
      })

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} ${resp.statusText}`)
      }

      const data: SearchResponse = await resp.json() as SearchResponse
      const latencyMs = Math.round(performance.now() - t0)

      results.push({
        query,
        latencyMs,
        resultCount: data.results.length,
      })
    } catch (err: unknown) {
      const latencyMs = Math.round(performance.now() - t0)
      const error = err instanceof Error ? err.message : String(err)
      results.push({ query, latencyMs, resultCount: 0, error })
    }
  }

  return results
}

/** Compute p50 / p95 / p99 latency percentiles and error rate from benchmark results. */
export function summarizeBenchmark(results: BenchmarkResult[]): {
  p50: number
  p95: number
  p99: number
  errorRate: number
} {
  if (results.length === 0) {
    return { p50: 0, p95: 0, p99: 0, errorRate: 0 }
  }

  const errorCount = results.filter((r) => r.error !== undefined).length
  const errorRate = errorCount / results.length

  const latencies = [...results].map((r) => r.latencyMs).sort((a, b) => a - b)

  function percentile(sorted: number[], p: number): number {
    if (sorted.length === 0) return 0
    const idx = Math.ceil((p / 100) * sorted.length) - 1
    return sorted[Math.max(0, Math.min(idx, sorted.length - 1))]
  }

  return {
    p50: percentile(latencies, 50),
    p95: percentile(latencies, 95),
    p99: percentile(latencies, 99),
    errorRate,
  }
}
