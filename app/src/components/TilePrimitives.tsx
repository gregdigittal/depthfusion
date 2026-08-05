// ---------------------------------------------------------------------------
// Shared tile primitives
//
// Extracted verbatim from DashboardPage.tsx (S-253) so every dashboard tile —
// including tiles that live outside DashboardPage.tsx — can reuse one named
// skeleton, loading and error primitive instead of re-declaring its own.
// ---------------------------------------------------------------------------

export function SkeletonRow() {
  return (
    <div
      style={{
        height: '1em',
        borderRadius: 4,
        background: 'var(--muted)',
        opacity: 0.25,
        marginBottom: 'var(--sp-2)',
      }}
    />
  )
}

/**
 * The established tile loading state: a full-width stack of `rows` skeleton
 * rows. Renders exactly what the existing tiles render inline today.
 */
export function TileLoading({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ width: '100%' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  )
}

export function TileError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
      <div style={{ color: 'var(--danger-soft)', fontSize: 'var(--fs-small)' }}>
        {message}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            alignSelf: 'flex-start',
            fontSize: 'var(--fs-small)',
            color: 'var(--accent)',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          Retry
        </button>
      )}
    </div>
  )
}
