import type { NodeData } from '../GraphPage'

interface NodeInspectorProps {
  node: NodeData | null
  onClose: () => void
  onOpenDocument?: (id: string) => void
}

const TYPE_LABELS: Record<NodeData['type'], string> = {
  document: '📄 Document',
  concept: '💡 Concept',
  decision: '⚖️ Decision',
}

export function NodeInspector({ node, onClose, onOpenDocument }: NodeInspectorProps) {
  if (!node) return null

  return (
    <div className="w-64 shrink-0 bg-gray-900 border border-gray-800 rounded-xl p-4 h-fit shadow-xl">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white truncate flex-1">
          Node Inspector
        </h3>
        <button
          onClick={onClose}
          className="text-gray-500 hover:text-white transition-colors ml-2"
          aria-label="Close inspector"
        >
          ✕
        </button>
      </div>

      <dl className="space-y-3">
        <div>
          <dt className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">Type</dt>
          <dd className="text-sm text-gray-200">{TYPE_LABELS[node.type] ?? node.type}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">Label</dt>
          <dd className="text-sm text-white font-medium break-words">{node.label}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">ID</dt>
          <dd className="text-xs font-mono text-indigo-400 break-all">{node.id}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">Position</dt>
          <dd className="text-xs text-gray-400">
            x: {Math.round(node.x)}, y: {Math.round(node.y)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">Provenance</dt>
          <dd className="text-xs text-gray-500 italic">—</dd>
        </div>
      </dl>

      {node.type === 'document' && onOpenDocument && (
        <button
          onClick={() => onOpenDocument(node.id)}
          className="mt-4 w-full px-3 py-2 text-xs rounded-lg bg-indigo-600 hover:bg-indigo-500 transition-colors text-white font-medium"
        >
          Open Document
        </button>
      )}
    </div>
  )
}
