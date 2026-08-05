/**
 * S-253 T-861 / T-862 — CheckpointTimeline tile.
 *
 * First dashboard tile to live in app/src/components/ rather than as a
 * module-local function inside DashboardPage.tsx (interview answer 6). The five
 * pre-existing tiles stay where they are; new tiles land here.
 *
 * Loading and error states use the shared TileLoading / TileError primitives
 * extracted into ./TilePrimitives — no local copies. Styling follows the
 * existing tile idiom: the semantic `df-activity` row classes from
 * design/components.css plus inline styles driven by CSS custom properties.
 *
 * T-862 wires the per-row "Resume from here" button. Two deliberate choices:
 *   1. The action is a plain `fetch()` POST to `${serverUrl}/session/seed`, not a
 *      new `#[tauri::command]` + `invoke()`. Every existing dashboard action
 *      talks to the REST surface, and a Rust command would be a second, parallel
 *      transport for the same endpoint.
 *   2. Feedback is rendered inline in the row. There is no toast library in this
 *      codebase and adding one for a single call site is not warranted.
 *
 * S-254 T-865 turns the files column into a per-file drill-down. It previously
 * collapsed the list through `formatFilesModified` into one string ("a, b, +N
 * more") with the full list only in a `title` tooltip, which left no per-path
 * element to hang a handler on. The row now maps `files_modified` itself: the
 * first FILES_SHOWN paths become real <button type="button"> pills and the
 * remainder collapse into a non-interactive "+N more" badge that carries the
 * former tooltip. `formatFilesModified` is deliberately left exported and
 * unmodified in lib/checkpoints.ts — it is still the single-string formatter for
 * any other caller and is covered by __tests__/checkpoints.test.ts.
 */
import { useState } from 'react'
import { getServerUrl, loadTokens } from '../lib/ipc'
import { useCheckpoints } from '../hooks/useCheckpoints'
import { FILES_SHOWN } from '../lib/checkpoints'
import type { CheckpointRecord } from '../lib/checkpoints'
import { FileDiffHistory } from './FileDiffHistory'
import { TileEmpty, TileError, TileLoading } from './TilePrimitives'

/**
 * Render an ISO-8601 `created_at` as a short local date + time.
 *
 * Mirrors the defensive posture of formatRelativeTime in DashboardPage.tsx: a
 * malformed timestamp degrades to the raw string rather than throwing inside
 * render.
 */
function formatCheckpointTime(createdAt: string): string {
  const parsed = new Date(createdAt)
  if (Number.isNaN(parsed.getTime())) return createdAt
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Outcome of one resume attempt, rendered inline beneath the row. */
type ResumeResult = { ok: boolean; message: string }

const RESUME_SUCCESS = 'Session seeded — open a new Claude Code session to resume'

/**
 * POST the resume request for a single checkpoint.
 *
 * `POST /session/seed` is `require_principal`-protected, so the bearer token is
 * mandatory — omitting it yields a 401. Tokens are read through `loadTokens()`,
 * the same source every other dashboard request uses.
 *
 * @throws Error with a human-readable message when the request is rejected.
 */
async function postResume(checkpoint: CheckpointRecord): Promise<void> {
  const [serverUrl, tokens] = await Promise.all([getServerUrl(), loadTokens()])
  const resp = await fetch(`${serverUrl}/session/seed`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {}),
    },
    body: JSON.stringify({
      project: checkpoint.project_slug,
      mode: 'resume',
      checkpoint_id: checkpoint.checkpoint_id,
    }),
  })
  if (!resp.ok) {
    // Prefer the server's own message; fall back to the status line when the
    // body is empty or unreadable.
    let detail: string
    try {
      detail = (await resp.text()).trim()
    } catch {
      detail = ''
    }
    throw new Error(
      detail || `Resume failed: ${resp.status} ${resp.statusText}`.trim(),
    )
  }
}

/**
 * One checkpoint row.
 *
 * Extracted so `resuming` / `resumeResult` are genuinely per-row state: a single
 * pair of flags hoisted to the list would disable every button at once and show
 * one shared message.
 */
function CheckpointRow({ checkpoint }: { checkpoint: CheckpointRecord }) {
  const [resuming, setResuming] = useState(false)
  const [resumeResult, setResumeResult] = useState<ResumeResult | null>(null)
  // Which file's diff history is open beneath this row; null = none.
  const [selectedFile, setSelectedFile] = useState<string | null>(null)

  /** Open `path`'s diff panel, or close it when it is already the open one. */
  function toggleFile(path: string) {
    setSelectedFile((current) => (current === path ? null : path))
  }

  const shownFiles = checkpoint.files_modified.slice(0, FILES_SHOWN)
  const hiddenCount = checkpoint.files_modified.length - shownFiles.length
  // Ties the open pill to the panel it toggles. Only set while the panel is
  // actually rendered — aria-controls pointing at a missing id is a dangling
  // reference, so the attribute is omitted rather than always present.
  const panelId = `diff-panel-${checkpoint.checkpoint_id}`

  async function handleResume() {
    setResuming(true)
    setResumeResult(null)
    try {
      await postResume(checkpoint)
      setResumeResult({ ok: true, message: RESUME_SUCCESS })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setResumeResult({ ok: false, message: msg })
    } finally {
      setResuming(false)
    }
  }

  return (
    <li className="df-activity__row" style={{ flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-1)' }}>
        <span>{checkpoint.project_slug}</span>
        <span
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 'var(--sp-2)',
            fontSize: 'var(--fs-micro)',
            color: 'var(--faint)',
          }}
        >
          <span className="df-activity__time">
            {formatCheckpointTime(checkpoint.created_at)}
          </span>
          {shownFiles.length === 0 ? (
            <span>no files</span>
          ) : (
            shownFiles.map((path) => {
              const open = selectedFile === path
              return (
                <button
                  key={path}
                  type="button"
                  onClick={() => toggleFile(path)}
                  aria-expanded={open}
                  aria-controls={open ? panelId : undefined}
                  aria-label={`${open ? 'Hide' : 'Show'} diff history for ${path}`}
                  title={path}
                  style={{
                    maxWidth: '14rem',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    fontSize: 'var(--fs-micro)',
                    fontFamily: 'var(--font-mono)',
                    color: open ? 'var(--accent)' : 'var(--muted)',
                    background: open ? 'var(--accent-wash)' : 'var(--surface-2)',
                    border: `1px solid ${open ? 'var(--accent-border)' : 'var(--border)'}`,
                    borderRadius: 'var(--r-sm)',
                    cursor: 'pointer',
                    padding: '0 var(--sp-1)',
                  }}
                >
                  {path}
                </button>
              )
            })
          )}
          {hiddenCount > 0 ? (
            // Non-interactive: the remainder has no single path to drill into, so
            // it stays a badge and inherits the tooltip the whole column used to
            // carry.
            <span title={checkpoint.files_modified.join('\n')}>+{hiddenCount} more</span>
          ) : null}
          <span>{checkpoint.plan_state}</span>
        </span>
      </div>
      <button
        type="button"
        onClick={() => void handleResume()}
        disabled={resuming}
        aria-busy={resuming}
        style={{
          fontSize: 'var(--fs-small)',
          color: resuming ? 'var(--muted)' : 'var(--accent)',
          background: 'none',
          border: 'none',
          cursor: resuming ? 'default' : 'pointer',
          padding: 0,
        }}
      >
        {resuming ? 'Resuming...' : 'Resume from here'}
      </button>
      {resumeResult ? (
        <span
          role="status"
          style={{
            flexBasis: '100%',
            fontSize: 'var(--fs-micro)',
            color: resumeResult.ok ? 'var(--muted)' : 'var(--danger)',
          }}
        >
          {resumeResult.message}
        </span>
      ) : null}
      {selectedFile !== null ? (
        // flexBasis 100% drops the panel onto its own line inside the wrapping
        // row; minWidth 0 lets the diff's own overflow-x-auto do the scrolling
        // instead of widening this flex track.
        <div id={panelId} style={{ flexBasis: '100%', minWidth: 0 }}>
          <FileDiffHistory file={selectedFile} onClose={() => setSelectedFile(null)} />
        </div>
      ) : null}
    </li>
  )
}

export function CheckpointTimeline() {
  const { checkpoints, loading, error, retry } = useCheckpoints()

  if (loading) {
    return <TileLoading rows={4} />
  }

  if (error) {
    return <TileError message={`Failed to load checkpoints: ${error}`} onRetry={retry} />
  }

  if (checkpoints.length === 0) {
    // Uses the shared TileEmpty primitive that was extracted from this very
    // inline block in S-254 — keeping a local copy alongside it would be the
    // duplication the extraction removed.
    return <TileEmpty message="No checkpoints published yet." />
  }

  return (
    <ul className="df-activity">
      {checkpoints.map((cp) => (
        <CheckpointRow key={cp.checkpoint_id} checkpoint={cp} />
      ))}
    </ul>
  )
}
