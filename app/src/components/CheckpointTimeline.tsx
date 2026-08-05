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
 */
import { useState } from 'react'
import { getServerUrl, loadTokens } from '../lib/ipc'
import { useCheckpoints } from '../hooks/useCheckpoints'
import { formatFilesModified } from '../lib/checkpoints'
import type { CheckpointRecord } from '../lib/checkpoints'
import { TileError, TileLoading } from './TilePrimitives'

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
            gap: 'var(--sp-2)',
            fontSize: 'var(--fs-micro)',
            color: 'var(--faint)',
          }}
        >
          <span className="df-activity__time">
            {formatCheckpointTime(checkpoint.created_at)}
          </span>
          <span
            title={checkpoint.files_modified.join('\n')}
            style={{
              maxWidth: '22rem',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {formatFilesModified(checkpoint.files_modified)}
          </span>
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
    return (
      <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-small)' }}>
        No checkpoints published yet.
      </div>
    )
  }

  return (
    <ul className="df-activity">
      {checkpoints.map((cp) => (
        <CheckpointRow key={cp.checkpoint_id} checkpoint={cp} />
      ))}
    </ul>
  )
}
