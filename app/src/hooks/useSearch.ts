import { useState, useEffect, useRef, useCallback } from 'react'
import type { SearchResult } from '../components/ResultCard'
import { loadTokens } from '../lib/ipc'

interface SearchResponse {
  results: SearchResult[]
}

interface UseSearchReturn {
  query: string
  setQuery: (q: string) => void
  results: SearchResult[]
  isLoading: boolean
  error: string | null
  latencyMs: number | null
}

const CACHE_MAX = 20

// LRU-style eviction: Map preserves insertion order; evict oldest when over limit
function putCache(
  cache: Map<string, SearchResult[]>,
  key: string,
  value: SearchResult[]
): Map<string, SearchResult[]> {
  const next = new Map(cache)
  if (next.has(key)) next.delete(key) // move to end
  if (next.size >= CACHE_MAX) {
    const oldest = next.keys().next().value
    if (oldest !== undefined) next.delete(oldest)
  }
  next.set(key, value)
  return next
}

export function useSearch(serverUrl: string): UseSearchReturn {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)
  const [token, setToken] = useState<string | null>(null)

  useEffect(() => {
    loadTokens().then((ts) => setToken(ts?.access_token ?? null)).catch(console.error)
  }, [])

  const cacheRef = useRef<Map<string, SearchResult[]>>(new Map())
  const abortRef = useRef<AbortController | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const executeSearch = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        setResults([])
        setIsLoading(false)
        setError(null)
        setLatencyMs(null)
        return
      }

      // Cache hit
      const cached = cacheRef.current.get(q)
      if (cached) {
        setResults(cached)
        setIsLoading(false)
        setError(null)
        return
      }

      // Cancel any in-flight request
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setIsLoading(true)
      setError(null)

      const t0 = performance.now()
      try {
        const resp = await fetch(`${serverUrl}/api/v1/search`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ q, limit: 20 }),
          signal: controller.signal,
        })

        if (!resp.ok) {
          throw new Error(`Search failed: ${resp.status} ${resp.statusText}`)
        }

        const data: SearchResponse = await resp.json() as SearchResponse
        const elapsed = performance.now() - t0

        cacheRef.current = putCache(cacheRef.current, q, data.results)
        setResults(data.results)
        setLatencyMs(Math.round(elapsed))
        setError(null)
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // Silently ignore cancelled requests
          return
        }
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        setResults([])
      } finally {
        setIsLoading(false)
      }
    },
    [serverUrl, token]
  )

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      void executeSearch(query)
    }, 300)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, executeSearch])

  // Abort any in-flight request on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  return { query, setQuery, results, isLoading, error, latencyMs }
}
