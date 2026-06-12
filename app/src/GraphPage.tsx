import { useEffect, useState, useRef, useCallback } from 'react'
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

interface GraphResponse {
  nodes: NodeData[]
  edges: EdgeData[]
}

interface GraphPageProps {
  onOpenDocument?: (id: string) => void
}

export function GraphPage({ onOpenDocument }: GraphPageProps) {
  const [serverUrl, setServerUrl] = useState('https://localhost:8000')
  const [nodes, setNodes] = useState<NodeData[]>([])
  const [edges, setEdges] = useState<EdgeData[]>([])
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [canvasSize, setCanvasSize] = useState({ width: 800, height: 600 })

  useEffect(() => {
    getServerUrl().then(setServerUrl).catch(console.error)
  }, [])

  // Fetch graph data
  useEffect(() => {
    if (!serverUrl) return
    setIsLoading(true)
    setError(null)

    fetch(`${serverUrl}/api/v1/graph`)
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`)
        return resp.json() as Promise<GraphResponse>
      })
      .then((data) => {
        setNodes(data.nodes)
        setEdges(data.edges)
        setIsLoading(false)
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        setIsLoading(false)
      })
  }, [serverUrl])

  // Measure container to size the canvas
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      setCanvasSize({
        width: Math.floor(entry.contentRect.width),
        height: Math.floor(entry.contentRect.height),
      })
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const handleNodeClick = useCallback((node: NodeData) => {
    setSelectedNode((prev) => (prev?.id === node.id ? null : node))
  }, [])

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300">Knowledge Graph</h2>
        {isLoading && (
          <span className="text-xs text-gray-500 animate-pulse">Loading…</span>
        )}
        {!isLoading && !error && (
          <span className="text-xs text-gray-500">
            {nodes.length} nodes · {edges.length} edges
          </span>
        )}
        {error && (
          <span className="text-xs text-red-400">Error: {error}</span>
        )}
        <div className="ml-auto text-xs text-gray-600">
          Scroll to zoom · Drag to pan · Click node to inspect
        </div>
      </div>

      {/* Canvas area */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div
          ref={containerRef}
          className="flex-1 overflow-hidden bg-gray-950"
        >
          {!isLoading && !error && (
            <GraphCanvas
              nodes={nodes}
              edges={edges}
              onNodeClick={handleNodeClick}
              width={canvasSize.width}
              height={canvasSize.height}
            />
          )}

          {!isLoading && !error && nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="text-center text-gray-600">
                <p className="text-4xl mb-3">🕸</p>
                <p className="text-sm">No graph data available</p>
              </div>
            </div>
          )}
        </div>

        {/* Node inspector */}
        {selectedNode && (
          <div className="shrink-0 p-4 border-l border-gray-800">
            <NodeInspector
              node={selectedNode}
              onClose={() => setSelectedNode(null)}
              onOpenDocument={onOpenDocument}
            />
          </div>
        )}
      </div>
    </div>
  )
}
