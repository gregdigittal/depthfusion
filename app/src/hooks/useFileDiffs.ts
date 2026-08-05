/**
 * S-254 T-865 — useFileDiffs: diff history for one file from
 * `GET /query/aggregate?type=file_diffs`.
 *
 * Reproduces the house hook idiom verbatim (useRecentSessions / useStats /
 * useCheckpoints): data/loading/error/retryCount state, a `cancelled` flag inside
 * useEffect, `e instanceof Error ? e.message : String(e)` catch narrowing, a
 * 60_000ms setInterval refresh torn down with clearInterval, a deps array ending
 * in retryCount, and a retry() that bumps retryCount.
 *
 * Layer contract (documented in useCheckpoints.ts, enforced here): this hook owns
 * lifecycle ONLY. It contains no URL construction and never touches a token —
 * `fetchFileDiffs` in ../lib/fileDiffs owns the entire request shape, so the
 * request has exactly one source of truth. `getServerUrl()` is read here because
 * the base URL is a *parameter* of that function, exactly as useCheckpoints does.
 *
 * One deliberate divergence from the sibling hooks: the empty-`file`
 * short-circuit. `file` is a drill-down selection, not a dashboard-wide filter,
 * so "nothing selected" is a real and frequent state. Requesting
 * `type=file_diffs` with `file=''` would be a pointless round trip *and* would
 * start a 60s poll for a panel nobody opened, so an empty `file` performs no
 * fetch and registers no interval, and the hook reports
 * { diffs: [], loading: false, error: null }.
 *
 * That empty result is DERIVED at the return statement rather than written into
 * state from inside the effect, for two reasons. First, correctness: an effect
 * writing it would land one render late, so a transition from a loaded file to
 * '' would briefly re-render the previous file's diffs under the new selection.
 * Deriving it is synchronous and cannot go stale. Second, effect hygiene: writing
 * it would add a second `react-hooks/set-state-in-effect` site on top of the one
 * the copied idiom already carries (`setLoading(true)`, flagged identically in
 * useCheckpoints/useRecentSessions/useStorageUsage), for no benefit. The internal
 * `diffs`/`loading`/`error` state is simply not read while nothing is selected.
 */
import { useEffect, useState } from 'react'
import { getServerUrl } from '../lib/ipc'
import { fetchFileDiffs } from '../lib/fileDiffs'
import type { FileDiffEntry } from '../lib/fileDiffs'

interface UseFileDiffsResult {
  diffs: FileDiffEntry[]
  loading: boolean
  error: string | null
  retry: () => void
}

/**
 * Stable identity for the no-selection result, so consumers memoising on `diffs`
 * do not see a new array on every render while nothing is selected.
 */
const NO_DIFFS: FileDiffEntry[] = []

export function useFileDiffs(
  file: string,
  since?: string,
  project?: string,
  limit?: number,
): UseFileDiffsResult {
  const [diffs, setDiffs] = useState<FileDiffEntry[]>([])
  // Seeded from `file`: with no file selected there is no request to wait on.
  const [loading, setLoading] = useState(file !== '')
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  const retry = () => setRetryCount((n) => n + 1)

  useEffect(() => {
    let cancelled = false

    // No selection: no request, no 60s poll. The empty result the hook reports
    // for this case is derived at the return statement, not written here.
    if (file === '') return

    setLoading(true)

    async function loadFileDiffs() {
      try {
        const serverUrl = await getServerUrl()
        if (cancelled) return
        const result = await fetchFileDiffs(serverUrl, file, since, project, limit)
        if (!cancelled) {
          setDiffs(result.items)
          setError(null)
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadFileDiffs()
    const interval = setInterval(() => void loadFileDiffs(), 60_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [file, since, project, limit, retryCount])

  if (file === '') {
    return { diffs: NO_DIFFS, loading: false, error: null, retry }
  }

  return { diffs, loading, error, retry }
}
