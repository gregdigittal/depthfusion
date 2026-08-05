// ---------------------------------------------------------------------------
// Shared tile primitives
//
// Extracted verbatim from DashboardPage.tsx (S-253) so every dashboard tile —
// including tiles that live outside DashboardPage.tsx — can reuse one named
// skeleton, loading and error primitive instead of re-declaring its own.
//
// S-254 T-865 adds TileEmpty, extracted from the inline empty state in
// CheckpointTimeline.tsx, so "nothing to show yet" is one primitive too.
//
// Styling note: these primitives predate any Tailwind usage in the dashboard
// and are styled with inline CSS custom properties (--muted, --danger-soft,
// --fs-small, --sp-2). TileEmpty deliberately follows that idiom rather than
// introducing Tailwind classes here, because AC-3 requires it to be consistent
// with the TileLoading/TileError it sits beside — a lone Tailwind-styled
// primitive would not inherit the same tokens and would drift visually.
// ---------------------------------------------------------------------------

import type { LucideIcon } from 'lucide-react'

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

/**
 * The established tile empty state: the "nothing published yet" line that
 * CheckpointTimeline.tsx rendered inline, named so every tile shows the same
 * muted small-text treatment instead of re-inventing one.
 *
 * Props mirror TileError: a required `message` plus one optional extra. Here
 * that extra is `icon` — a lucide-react icon *component* (not an element), so
 * the primitive controls its size and colour and callers cannot drift.
 */
export function TileEmpty({ message, icon: Icon }: { message: string; icon?: LucideIcon }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--sp-2)',
        color: 'var(--muted)',
        fontSize: 'var(--fs-small)',
      }}
    >
      {Icon && <Icon size={14} aria-hidden="true" />}
      <span>{message}</span>
    </div>
  )
}
