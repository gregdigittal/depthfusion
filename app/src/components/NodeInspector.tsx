import type { NodeData } from '../GraphPage'

interface NodeInspectorProps {
  node: NodeData | null
  onClose: () => void
  onOpenDocument?: (id: string) => void
}

const CHIP_CLASS: Record<NodeData['type'], string> = {
  document: 'df-chip df-chip--doc',
  concept: 'df-chip df-chip--concept',
  decision: 'df-chip df-chip--decision',
}

const CHIP_LABEL: Record<NodeData['type'], string> = {
  document: 'Document',
  concept: 'Concept',
  decision: 'Decision',
}

export function NodeInspector({ node, onClose, onOpenDocument }: NodeInspectorProps) {
  if (!node) return null

  return (
    <div className="df-inspector">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--sp-4)' }}>
        <span style={{ fontSize: 'var(--fs-small)', fontWeight: 600, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Node Inspector
        </span>
        <button
          onClick={onClose}
          className="df-iconbtn"
          aria-label="Close inspector"
        >
          ✕
        </button>
      </div>

      <dl style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
        <div>
          <dt className="df-inspector__label">Type</dt>
          <dd className="df-inspector__value">
            <span className={CHIP_CLASS[node.type] ?? 'df-chip'}>
              {CHIP_LABEL[node.type] ?? node.type}
            </span>
          </dd>
        </div>
        <div>
          <dt className="df-inspector__label">Label</dt>
          <dd className="df-inspector__value" style={{ wordBreak: 'break-word' }}>{node.label}</dd>
        </div>
        <div>
          <dt className="df-inspector__label">ID</dt>
          <dd className="df-inspector__value df-inspector__value--mono" style={{ wordBreak: 'break-all' }}>
            {node.id}
          </dd>
        </div>
        <div>
          <dt className="df-inspector__label">Position</dt>
          <dd className="df-inspector__value">
            x: {Math.round(node.x)}, y: {Math.round(node.y)}
          </dd>
        </div>
        <div>
          <dt className="df-inspector__label">Provenance</dt>
          <dd className="df-inspector__value" style={{ fontStyle: 'italic', color: 'var(--muted)' }}>—</dd>
        </div>
      </dl>

      {node.type === 'document' && onOpenDocument && (
        <button
          onClick={() => onOpenDocument(node.id)}
          className="df-btn df-btn--primary df-btn--sm"
          style={{ width: '100%', marginTop: 'var(--sp-5)' }}
        >
          Open Document
        </button>
      )}
    </div>
  )
}
