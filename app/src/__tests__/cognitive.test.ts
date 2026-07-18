/**
 * S-232 T-809 — unit tests for cognitive ipc functions.
 *
 * Environment is Node (no jsdom) so we test the pure fetch-wrapper layer
 * rather than rendering components. Component behaviour is covered by
 * manual integration testing against the running server.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// Hoist mocks before any module imports
// ---------------------------------------------------------------------------

const { mockInvoke } = vi.hoisted(() => ({ mockInvoke: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))

// We need to control fetch. Assign a vi.fn to globalThis.fetch before the
// ipc module is imported (hoisted), then replace it per-test.
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

import {
  getCognitiveStatus,
  getCognitiveScenarios,
  fetchBridgeContent,
  type CognitiveStatus,
  type CognitiveScenariosResponse,
  type BridgeResponse,
} from '../lib/ipc'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeJsonResponse(data: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    json: vi.fn().mockResolvedValue(data),
  }
}

const MOCK_SERVER_URL = 'http://localhost:7301'
const MOCK_TOKEN = 'test-bearer-token'

const MOCK_STATUS: CognitiveStatus = {
  depthfusion: '2.1.1',
  persona: {
    persona_trigger_every_n: 10,
    persona_last_updated: '2026-07-18T00:00:00Z',
    memory_count_at_last_generation: 42,
  },
  offload: {
    offload_enabled: true,
    offload_mmd_max_tokens: 4096,
    refs_count: 7,
  },
  distillation: {
    configured_backend: 'anthropic',
    local_llm_url: '',
    resolved_backend: 'anthropic',
  },
  rlm_enabled: true,
  router_enabled: false,
  session_enabled: true,
  fusion_enabled: true,
}

const MOCK_SCENARIOS: CognitiveScenariosResponse = {
  scenarios: [{ project_id: 'depthfusion', title: 'API design pattern', summary: 'REST over MCP' }],
  project_ids: ['depthfusion'],
}

const MOCK_BRIDGE: BridgeResponse = {
  node_id: 'abc123',
  session_id: '',
  text: 'offloaded context text here',
}

beforeEach(() => {
  vi.clearAllMocks()
  // Default: server URL resolves, tokens resolve
  mockInvoke.mockImplementation((cmd: string) => {
    if (cmd === 'get_server_url') return Promise.resolve(MOCK_SERVER_URL)
    if (cmd === 'load_tokens') return Promise.resolve({ access_token: MOCK_TOKEN, id_token: null, refresh_token: null, expires_in: 3600, token_type: 'Bearer' })
    return Promise.resolve(undefined)
  })
})

// ---------------------------------------------------------------------------
// getCognitiveStatus
// ---------------------------------------------------------------------------

describe('getCognitiveStatus', () => {
  it('fetches GET /api/v1/cognitive/status and returns parsed JSON', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_STATUS))
    const result = await getCognitiveStatus()
    expect(mockFetch).toHaveBeenCalledOnce()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(`${MOCK_SERVER_URL}/api/v1/cognitive/status`)
    expect(result.persona.memory_count_at_last_generation).toBe(42)
    expect(result.offload.refs_count).toBe(7)
  })

  it('includes Authorization header when tokens are available', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_STATUS))
    await getCognitiveStatus()
    const [, init] = mockFetch.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: `Bearer ${MOCK_TOKEN}`,
    })
  })

  it('does not include Authorization header when no tokens', async () => {
    mockInvoke.mockImplementation((cmd: string) => {
      if (cmd === 'get_server_url') return Promise.resolve(MOCK_SERVER_URL)
      if (cmd === 'load_tokens') return Promise.resolve(null)
      return Promise.resolve(undefined)
    })
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_STATUS))
    await getCognitiveStatus()
    const [, init] = mockFetch.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('Authorization')
  })

  it('throws when server returns non-2xx', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}, false))
    await expect(getCognitiveStatus()).rejects.toThrow('cognitive/status 500')
  })
})

// ---------------------------------------------------------------------------
// getCognitiveScenarios
// ---------------------------------------------------------------------------

describe('getCognitiveScenarios', () => {
  it('fetches GET /api/v1/cognitive/scenarios', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_SCENARIOS))
    const result = await getCognitiveScenarios()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(`${MOCK_SERVER_URL}/api/v1/cognitive/scenarios`)
    expect(result.scenarios).toHaveLength(1)
    expect(result.scenarios[0].title).toBe('API design pattern')
  })

  it('throws when server returns non-2xx', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}, false))
    await expect(getCognitiveScenarios()).rejects.toThrow('cognitive/scenarios 500')
  })
})

// ---------------------------------------------------------------------------
// fetchBridgeContent
// ---------------------------------------------------------------------------

describe('fetchBridgeContent', () => {
  it('POSTs to /api/v1/cognitive/bridge with node_id in body', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_BRIDGE))
    const result = await fetchBridgeContent('abc123')
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(`${MOCK_SERVER_URL}/api/v1/cognitive/bridge`)
    expect((init as RequestInit).method).toBe('POST')
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.node_id).toBe('abc123')
    expect(result.text).toBe('offloaded context text here')
  })

  it('includes Content-Type application/json header', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_BRIDGE))
    await fetchBridgeContent('abc123')
    const [, init] = mockFetch.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/json',
    })
  })

  it('passes optional session_id in body', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse(MOCK_BRIDGE))
    await fetchBridgeContent('abc123', 'sess-456')
    const [, init] = mockFetch.mock.calls[0]
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.session_id).toBe('sess-456')
  })

  it('throws when server returns non-2xx', async () => {
    mockFetch.mockResolvedValue(makeJsonResponse({}, false))
    await expect(fetchBridgeContent('abc123')).rejects.toThrow('cognitive/bridge 500')
  })
})
