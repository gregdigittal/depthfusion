import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// vi.hoisted() runs BEFORE vi.mock() hoisting so variables are defined
// when the factory functions execute.
const { mockInvoke, mockListen } = vi.hoisted(() => ({
  mockInvoke: vi.fn(),
  mockListen: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))
vi.mock('@tauri-apps/api/event', () => ({ listen: mockListen }))

import {
  getAuthState,
  onAuthStateChange,
  signOut,
  startLogin,
  type AuthState,
} from '../lib/auth'

// Reset module-level singleton state before each test
beforeEach(() => {
  mockInvoke.mockReset()
  mockListen.mockReset()
  // listen() must return an unlisten function; default is no-op
  mockListen.mockResolvedValue(() => {})
  signOut()
})

afterEach(() => {
  signOut()
})

// ---------------------------------------------------------------------------
// getAuthState
// ---------------------------------------------------------------------------

describe('getAuthState', () => {
  it('returns idle state on first load', () => {
    const state = getAuthState()
    expect(state.status).toBe('idle')
  })
})

// ---------------------------------------------------------------------------
// onAuthStateChange — subscription / unsubscribe
// ---------------------------------------------------------------------------

describe('onAuthStateChange', () => {
  it('returns an unsubscribe function', () => {
    const fn = vi.fn()
    const unsub = onAuthStateChange(fn)
    expect(typeof unsub).toBe('function')
    unsub()
  })

  it('notifies subscriber when state changes via signOut', () => {
    const received: AuthState[] = []
    const unsub = onAuthStateChange((s) => received.push(s))

    signOut()
    expect(received).toHaveLength(1)
    expect(received[0].status).toBe('idle')
    unsub()
  })

  it('does not notify after unsubscribe', () => {
    const fn = vi.fn()
    const unsub = onAuthStateChange(fn)
    unsub()
    signOut()
    expect(fn).not.toHaveBeenCalled()
  })

  it('supports multiple simultaneous subscribers', () => {
    const a = vi.fn()
    const b = vi.fn()
    const ua = onAuthStateChange(a)
    const ub = onAuthStateChange(b)

    signOut()
    expect(a).toHaveBeenCalledOnce()
    expect(b).toHaveBeenCalledOnce()
    ua()
    ub()
  })
})

// ---------------------------------------------------------------------------
// signOut
// ---------------------------------------------------------------------------

describe('signOut', () => {
  it('resets state to idle', () => {
    signOut()
    expect(getAuthState().status).toBe('idle')
  })
})

// ---------------------------------------------------------------------------
// startLogin
// ---------------------------------------------------------------------------

describe('startLogin', () => {
  it('transitions state to pending before opening the browser', async () => {
    const states: string[] = []
    const unsub = onAuthStateChange((s) => states.push(s.status))

    mockInvoke.mockResolvedValue('https://auth.example.com/authorize?code_challenge=xxx')

    const url = await startLogin()

    expect(states[0]).toBe('pending')
    expect(url).toContain('https://auth.example.com')
    expect(mockInvoke).toHaveBeenCalledWith('start_login')
    unsub()
  })

  it('transitions to error when invoke("start_login") throws', async () => {
    const states: string[] = []
    const unsub = onAuthStateChange((s) => states.push(s.status))

    mockInvoke.mockRejectedValue('IPC error')

    await expect(startLogin()).rejects.toBe('IPC error')
    expect(states).toContain('error')
    expect(getAuthState().status).toBe('error')
    unsub()
  })

  it('registers the deep-link listener before invoking start_login', async () => {
    mockInvoke.mockResolvedValue('https://auth.example.com/auth')

    await startLogin()

    expect(mockListen).toHaveBeenCalledWith(
      'deep-link://new-url',
      expect.any(Function),
    )
    // listen must be called BEFORE invoke (listener registered before browser opens)
    const listenOrder = mockListen.mock.invocationCallOrder[0]
    const invokeOrder = mockInvoke.mock.invocationCallOrder[0]
    expect(listenOrder).toBeLessThan(invokeOrder)
  })
})

// ---------------------------------------------------------------------------
// Deep-link callback → authenticated (S-216 AC-4, S-217 AC-3)
// ---------------------------------------------------------------------------

describe('deep-link callback handling', () => {
  it('transitions to authenticated when depthfusion://callback arrives', async () => {
    // Capture the handler registered by listenForDeepLink()
    let capturedHandler: ((event: { payload: string[] }) => Promise<void>) | null = null
    mockListen.mockImplementation(
      (_event: string, handler: (e: { payload: string[] }) => Promise<void>) => {
        capturedHandler = handler
        return Promise.resolve(() => {})
      },
    )

    const fakeTokenSet = {
      access_token: 'tok-abc',
      id_token: 'id-tok',
      refresh_token: null,
      expires_in: 3600,
      token_type: 'Bearer',
    }
    mockInvoke
      .mockResolvedValueOnce('https://auth.example.com/auth')  // start_login
      .mockResolvedValueOnce(fakeTokenSet)                      // handle_deep_link

    const states: string[] = []
    const unsub = onAuthStateChange((s) => states.push(s.status))

    await startLogin()
    await capturedHandler!({ payload: ['depthfusion://callback?code=abc&state=xyz'] })

    expect(states).toContain('authenticated')
    expect(getAuthState().status).toBe('authenticated')
    expect(getAuthState().token?.access_token).toBe('tok-abc')
    expect(mockInvoke).toHaveBeenCalledWith('handle_deep_link', {
      rawUrl: 'depthfusion://callback?code=abc&state=xyz',
    })
    unsub()
  })

  it('transitions to error when handle_deep_link exchange fails', async () => {
    let capturedHandler: ((event: { payload: string[] }) => Promise<void>) | null = null
    mockListen.mockImplementation(
      (_event: string, handler: (e: { payload: string[] }) => Promise<void>) => {
        capturedHandler = handler
        return Promise.resolve(() => {})
      },
    )
    mockInvoke
      .mockResolvedValueOnce('https://auth.example.com/auth')
      .mockRejectedValueOnce(new Error('token exchange failed'))

    const states: string[] = []
    const unsub = onAuthStateChange((s) => states.push(s.status))

    await startLogin()
    await capturedHandler!({ payload: ['depthfusion://callback?code=bad'] })

    expect(getAuthState().status).toBe('error')
    expect(states).toContain('error')
    unsub()
  })

  it('ignores deep-link URLs that are not depthfusion://callback', async () => {
    let capturedHandler: ((event: { payload: string[] }) => Promise<void>) | null = null
    mockListen.mockImplementation(
      (_event: string, handler: (e: { payload: string[] }) => Promise<void>) => {
        capturedHandler = handler
        return Promise.resolve(() => {})
      },
    )
    mockInvoke.mockResolvedValueOnce('https://auth.example.com/auth')

    const states: string[] = []
    const unsub = onAuthStateChange((s) => states.push(s.status))

    await startLogin()
    await capturedHandler!({ payload: ['https://unrelated.example.com/other'] })

    // Unrelated URL — state must remain pending (startLogin set it to pending)
    expect(getAuthState().status).toBe('pending')
    expect(mockInvoke).not.toHaveBeenCalledWith('handle_deep_link', expect.anything())
    unsub()
  })
})
