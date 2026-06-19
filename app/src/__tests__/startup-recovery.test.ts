import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const { mockInvoke, mockListen } = vi.hoisted(() => ({
  mockInvoke: vi.fn(),
  mockListen: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))
vi.mock('@tauri-apps/api/event', () => ({ listen: mockListen }))

import {
  getAuthState,
  onAuthStateChange,
  pollAuthState,
  signOut,
  type AuthState,
  type TokenSet,
} from '../lib/auth'

const TOKEN_SET: TokenSet = {
  access_token: 'access-token',
  id_token: 'id-token',
  refresh_token: 'refresh-token',
  expires_in: 3600,
  token_type: 'Bearer',
}

beforeEach(() => {
  mockInvoke.mockReset()
  mockListen.mockReset()
  mockListen.mockResolvedValue(() => {})
  signOut()
})

afterEach(() => {
  signOut()
})

describe('app startup restart recovery', () => {
  it('restores authenticated state from poll_auth_state without starting login', async () => {
    const states: AuthState[] = []
    const unsub = onAuthStateChange((state) => states.push(state))

    mockInvoke.mockImplementation(async (command: string) => {
      if (command === 'poll_auth_state') return TOKEN_SET
      throw new Error(`unexpected command: ${command}`)
    })

    const token = await pollAuthState(200)

    expect(token).toEqual(TOKEN_SET)
    expect(getAuthState().status).toBe('authenticated')
    expect(getAuthState().token).toEqual(TOKEN_SET)
    expect(states.some((state) => state.status === 'authenticated')).toBe(true)
    expect(mockInvoke).toHaveBeenCalledWith('poll_auth_state')
    expect(mockInvoke).not.toHaveBeenCalledWith('start_login')
    expect(mockListen).not.toHaveBeenCalled()

    unsub()
  })

  it('rejects and remains unauthenticated when poll_auth_state returns null', async () => {
    const states: AuthState[] = []
    const unsub = onAuthStateChange((state) => states.push(state))

    mockInvoke.mockImplementation(async (command: string) => {
      if (command === 'poll_auth_state') return null
      throw new Error(`unexpected command: ${command}`)
    })

    await expect(pollAuthState(200)).rejects.toThrow('Authentication timed out')

    expect(getAuthState().status).not.toBe('authenticated')
    expect(getAuthState().status).toBe('error')
    expect(states.some((state) => state.status === 'authenticated')).toBe(false)
    expect(mockInvoke).toHaveBeenCalledWith('poll_auth_state')
    expect(mockInvoke).not.toHaveBeenCalledWith('start_login')

    unsub()
  })
})
