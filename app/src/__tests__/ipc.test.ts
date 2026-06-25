import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockInvoke } = vi.hoisted(() => ({ mockInvoke: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))

import {
  setupSoloAuth,
  setupConnectAuth,
  setWizardCompleted,
  checkServerHealth,
} from '../lib/ipc'

beforeEach(() => {
  mockInvoke.mockReset()
})

// S-215 AC-3: setupSoloAuth must call invoke('setup_solo_auth', { apiKey })
// so the Rust handler can persist the key via vault::store_tokens.
describe('setupSoloAuth', () => {
  it('calls invoke("setup_solo_auth") with the api key', async () => {
    mockInvoke.mockResolvedValue(undefined)
    await setupSoloAuth('sk-ant-api03-test-key')
    expect(mockInvoke).toHaveBeenCalledOnce()
    expect(mockInvoke).toHaveBeenCalledWith('setup_solo_auth', { apiKey: 'sk-ant-api03-test-key' })
  })

  it('propagates invoke errors to the caller', async () => {
    mockInvoke.mockRejectedValue('Keychain locked')
    await expect(setupSoloAuth('sk-ant-api03-test-key')).rejects.toBe('Keychain locked')
  })
})

// S-218: setupConnectAuth stores bearer token via setup_connect_auth command.
describe('setupConnectAuth', () => {
  it('calls invoke("setup_connect_auth") with the bearer token', async () => {
    mockInvoke.mockResolvedValue(undefined)
    await setupConnectAuth('3cea56481975dc53587e8d99cfa989c3ab8b1c3e5e44792443832f4cf8c1f317')
    expect(mockInvoke).toHaveBeenCalledOnce()
    expect(mockInvoke).toHaveBeenCalledWith('setup_connect_auth', {
      bearerToken: '3cea56481975dc53587e8d99cfa989c3ab8b1c3e5e44792443832f4cf8c1f317',
    })
  })

  it('propagates invoke errors to the caller', async () => {
    mockInvoke.mockRejectedValue('Keychain locked')
    await expect(setupConnectAuth('some-token')).rejects.toBe('Keychain locked')
  })
})

// S-214 AC-4: setWizardCompleted(false) is what "Re-run setup wizard" calls.
describe('setWizardCompleted', () => {
  it('calls invoke("set_wizard_completed") with the completed flag', async () => {
    mockInvoke.mockResolvedValue(undefined)
    await setWizardCompleted(false)
    expect(mockInvoke).toHaveBeenCalledWith('set_wizard_completed', { completed: false })
  })

  it('propagates invoke errors to the caller', async () => {
    mockInvoke.mockRejectedValue('Store write failed')
    await expect(setWizardCompleted(true)).rejects.toBe('Store write failed')
  })
})

// checkServerHealth is used in SoloInstallScreen polling.
describe('checkServerHealth', () => {
  it('calls invoke("check_server_health") and returns true', async () => {
    mockInvoke.mockResolvedValue(true)
    expect(await checkServerHealth('http://localhost:7301')).toBe(true)
    expect(mockInvoke).toHaveBeenCalledWith('check_server_health', { url: 'http://localhost:7301' })
  })

  it('calls invoke("check_server_health") and returns false', async () => {
    mockInvoke.mockResolvedValue(false)
    expect(await checkServerHealth('http://unreachable')).toBe(false)
    expect(mockInvoke).toHaveBeenCalledWith('check_server_health', { url: 'http://unreachable' })
  })

  it('returns false when invoke rejects (never rejects contract)', async () => {
    mockInvoke.mockRejectedValue(new Error('network timeout'))
    expect(await checkServerHealth('http://localhost:7301')).toBe(false)
  })
})
