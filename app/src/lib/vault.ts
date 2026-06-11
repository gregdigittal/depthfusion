/**
 * vault.ts — TypeScript wrapper for the Rust-side OS keychain token vault.
 *
 * Uses Tauri's typed `invoke<T>()` for all IPC calls so callers get full
 * TypeScript inference without unsafe casts.
 *
 * Platform notes (handled transparently by the Rust layer):
 *   macOS   → Security framework keychain
 *   Windows → DPAPI / Windows Credential Manager
 *   Linux   → Secret Service via DBus (e.g. GNOME Keyring, KWallet)
 */

import { invoke } from '@tauri-apps/api/core'

/** Mirror of the Rust `vault::TokenSet` struct. */
export interface TokenSet {
  access_token: string
  id_token: string | null
  refresh_token: string | null
  expires_in: number | null
  token_type: string
}

/**
 * Persist `tokens` in the OS keychain.
 *
 * Overwrites any previously stored session.
 *
 * @throws {string} Human-readable error from the Rust layer on keychain failure.
 */
export async function storeTokens(tokens: TokenSet): Promise<void> {
  return invoke<void>('store_tokens', { tokens })
}

/**
 * Load the stored `TokenSet` from the OS keychain.
 *
 * Returns `null` when no entry is present (first run / after sign-out) — this
 * is NOT an error condition.
 *
 * @throws {string} Human-readable error from the Rust layer on keychain failure.
 */
export async function loadTokens(): Promise<TokenSet | null> {
  return invoke<TokenSet | null>('load_tokens')
}

/**
 * Delete the stored `TokenSet` from the OS keychain.
 *
 * Idempotent — succeeds even when no entry exists.
 *
 * @throws {string} Human-readable error from the Rust layer on keychain failure.
 */
export async function clearTokens(): Promise<void> {
  return invoke<void>('clear_tokens')
}
