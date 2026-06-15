import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockInvoke } = vi.hoisted(() => ({ mockInvoke: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))

import { storeTokens, loadTokens, clearTokens, type TokenSet } from '../lib/vault'

const SAMPLE_TOKENS: TokenSet = {
  access_token: 'access.tok.en',
  id_token: 'id.tok.en',
  refresh_token: 'refresh.tok.en',
  expires_in: 3600,
  stored_at: 1_700_000_000,
  token_type: 'Bearer',
}

beforeEach(() => {
  mockInvoke.mockReset()
})

describe('storeTokens', () => {
  it('calls invoke("store_tokens") with the token set', async () => {
    mockInvoke.mockResolvedValue(undefined)
    await storeTokens(SAMPLE_TOKENS)
    expect(mockInvoke).toHaveBeenCalledOnce()
    expect(mockInvoke).toHaveBeenCalledWith('store_tokens', { tokens: SAMPLE_TOKENS })
  })

  it('propagates invoke errors to the caller', async () => {
    mockInvoke.mockRejectedValue('Keychain locked')
    await expect(storeTokens(SAMPLE_TOKENS)).rejects.toBe('Keychain locked')
  })
})

describe('loadTokens', () => {
  it('calls invoke("load_tokens") and returns the token set', async () => {
    mockInvoke.mockResolvedValue(SAMPLE_TOKENS)
    const result = await loadTokens()
    expect(mockInvoke).toHaveBeenCalledWith('load_tokens')
    expect(result).toEqual(SAMPLE_TOKENS)
  })

  it('returns null when no tokens are stored', async () => {
    mockInvoke.mockResolvedValue(null)
    const result = await loadTokens()
    expect(result).toBeNull()
  })

  it('propagates invoke errors to the caller', async () => {
    mockInvoke.mockRejectedValue('DBus error')
    await expect(loadTokens()).rejects.toBe('DBus error')
  })
})

describe('clearTokens', () => {
  it('calls invoke("clear_tokens") with no additional arguments', async () => {
    mockInvoke.mockResolvedValue(undefined)
    await clearTokens()
    expect(mockInvoke).toHaveBeenCalledWith('clear_tokens')
  })

  it('propagates invoke errors to the caller', async () => {
    mockInvoke.mockRejectedValue('Keychain item not found')
    await expect(clearTokens()).rejects.toBe('Keychain item not found')
  })
})
