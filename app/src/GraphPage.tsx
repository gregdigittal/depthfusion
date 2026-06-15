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
  const serverUrl = getServerUrl()
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
      <div className="px-6 py-3 border-b border-gray-800 shrink-0 flex items-center justify-between">
        <h1 className="text-sm font-semibold text-white">Knowledge Graph</h1>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span>{nodes.length} nodes</span>
          <span>{edges.length} edges</span>
          <span className="text-gray-700">· scroll to zoom, drag to pan</span>
        </div>
      </div>

      {/* Main area */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Canvas area */}
        <div ref={containerRef} className="flex-1 relative overflow-hidden bg-gray-950">
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-sm">
              Loading graph…
            </div>
          )}
          {!isLoading && error && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center">
                <p className="text-red-400 text-sm mb-2">Failed to load graph</p>
                <p className="text-gray-600 text-xs">{error}</p>
              </div>
            </div>
          )}
          {!isLoading && !error && nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center text-gray-600 text-sm">
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

        {/* Node inspector sidebar */}
        {selectedNode && (
          <div className="shrink-0 w-72 border-l border-gray-800 p-4 overflow-y-auto bg-gray-950">
            <NodeInspector
              node={selectedNode}
              onClose={handleCloseInspector}
              onOpenDocument={onOpenDocument}
            />
          </div>
        )}
      </div>
    </div>
  )
}
