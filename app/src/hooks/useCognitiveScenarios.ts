import { useEffect, useState } from 'react'
import { type CognitiveScenariosResponse, getCognitiveScenarios } from '../lib/ipc'

interface UseCognitiveScenariosResult {
  data: CognitiveScenariosResponse | null
  loading: boolean
  error: string | null
}

export function useCognitiveScenarios(): UseCognitiveScenariosResult {
  const [data, setData] = useState<CognitiveScenariosResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const result = await getCognitiveScenarios()
        if (!cancelled) {
          setData(result)
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
