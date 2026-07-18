import { useEffect, useState } from 'react'
import { type CognitiveStatus, getCognitiveStatus } from '../lib/ipc'

interface UseCognitiveStatusResult {
  data: CognitiveStatus | null
  loading: boolean
  error: string | null
}

export function useCognitiveStatus(): UseCognitiveStatusResult {
  const [data, setData] = useState<CognitiveStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const status = await getCognitiveStatus()
        if (!cancelled) {
          setData(status)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    const id = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return { data, loading, error }
}
