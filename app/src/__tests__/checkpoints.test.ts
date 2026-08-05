/**
 * S-253 T-861 — unit tests for the checkpoints data layer.
 *
 * The .ts extension is mandatory: app/vitest.config.ts runs with
 * environment:'node' and include ['src/__tests__/**\/*.test.ts'], and there is
 * no jsdom — a .tsx spec would silently never run.
 *
 * Mocking follows cognitive.test.ts: mock `@tauri-apps/api/core` so the real
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

import {
  fetchCheckpoints,
  formatFilesModified,
  FILES_SHOWN,
  type CheckpointRecord,
} from '../lib/checkpoints'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_SERVER_URL = 'http://localhost:7301'
const MOCK_TOKEN = 'test-bearer-token'

const MOCK_CHECKPOINT: CheckpointRecord = {
  checkpoint_id: 'ckpt-001',
  session_id: 'sess-abc',
  project_slug: 'depthfusion',
  created_at: '2026-08-05T10:00:00Z',
  files_modified: ['app/src/lib/checkpoints.ts', 'src/depthfusion/api/query.py'],
  plan_state: 'T-861 data layer in progress',
  git_stash_ref: 'stash@{0}',
  context_pct_at_checkpoint: 62.5,
}

const MOCK_CHECKPOINT_NULLS: CheckpointRecord = {
  checkpoint_id: 'ckpt-002',
  session_id: 'sess-def',
  project_slug: 'depthfusion',
  created_at: '2026-08-05T09:00:00Z',
  files_modified: [],
  plan_state: '',
  git_stash_ref: null,
  context_pct_at_checkpoint: null,
}

function makeJsonResponse(data: unknown, ok = true, status?: number, statusText = '') {
  return {
    ok,
    status: status ?? (ok ? 200 : 500),
    statusText,
    json: vi.fn().mockResolvedValue(data),
  }
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
// URL construction (AC-3)
// ---------------------------------------------------------------------------

describe('fetchCheckpoints — URL construction', () => {
  it('GETs <serverUrl>/query/checkpoints with project and limit query params', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [MOCK_CHECKPOINT] }))
    await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    expect(mockFetch).toHaveBeenCalledOnce()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(`${MOCK_SERVER_URL}/query/checkpoints?project=depthfusion&limit=10`)
  })

  it('carries the runtime argument values, not hardcoded defaults', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchCheckpoints('https://df.example.com', 'other-project', 3)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(
      'https://df.example.com/query/checkpoints?project=other-project&limit=3',
    )
  })

  it('percent-encodes the project slug', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchCheckpoints(MOCK_SERVER_URL, 'my project/slug', 5)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(
      `${MOCK_SERVER_URL}/query/checkpoints?project=my%20project%2Fslug&limit=5`,
    )
  })
})

// ---------------------------------------------------------------------------
// Authorization header (AC-2)
// ---------------------------------------------------------------------------

describe('fetchCheckpoints — authorization', () => {
  it('calls loadTokens() internally via the real ipc wrapper', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    expect(mockInvoke).toHaveBeenCalledWith('load_tokens')
  })

  it('attaches Authorization: Bearer <access_token> when tokens are non-null', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    const [, init] = mockFetch.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: `Bearer ${MOCK_TOKEN}`,
    })
  })

  it('omits Authorization when loadTokens() resolves null', async () => {
    mockInvoke.mockImplementation((cmd: string) => {
      if (cmd === 'load_tokens') return Promise.resolve(null)
      return Promise.resolve(undefined)
    })
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [] }))
    await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    const [, init] = mockFetch.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('Authorization')
  })
})

// ---------------------------------------------------------------------------
// Return shape and error surfacing (AC-1, AC-4)
// ---------------------------------------------------------------------------

describe('fetchCheckpoints — response handling', () => {
  it('returns the parsed items array with the eight to_dict() field names intact', async () => {
    mockFetch.mockResolvedValue(
      makeJsonResponse({ items: [MOCK_CHECKPOINT, MOCK_CHECKPOINT_NULLS] }),
    )
    const items = await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    expect(items).toHaveLength(2)
    expect(Object.keys(items[0]).sort()).toEqual(
      [
        'checkpoint_id',
        'context_pct_at_checkpoint',
        'created_at',
        'files_modified',
        'git_stash_ref',
        'plan_state',
        'project_slug',
        'session_id',
      ].sort(),
    )
    expect(items[0].checkpoint_id).toBe('ckpt-001')
    expect(items[0].plan_state).toBe('T-861 data layer in progress')
    expect(items[0].files_modified).toEqual([
      'app/src/lib/checkpoints.ts',
      'src/depthfusion/api/query.py',
    ])
    expect(items[0].git_stash_ref).toBe('stash@{0}')
    expect(items[0].context_pct_at_checkpoint).toBe(62.5)
  })

  it('tolerates null git_stash_ref and null context_pct_at_checkpoint', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({ items: [MOCK_CHECKPOINT_NULLS] }))
    const items = await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    expect(items[0].git_stash_ref).toBeNull()
    expect(items[0].context_pct_at_checkpoint).toBeNull()
    expect(items[0].files_modified).toEqual([])
  })

  it('defaults to an empty array when items is absent', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}))
    const items = await fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)
    expect(items).toEqual([])
  })

  it('surfaces a readable error on a non-ok response', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}, false, 503, 'Service Unavailable'))
    await expect(fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10)).rejects.toThrow(
      'Checkpoints fetch failed: 503 Service Unavailable',
    )
  })

  it('does not parse the body when the response is not ok', async () => {
    const resp = makeJsonResponse({}, false, 401, 'Unauthorized')
    mockFetch.mockResolvedValue(resp)
    await expect(
      fetchCheckpoints(MOCK_SERVER_URL, 'depthfusion', 10),
    ).rejects.toThrow(/401/)
    expect(resp.json).not.toHaveBeenCalled()
  })
})

describe('formatFilesModified — S-253 AC-2 shows the files, not a count', () => {
  it('lists the file paths themselves', () => {
    expect(formatFilesModified(['src/a.py', 'src/b.py'])).toBe('src/a.py, src/b.py')
  })

  it('collapses beyond FILES_SHOWN to "+N more" without dropping the shown paths', () => {
    const files = ['a.py', 'b.py', 'c.py', 'd.py', 'e.py']
    expect(formatFilesModified(files)).toBe('a.py, b.py, c.py +2 more')
    expect(FILES_SHOWN).toBe(3)
  })

  it('renders exactly FILES_SHOWN paths with no "+N more" suffix', () => {
    expect(formatFilesModified(['a.py', 'b.py', 'c.py'])).toBe('a.py, b.py, c.py')
  })

  it('never renders a bare number for an empty list', () => {
    const out = formatFilesModified([])
    expect(out).toBe('no files')
    expect(out).not.toMatch(/^\d+$/)
  })
})
