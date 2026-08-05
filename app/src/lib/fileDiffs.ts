/**
 * S-254 T-865 — file-diff history data layer.
 *
 * Thin fetch wrapper over `GET /query/aggregate?type=file_diffs` (see
 * src/depthfusion/api/rest.py). Field names mirror the Python response body
 * exactly — snake_case, no renaming, no camelCasing — so a record round-trips
 * between the backend and the desktop app without a translation layer.
 *
 * Layer contract (documented in hooks/useCheckpoints.ts and enforced here):
 * this module owns the ENTIRE request shape — URL construction, `loadTokens()`
 * bearer header, non-ok handling, and response parsing. The hook layer owns
 * lifecycle only and must never rebuild a URL or touch a token, otherwise the
 * request shape has two sources of truth.
 *
 * Structure copied from ./checkpoints.ts: `serverUrl` is a parameter (the caller
 * already holds it) while tokens are resolved internally via `loadTokens()`, so
 * this function stays callable from a non-hook context (e.g. an action handler)
 * and still attaches the bearer token.
 */
import { loadTokens } from './ipc'

/**
 * One historical diff for a single file, as returned by the `file_diffs`
 * aggregate. `checkpoint_id`/`session_id`/`project_slug`/`created_at` identify
 * the checkpoint the diff was captured at; `diff` is the unified-diff text.
 */
export type FileDiffEntry = {
  checkpoint_id: string
  session_id: string
  project_slug: string
  created_at: string
  diff: string
}

/**
 * Parsed `type=file_diffs` result.
 *
 * `total` is how many diffs exist for the file server-side, `count` how many
 * `items` came back in this response (they differ once `limit` truncates), and
 * `file` echoes the path queried so a caller rendering several files cannot
 * mis-attribute a late response.
 */
export type FileDiffResult = {
  items: FileDiffEntry[]
  total: number
  count: number
  file: string
}

/** Raw shape of the `GET /query/aggregate?type=file_diffs` response body. */
type FileDiffsResponse = {
  items?: FileDiffEntry[]
  total?: number
  count?: number
  file?: string
}

/**
 * Fetch the diff history for `file`, OLDEST first — the server sorts ascending by
 * `created_at` because this is a chronological history view (the deliberate
 * opposite of `/query/checkpoints`, which is newest-first). Callers that want the
 * newest entry read the END of `items`, not the start.
 *
 * Note that the server's `limit` is applied to the FRONT of that ascending
 * chronology, so a file with more recorded diffs than `limit` returns its oldest
 * `limit` entries. Pass a `limit` large enough for the window you intend to show.
 *
 * `type` and `file` are always sent. `since`, `project` and `limit` are appended
 * only when supplied — an absent optional must not become `since=undefined` in
 * the query string, which the server would read as a literal filter value.
 *
 * @param serverUrl Base URL of the DepthFusion server (no trailing slash).
 * @param file      Repository-relative path whose diff history is wanted.
 * @param since     Optional ISO-8601 lower bound on `created_at`.
 * @param project   Optional project slug to filter on.
 * @param limit     Optional maximum number of diffs to return.
 * @throws Error with a readable message when the response is not ok; the
 *         message is what the hook layer surfaces to the tile.
 */
export async function fetchFileDiffs(
  serverUrl: string,
  file: string,
  since?: string,
  project?: string,
  limit?: number,
): Promise<FileDiffResult> {
  const tokens = await loadTokens()

  const params = new URLSearchParams({ type: 'file_diffs', file })
  if (since !== undefined) params.set('since', since)
  if (project !== undefined) params.set('project', project)
  if (limit !== undefined) params.set('limit', String(limit))

  const url = `${serverUrl}/query/aggregate?${params.toString()}`
  const resp = await fetch(url, {
    headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {},
  })
  if (!resp.ok) {
    throw new Error(`File diffs fetch failed: ${resp.status} ${resp.statusText}`.trim())
  }

  const json = (await resp.json()) as FileDiffsResponse
  const items = json.items ?? []
  return {
    items,
    total: json.total ?? items.length,
    count: json.count ?? items.length,
    file: json.file ?? file,
  }
}
