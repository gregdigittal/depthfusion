/**
 * S-254 T-865 — FileDiffHistory: the per-file diff drill-down panel.
 *
 * Rendered inline beneath a CheckpointTimeline row when one of that row's file
 * pills is clicked. This is a drill-down panel, NOT a dashboard tile: it is
 * deliberately absent from DEFAULT_TILES in hooks/useDashboard.ts, so the
 * `depthfusion-dashboard-layout-v2` localStorage key keeps its existing shape and
 * no user's saved layout is invalidated by this feature.
 *
 * Layering: all three non-data states come from ./TilePrimitives (TileLoading /
 * TileError / TileEmpty) rather than local copies, so the panel's chrome matches
 * every tile it sits inside. Data comes from useFileDiffs, which owns lifecycle;
 * the request shape lives in lib/fileDiffs.ts. Nothing here builds a URL or
 * reads a token.
 *
 * Styling: Tailwind v4 utilities (the panel is new surface area, and Tailwind is
 * already wired via @tailwindcss/vite + `@import "tailwindcss"` in index.css),
 * with design tokens threaded through arbitrary values so colours, radii and
 * font sizes stay on the same scale as the inline-styled tiles around it.
 *
 * Horizontal-overflow contract: unified diffs contain arbitrarily long lines. The
 * <pre> keeps `whitespace-pre` (no wrapping — column alignment is meaningful in a
 * diff) and owns its own `overflow-x-auto`, while it and every flex ancestor carry
 * `min-w-0`. Without those, a long diff line would blow out the flex track and
 * force the whole dashboard page to scroll horizontally.
 */
import { FileDiff, X } from 'lucide-react'
import { useFileDiffs } from '../hooks/useFileDiffs'
import { TileEmpty, TileError, TileLoading } from './TilePrimitives'

/**
 * Render an ISO-8601 `created_at` as a short local date + time.
 *
 * Intentionally a local copy of the same five lines in CheckpointTimeline.tsx
 * rather than a shared import: CheckpointTimeline imports *this* component, so
 * importing its formatter back would create a circular module graph for the sake
 * of one formatter. Same defensive posture as the original — a malformed
 * timestamp degrades to the raw string instead of throwing inside render.
 */
function formatDiffTime(createdAt: string): string {
  const parsed = new Date(createdAt)
  if (Number.isNaN(parsed.getTime())) return createdAt
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function FileDiffHistory({
  file,
  onClose,
}: {
  file: string
  onClose: () => void
}) {
  const { diffs, loading, error, retry } = useFileDiffs(file)

  return (
    <section
      aria-label={`Diff history for ${file}`}
      className="mt-2 w-full min-w-0 rounded-[var(--r-md)] border border-[var(--border)] bg-[var(--surface-2)] p-3"
    >
      <header className="mb-2 flex min-w-0 items-center justify-between gap-2">
        <h3 className="m-0 min-w-0 truncate font-[family-name:var(--font-mono)] text-[length:var(--fs-micro)] font-normal text-[var(--text-2)]">
          {file}
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label={`Close diff history for ${file}`}
          className="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-[var(--muted)] hover:text-[var(--text)]"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </header>

      {loading ? (
        <TileLoading rows={2} />
      ) : error ? (
        <TileError message={`Failed to load diffs: ${error}`} onRetry={retry} />
      ) : diffs.length === 0 ? (
        <TileEmpty message="No diffs recorded for this file yet." icon={FileDiff} />
      ) : (
        <ol className="m-0 flex list-none flex-col gap-3 p-0">
          {diffs.map((entry) => (
            <li key={entry.checkpoint_id} className="min-w-0">
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[length:var(--fs-micro)] text-[var(--faint)]">
                <time dateTime={entry.created_at}>{formatDiffTime(entry.created_at)}</time>
                <span>{entry.project_slug}</span>
              </div>
              <pre className="m-0 max-h-72 min-w-0 overflow-x-auto overflow-y-auto whitespace-pre rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--bg)] p-2 font-[family-name:var(--font-mono)] text-[length:var(--fs-micro)] leading-relaxed text-[var(--text-2)]">
                {entry.diff}
              </pre>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
