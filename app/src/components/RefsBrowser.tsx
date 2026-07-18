import { useState } from 'react'
import { type BridgeResponse, fetchBridgeContent } from '../lib/ipc'

interface RefsBrowserProps {
  refsCount: number
}

export function RefsBrowser({ refsCount }: RefsBrowserProps) {
  const [nodeId, setNodeId] = useState('')
  const [result, setResult] = useState<BridgeResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleFetch = async () => {
    const trimmed = nodeId.trim()
    if (!trimmed) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await fetchBridgeContent(trimmed)
      if (data.error) {
        setError(data.error)
      } else {
        setResult(data)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="df-refs-browser">
      <p className="df-refs-browser__count">
        {refsCount} offloaded ref{refsCount !== 1 ? 's' : ''} stored
      </p>
      <div className="df-refs-browser__input-row">
        <input
          className="df-refs-browser__input"
          type="text"
          placeholder="node_id (hex hash)"
          value={nodeId}
          onChange={(e) => setNodeId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleFetch()}
        />
        <button
          className="df-refs-browser__btn"
          onClick={handleFetch}
          disabled={loading || !nodeId.trim()}
        >
          {loading ? 'Fetching…' : 'Fetch'}
        </button>
      </div>
      {error && <p className="df-cognitive__error">{error}</p>}
      {result && (
        <pre className="df-refs-browser__content">{result.text}</pre>
      )}
      {refsCount === 0 && !result && (
        <p className="df-cognitive__empty">
          No refs yet. Context offloading activates when sessions grow large.
        </p>
      )}
    </div>
  )
}
