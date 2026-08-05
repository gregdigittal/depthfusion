/**
 * S-253 T-861 — checkpoints data layer.
 *
 * Thin fetch wrapper over `GET /query/checkpoints` (see
 * src/depthfusion/api/rest.py). `CheckpointRecord` mirrors the Python
 * `CheckpointRecord.to_dict()` keys in core/event_store.py exactly — snake_case,
 * no renaming, no camelCasing — so a record can round-trip between the backend
 * and the desktop app without a translation layer.
 *
 * Deliberate divergence from the `useRecentSessions` hook idiom (interview
 * answer 4): the hook `Promise.all`s `getServerUrl()` and `loadTokens()`
 * together, whereas this function takes `serverUrl` as a parameter — the caller
 * already holds it — and resolves tokens itself via `loadTokens()`. That keeps
 * this module callable from a non-hook context (e.g. an action handler) while
 * still attaching the bearer token.
 */
import { loadTokens } from '../lib/ipc'

/**
 * A session checkpoint as persisted by `EventStore.publish_checkpoint`.
 *
 * Field names are load-bearing: they are exactly the eight keys emitted by
 * the Python `CheckpointRecord.to_dict()`. Do not rename or camelCase them.
 */
export type CheckpointRecord = {
  checkpoint_id: string
  session_id: string
  project_slug: string
  created_at: string
  files_modified: string[]
  plan_state: string
  git_stash_ref: string | null
  context_pct_at_checkpoint: number | null
}

/** Shape of the `GET /query/checkpoints` response body. */
type CheckpointsResponse = {
  items?: CheckpointRecord[]
}

/**
 * Fetch checkpoints for `project`, newest first (the server sorts descending).
 *
 * @param serverUrl Base URL of the DepthFusion server (no trailing slash).
 * @param project   Project slug to filter on.
 * @param limit     Maximum number of records to return.
 * @throws Error with a readable message when the response is not ok.
 */
export async function fetchCheckpoints(
  serverUrl: string,
  project: string,
  limit: number,
): Promise<CheckpointRecord[]> {
  const tokens = await loadTokens()
  const url = `${serverUrl}/query/checkpoints?project=${encodeURIComponent(project)}&limit=${limit}`
  const resp = await fetch(url, {
    headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {},
  })
  if (!resp.ok) {
    throw new Error(`Checkpoints fetch failed: ${resp.status} ${resp.statusText}`.trim())
  }
  const json = (await resp.json()) as CheckpointsResponse
  return json.items ?? []
}

/** How many file paths a row lists before collapsing the remainder to "+N more". */
export const FILES_SHOWN = 3

/**
 * Render the *files modified* by a checkpoint — S-253 AC-2 asks for the files,
 * not a count, so the paths themselves are shown.
 *
 * A checkpoint can list dozens of paths, which would blow out a dashboard row,
 * so the first `FILES_SHOWN` are rendered and the rest collapse to "+N more".
 * Callers keep the full list reachable via a `title` tooltip, so nothing is
 * actually hidden.
 */
export function formatFilesModified(files: string[]): string {
  if (files.length === 0) return 'no files'
  const shown = files.slice(0, FILES_SHOWN).join(', ')
  const rest = files.length - FILES_SHOWN
  return rest > 0 ? `${shown} +${rest} more` : shown
}
