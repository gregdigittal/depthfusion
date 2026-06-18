import { useEffect, useRef, useState, useCallback } from 'react'
import { GraphCanvas } from './components/GraphCanvas'
import { NodeInspector } from './components/NodeInspector'
import { getServerUrl } from './lib/ipc'

export interface NodeData {
  id: string
  label: string
  type: 'document' | 'concept' | 'decision'
  x: number
  y: number
}

export interface EdgeData {
  from: string
  to: string
  label?: string
}

interface GraphPageProps {
  onOpenDocument?: (id: string) => void
}

interface GraphResponse {
  nodes: NodeData[]
  edges: EdgeData[]
}

export function GraphPage({ onOpenDocument }: GraphPageProps) {
  const [serverUrl, setServerUrlState] = useState<string>('')
  useEffect(() => {
    getServerUrl().then(setServerUrlState).catch(console.error)
  }, [])
  const [nodes, setNodes] = useState<NodeData[]>([])
  const [edges, setEdges] = useState<EdgeData[]>([])
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [canvasSize, setCanvasSize] = useState({ width: 800, height: 600 })
  const containerRef = useRef<HTMLDivElement>(null)

  // Observe container resize
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (width > 0 && height > 0) {
          setCanvasSize({ width: Math.floor(width), height: Math.floor(height) })
        }
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Fetch graph data
  useEffect(() => {
    let cancelled = false
    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        const resp = await fetch(`${serverUrl}/api/v1/graph`)
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`)
        const data: GraphResponse = await resp.json() as GraphResponse
        if (!cancelled) {
          setNodes(data.nodes)
          setEdges(data.edges)
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
        }
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [serverUrl])

  const handleNodeClick = useCallback((node: NodeData) => {
    setSelectedNode((prev) => (prev?.id === node.id ? null : node))
  }, [])

  const handleCloseInspector = useCallback(() => setSelectedNode(null), [])

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="df-graphbar">
        <span style={{ color: 'var(--text)', fontSize: 'var(--fs-body)', fontWeight: 500 }}>
          Knowledge Graph
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)', color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>
          <span>{nodes.length} nodes</span>
          <span>{edges.length} edges</span>
          <span style={{ color: 'var(--border-strong)' }}>· scroll to zoom, drag to pan</span>
        </div>
      </div>

      {/* Main area */}
      <div className="df-graph-body">
        {/* Canvas area */}
        <div ref={containerRef} className="df-canvas">
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center" style={{ color: 'var(--muted)', fontSize: 'var(--fs-body)' }}>
              Loading graph…
            </div>
          )}
          {!isLoading && error && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div style={{ textAlign: 'center' }}>
                <p style={{ color: 'var(--danger)', fontSize: 'var(--fs-body)', marginBottom: 'var(--sp-2)' }}>
                  Failed to load graph
                </p>
                <p style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>{error}</p>
              </div>
            </div>
          )}
          {!isLoading && !error && nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center" style={{ color: 'var(--muted)', fontSize: 'var(--fs-body)' }}>
              No graph data yet
            </div>
          )}
          {!isLoading && !error && nodes.length > 0 && (
            <GraphCanvas
              nodes={nodes}
              edges={edges}
              onNodeClick={handleNodeClick}
              width={canvasSize.width}
              height={canvasSize.height}
            />
          )}
        </div>

        {/* Node inspector — df-inspector provides width, border-left, bg, padding, scroll */}
        {selectedNode && (
          <NodeInspector
            node={selectedNode}
            onClose={handleCloseInspector}
            onOpenDocument={onOpenDocument}
          />
        )}
      </div>
    </div>
  )
}
