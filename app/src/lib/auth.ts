/**
 * auth.ts — TypeScript side of the system-browser OIDC flow.
 *
 * Flow:
 *   1. startLogin()       — Rust builds a PKCE URL, opens system browser
 *   2. User authenticates — browser redirects to depthfusion://callback?code=...
 *   3. Tauri deep-link plugin fires an event → handleDeepLinkEvent()
 *   4. pollAuthState()    — frontend polls until a TokenSet is available
 */

import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

// ---------------------------------------------------------------------------
// Types (mirroring Rust structs)
// ---------------------------------------------------------------------------

export interface TokenSet {
  access_token: string
  id_token: string | null
  refresh_token: string | null
  expires_in: number | null
  token_type: string
}

export interface AuthState {
  status: 'idle' | 'pending' | 'authenticated' | 'error'
  token?: TokenSet
  error?: string
}

// ---------------------------------------------------------------------------
// Module-level auth state
// ---------------------------------------------------------------------------

let _authState: AuthState = { status: 'idle' }
let _deepLinkUnlisten: (() => void) | null = null

// Subscribers notified on every state change
const _listeners = new Set<(state: AuthState) => void>()

function setState(next: AuthState): void {
  _authState = next
  _listeners.forEach((fn) => fn(next))
}

/** Subscribe to auth state changes. Returns an unsubscribe function. */
export function onAuthStateChange(fn: (state: AuthState) => void): () => void {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}

/** Read the current auth state synchronously. */
export function getAuthState(): AuthState {
  return _authState
}

// ---------------------------------------------------------------------------
// Deep-link handler setup
// ---------------------------------------------------------------------------

/**
 * Register a listener for the `deep-link://new-url` event emitted by the
 * Tauri deep-link plugin when the OS delivers a `depthfusion://` URI.
 *
 * This is called automatically by `startLogin()` and torn down after the
 * callback is processed (or on error).
 */
async function listenForDeepLink(): Promise<void> {
  // Avoid double-registering
  if (_deepLinkUnlisten) return

  const unlisten = await listen<string[]>('deep-link://new-url', async (event) => {
    const urls: string[] = event.payload
    const callbackUrl = urls.find((u) => u.startsWith('depthfusion://callback'))

    if (!callbackUrl) return

    // Tear down listener immediately — we only expect one callback
    _deepLinkUnlisten?.()
    _deepLinkUnlisten = null

    try {
      const tokens = await invoke<TokenSet>('handle_deep_link', { rawUrl: callbackUrl })
      setState({ status: 'authenticated', token: tokens })
    } catch (err) {
      setState({ status: 'error', error: String(err) })
    }
  })

  _deepLinkUnlisten = unlisten
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Kick off the sign-in flow.
 *
 * Opens the system browser with a PKCE-protected OIDC authorisation URL.
 * Registers a deep-link listener so that when the callback arrives the tokens
 * are exchanged automatically.
 *
 * @returns The authorisation URL that was opened (useful for debugging).
 */
export async function startLogin(): Promise<string> {
  setState({ status: 'pending' })

  // Set up deep-link listener before opening the browser so we never miss the
  // callback, even on very fast redirects.
  await listenForDeepLink()

  try {
    const url = await invoke<string>('start_login')
    return url
  } catch (err) {
    setState({ status: 'error', error: String(err) })
    throw err
  }
}

/**
 * Poll auth state with exponential back-off.
 *
 * Resolves with a `TokenSet` once the deep-link callback has been processed,
 * or rejects after `timeoutMs` milliseconds.
 *
 * In the current implementation the deep-link event drives state directly, so
 * this function simply waits for `_authState` to become `authenticated` (or
 * `error`). The Rust `poll_auth_state` command is also called as a fallback
 * in case the event was missed (e.g. when the OS delivered the link before
 * the listener was ready).
 */
export async function pollAuthState(timeoutMs = 120_000): Promise<TokenSet> {
  const deadline = Date.now() + timeoutMs
  let delay = 500

  while (Date.now() < deadline) {
    // Check Rust-side state (T-630 vault will populate this path)
    const rustState = await invoke<TokenSet | null>('poll_auth_state')
    if (rustState) {
      setState({ status: 'authenticated', token: rustState })
      return rustState
    }

    // Check JS-side state (populated by the deep-link listener)
    if (_authState.status === 'authenticated' && _authState.token) {
      return _authState.token
    }

    if (_authState.status === 'error') {
      throw new Error(_authState.error ?? 'Authentication failed')
    }

    await sleep(delay)
    delay = Math.min(delay * 1.5, 5_000)
  }

  setState({ status: 'error', error: 'Authentication timed out' })
  throw new Error('Authentication timed out')
}

/**
 * Sign the user out by clearing local auth state.
 *
 * Token vault wipe is handled by T-630 (Rust side).
 */
export function signOut(): void {
  _deepLinkUnlisten?.()
  _deepLinkUnlisten = null
  setState({ status: 'idle' })
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
