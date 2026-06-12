import { useEffect, useRef, useCallback } from 'react'
import type { NodeData, EdgeData } from '../GraphPage'

interface GraphCanvasProps {
  nodes: NodeData[]
  edges: EdgeData[]
  onNodeClick: (node: NodeData) => void
  width: number
  height: number
}

const NODE_RADIUS = 28
const NODE_COLORS: Record<NodeData['type'], string> = {
  document: '#6366f1', // indigo
  concept: '#10b981',  // emerald
  decision: '#f59e0b', // amber
}
const EDGE_COLOR = '#4b5563'
const SELECTED_COLOR = '#a5b4fc'
const TEXT_COLOR = '#e5e7eb'

export function GraphCanvas({
  nodes,
  edges,
  onNodeClick,
  width,
  height,
}: GraphCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // Viewport: pan offset
  const viewportRef = useRef({ dx: 0, dy: 0, scale: 1 })
  const dragRef = useRef<{ startX: number; startY: number; panStart: { dx: number; dy: number } } | null>(null)
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)

  nodesRef.current = nodes
  edgesRef.current = edges

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const { dx, dy, scale } = viewportRef.current

    ctx.clearRect(0, 0, width, height)

    ctx.save()
    ctx.translate(dx, dy)
    ctx.scale(scale, scale)

    const vpLeft = -dx / scale
    const vpTop = -dy / scale
    const vpRight = vpLeft + width / scale
    const vpBottom = vpTop + height / scale

    // Viewport culling helper
    const inViewport = (x: number, y: number) =>
      x + NODE_RADIUS >= vpLeft &&
      x - NODE_RADIUS <= vpRight &&
      y + NODE_RADIUS >= vpTop &&
      y - NODE_RADIUS <= vpBottom

    // Draw edges
    ctx.strokeStyle = EDGE_COLOR
    ctx.lineWidth = 1.5 / scale

    const nodeMap = new Map(nodesRef.current.map((n) => [n.id, n]))

    for (const edge of edgesRef.current) {
      const from = nodeMap.get(edge.from)
      const to = nodeMap.get(edge.to)
      if (!from || !to) continue
      if (!inViewport(from.x, from.y) && !inViewport(to.x, to.y)) continue

      ctx.beginPath()
      ctx.moveTo(from.x, from.y)
      ctx.lineTo(to.x, to.y)
      ctx.stroke()

      // Edge label
      if (edge.label) {
        const mx = (from.x + to.x) / 2
        const my = (from.y + to.y) / 2
        ctx.fillStyle = '#9ca3af'
        ctx.font = `${10 / scale}px sans-serif`
        ctx.textAlign = 'center'
        ctx.fillText(edge.label, mx, my - 4 / scale)
      }
    }

    // Draw nodes
    for (const node of nodesRef.current) {
      if (!inViewport(node.x, node.y)) continue

      const color = NODE_COLORS[node.type] ?? '#6366f1'

      // Shadow / glow
      ctx.shadowColor = color
      ctx.shadowBlur = 8 / scale

      ctx.beginPath()
      ctx.arc(node.x, node.y, NODE_RADIUS / scale, 0, Math.PI * 2)
      ctx.fillStyle = color + '33'
      ctx.fill()
      ctx.strokeStyle = color
      ctx.lineWidth = 2 / scale
      ctx.stroke()
      ctx.shadowBlur = 0

      // Label
      ctx.fillStyle = TEXT_COLOR
      ctx.font = `bold ${11 / scale}px sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      const maxW = (NODE_RADIUS * 2 - 8) / scale
      let label = node.label
      if (ctx.measureText(label).width > maxW) {
        while (label.length > 0 && ctx.measureText(label + '…').width > maxW) {
          label = label.slice(0, -1)
        }
        label += '…'
      }
      ctx.fillText(label, node.x, node.y)
    }

    ctx.restore()
  }, [width, height])

  // Re-draw when nodes/edges/dimensions change
  useEffect(() => {
    draw()
  }, [nodes, edges, width, height, draw])

  // Click handler: find which node was clicked
  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const { dx, dy, scale } = viewportRef.current
      const cx = (e.clientX - rect.left - dx) / scale
      const cy = (e.clientY - rect.top - dy) / scale

      for (const node of nodesRef.current) {
        const dist = Math.hypot(cx - node.x, cy - node.y)
        if (dist <= NODE_RADIUS / scale) {
          onNodeClick(node)
          return
        }
      }
    },
    [onNodeClick]
  )

  // Pan by dragging
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      panStart: { ...viewportRef.current },
    }
  }, [])

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!dragRef.current) return
      const { startX, startY, panStart } = dragRef.current
      viewportRef.current.dx = panStart.dx + (e.clientX - startX)
      viewportRef.current.dy = panStart.dy + (e.clientY - startY)
      draw()
    },
    [draw]
  )

  const handleMouseUp = useCallback(() => {
    dragRef.current = null
  }, [])

  // Zoom with wheel
  const handleWheel = useCallback(
    (e: React.WheelEvent<HTMLCanvasElement>) => {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.1 : 0.9
      viewportRef.current.scale = Math.max(
        0.2,
        Math.min(4, viewportRef.current.scale * factor)
      )
      draw()
    },
    [draw]
  )

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      className="block cursor-grab active:cursor-grabbing"
      onClick={handleClick}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onWheel={handleWheel}
      style={{ width, height }}
    />
  )
}

// Re-export so dependents can import the SELECTED_COLOR constant
export { SELECTED_COLOR }
