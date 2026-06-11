import { invoke } from '@tauri-apps/api/core'

export interface AppInfo {
  version: string
  name: string
}

export async function getAppInfo(): Promise<AppInfo> {
  return invoke<AppInfo>('get_app_info')
}

export async function ping(message: string): Promise<string> {
  return invoke<string>('ping', { message })
}

// ---------------------------------------------------------------------------
// Settings — server URL (T-634)
// ---------------------------------------------------------------------------

/** Retrieve the stored server URL (defaults to https://localhost:8000). */
export async function getServerUrl(): Promise<string> {
  return invoke<string>('get_server_url')
}

/** Persist the server URL to the Tauri store plugin. */
export async function setServerUrl(url: string): Promise<void> {
  return invoke<void>('set_server_url', { url })
}

// ---------------------------------------------------------------------------
// Auth helpers re-exported for Settings page (T-634)
// ---------------------------------------------------------------------------

export interface TokenSet {
  access_token: string
  id_token: string | null
  refresh_token: string | null
  expires_in: number | null
  token_type: string
}

export async function loadTokens(): Promise<TokenSet | null> {
  return invoke<TokenSet | null>('load_tokens')
}

export async function logoutUser(): Promise<void> {
  return invoke<void>('logout')
}
