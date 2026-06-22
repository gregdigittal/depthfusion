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

// ---------------------------------------------------------------------------
// Setup wizard (E-65)
// ---------------------------------------------------------------------------

/** Whether the first-run setup wizard has been completed. */
export async function getWizardCompleted(): Promise<boolean> {
  return invoke<boolean>('get_wizard_completed')
}

/** Persist the wizard-completed flag. */
export async function setWizardCompleted(completed: boolean): Promise<void> {
  return invoke<void>('set_wizard_completed', { completed })
}

/** Retrieve the stored deployment mode: 'solo' | 'vps' | 'connect' | null. */
export async function getDeploymentMode(): Promise<string | null> {
  return invoke<string | null>('get_deployment_mode')
}

/** Persist the deployment mode. */
export async function setDeploymentMode(mode: string): Promise<void> {
  return invoke<void>('set_deployment_mode', { mode })
}

/**
 * Check whether a DepthFusion server at `url` is reachable and healthy.
 * Returns true when the server responds with 2xx on GET {url}/health.
 * Returns false on non-2xx or any network error — never rejects.
 */
export async function checkServerHealth(url: string): Promise<boolean> {
  return invoke<boolean>('check_server_health', { url })
}

/**
 * Validate an Anthropic API key (sk-ant- prefix) and configure solo mode:
 * stores the key in the OS keychain vault, sets deployment_mode='solo',
 * and marks the wizard as completed.
 */
export async function setupSoloAuth(apiKey: string): Promise<void> {
  return invoke<void>('setup_solo_auth', { apiKey })
}
