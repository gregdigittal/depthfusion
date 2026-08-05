/**
 * S-254 T-865 — unit tests for the file-diff history data layer.
 *
 * The .ts extension is mandatory: app/vitest.config.ts runs with
 * environment:'node' and include ['src/__tests__/**\/*.test.ts'], and there is
 * no jsdom — a .tsx spec would silently never run.
 *
 * Mocking follows checkpoints.test.ts: mock `@tauri-apps/api/core` so the real
 * `loadTokens()` wrapper body in ../lib/ipc is exercised (we do NOT mock
 * '../lib/ipc'), and stub global fetch.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// Hoist mocks before any module imports
// ---------------------------------------------------------------------------

const { mockInvoke } = vi.hoisted(() => ({ mockInvoke: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

import { fetchFileDiffs, type FileDiffEntry, type FileDiffResult } from '../lib/fileDiffs'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_SERVER_URL = 'http://localhost:7301'
const MOCK_TOKEN = 'test-bearer-token'
const MOCK_FILE = 'src/depthfusion/api/query.py'

const MOCK_DIFF: FileDiffEntry = {
  checkpoint_id: 'ckpt-001',
  session_id: 'sess-abc',
  project_slug: 'depthfusion',
  created_at: '2026-08-05T10:00:00Z',
  diff: '--- a/src/depthfusion/api/query.py\n+++ b/src/depthfusion/api/query.py\n@@ -1 +1 @@\n-old\n+new\n',
}

const MOCK_DIFF_2: FileDiffEntry = {
  checkpoint_id: 'ckpt-002',
  session_id: 'sess-def',
  project_slug: 'depthfusion',
  created_at: '2026-08-05T09:00:00Z',
  diff: '',
}

function makeJsonResponse(data: unknown, ok = true, status?: number, statusText = '') {
  return {
    ok,
    status: status ?? (ok ? 200 : 500),
    statusText,
    json: vi.fn().mockResolvedValue(data),
  }
}

/** The query string of the single recorded fetch call, as URLSearchParams. */
function fetchedParams(): URLSearchParams {
  const [url] = mockFetch.mock.calls[0] as [string]
  return new URL(url).searchParams
}

beforeEach(() => {
  vi.clearAllMocks()
  mockInvoke.mockImplementation((cmd: string) => {
    if (cmd === 'load_tokens')
      return Promise.resolve({
        access_token: MOCK_TOKEN,
        id_token: null,
        refresh_token: null,
        expires_in: 3600,
        token_type: 'Bearer',
      })
    return Promise.resolve(undefined)
  })
})

// ---------------------------------------------------------------------------
// URL construction (AC-2, AC-4)
// ---------------------------------------------------------------------------

describe('fetchFileDiffs — URL construction', () => {
  it('GETs <serverUrl>/query/aggregate', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [MOCK_DIFF] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    expect(mockFetch).toHaveBeenCalledOnce()
    const [url] = mockFetch.mock.calls[0] as [string]
    expect(new URL(url).origin + new URL(url).pathname).toBe(
      `${MOCK_SERVER_URL}/query/aggregate`,
    )
  })

  it('always carries type=file_diffs and the file param', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    const params = fetchedParams()
    expect(params.get('type')).toBe('file_diffs')
    expect(params.get('file')).toBe(MOCK_FILE)
  })

  it('percent-encodes the file path rather than emitting raw slashes', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, 'app/src/lib/file diffs.ts')
    const [url] = mockFetch.mock.calls[0] as [string]
    expect(url).toContain('file=app%2Fsrc%2Flib%2Ffile+diffs.ts')
    expect(fetchedParams().get('file')).toBe('app/src/lib/file diffs.ts')
  })

  it('omits since, project and limit entirely when they are undefined', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    const [url] = mockFetch.mock.calls[0] as [string]
    const params = fetchedParams()
    expect(params.has('since')).toBe(false)
    expect(params.has('project')).toBe(false)
    expect(params.has('limit')).toBe(false)
    // Guards the specific regression: an absent optional must not be
    // stringified into the query as the literal "undefined".
    expect(url).not.toContain('undefined')
    expect([...params.keys()].sort()).toEqual(['file', 'type'])
  })

  it('includes since only when supplied, leaving project and limit off', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE, '2026-08-01T00:00:00Z')
    const params = fetchedParams()
    expect(params.get('since')).toBe('2026-08-01T00:00:00Z')
    expect(params.has('project')).toBe(false)
    expect(params.has('limit')).toBe(false)
  })

  it('includes project without since when since is explicitly undefined', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE, undefined, 'depthfusion')
    const params = fetchedParams()
    expect(params.has('since')).toBe(false)
    expect(params.get('project')).toBe('depthfusion')
    expect(params.has('limit')).toBe(false)
  })

  it('serialises limit as a number-valued string and carries all four optionals', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(
      'https://df.example.com',
      MOCK_FILE,
      '2026-08-01T00:00:00Z',
      'other-project',
      5,
    )
    const params = fetchedParams()
    expect(params.get('type')).toBe('file_diffs')
    expect(params.get('file')).toBe(MOCK_FILE)
    expect(params.get('since')).toBe('2026-08-01T00:00:00Z')
    expect(params.get('project')).toBe('other-project')
    expect(params.get('limit')).toBe('5')
    const [url] = mockFetch.mock.calls[0] as [string]
    expect(new URL(url).origin).toBe('https://df.example.com')
  })

  it('keeps limit=0 rather than dropping it as falsy', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE, undefined, undefined, 0)
    expect(fetchedParams().get('limit')).toBe('0')
  })
})

// ---------------------------------------------------------------------------
// Authorization header (AC-2, AC-4)
// ---------------------------------------------------------------------------

describe('fetchFileDiffs — authorization', () => {
  it('calls loadTokens() internally via the real ipc wrapper', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    expect(mockInvoke).toHaveBeenCalledWith('load_tokens')
  })

  it('attaches Authorization: Bearer <access_token> when tokens are non-null', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect(init.headers).toMatchObject({ Authorization: `Bearer ${MOCK_TOKEN}` })
  })

  it('omits Authorization when loadTokens() resolves null', async () => {
    mockInvoke.mockImplementation((cmd: string) => {
      if (cmd === 'load_tokens') return Promise.resolve(null)
      return Promise.resolve(undefined)
    })
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect(init.headers).not.toHaveProperty('Authorization')
  })
})

// ---------------------------------------------------------------------------
// Return shape and error surfacing (AC-1, AC-2, AC-4)
// ---------------------------------------------------------------------------

describe('fetchFileDiffs — response handling', () => {
  it('returns a FileDiffResult with items, total, count and file', async () => {
    mockFetch.mockResolvedValue(
      makeJsonResponse({
        items: [MOCK_DIFF, MOCK_DIFF_2],
        total: 7,
        count: 2,
        file: MOCK_FILE,
      }),
    )
    const result: FileDiffResult = await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    expect(Object.keys(result).sort()).toEqual(['count', 'file', 'items', 'total'])
    expect(result.total).toBe(7)
    expect(result.count).toBe(2)
    expect(result.file).toBe(MOCK_FILE)
    expect(result.items).toHaveLength(2)
    expect(Object.keys(result.items[0]).sort()).toEqual(
      ['checkpoint_id', 'created_at', 'diff', 'project_slug', 'session_id'].sort(),
    )
    expect(result.items[0].checkpoint_id).toBe('ckpt-001')
    expect(result.items[0].session_id).toBe('sess-abc')
    expect(result.items[0].project_slug).toBe('depthfusion')
    expect(result.items[0].created_at).toBe('2026-08-05T10:00:00Z')
    expect(result.items[0].diff).toContain('+++ b/src/depthfusion/api/query.py')
  })

  it('tolerates an empty diff string', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [MOCK_DIFF_2], total: 1, count: 1 }))
    const result = await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    expect(result.items[0].diff).toBe('')
  })

  it('defaults items to [] and derives total/count/file when the body omits them', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}))
    const result = await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    expect(result).toEqual({ items: [], total: 0, count: 0, file: MOCK_FILE })
  })

  it('derives total and count from items length when only items is present', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [MOCK_DIFF, MOCK_DIFF_2] }))
    const result = await fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)
    expect(result.total).toBe(2)
    expect(result.count).toBe(2)
    expect(result.file).toBe(MOCK_FILE)
  })

  it('surfaces a readable error on a non-ok response', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}, false, 503, 'Service Unavailable'))
    await expect(fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)).rejects.toThrow(
      'File diffs fetch failed: 503 Service Unavailable',
    )
  })

  it('does not parse the body when the response is not ok', async () => {
    const resp = makeJsonResponse({}, false, 401, 'Unauthorized')
    mockFetch.mockResolvedValue(resp)
    await expect(fetchFileDiffs(MOCK_SERVER_URL, MOCK_FILE)).rejects.toThrow(/401/)
    expect(resp.json).not.toHaveBeenCalled()
  })
})
