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
  try {
    return await invoke<boolean>('check_server_health', { url })
  } catch {
    return false
  }
}

/**
 * Validate an Anthropic API key (sk-ant- prefix) and configure solo mode:
 * stores the key in the OS keychain vault, sets deployment_mode='solo',
 * and marks the wizard as completed.
 */
export async function setupSoloAuth(apiKey: string): Promise<void> {
  return invoke<void>('setup_solo_auth', { apiKey })
}

/**
 * Store a static bearer token and configure connect mode:
 * stores the token in the OS keychain vault with token_type='Bearer',
 * sets deployment_mode='connect', and marks the wizard as completed.
 */
export async function setupConnectAuth(bearerToken: string): Promise<void> {
  return invoke<void>('setup_connect_auth', { bearerToken })
}

// ---------------------------------------------------------------------------
// Cognitive / Memory endpoints (S-232)
// ---------------------------------------------------------------------------

export interface CognitivePersona {
  persona_trigger_every_n: number
  persona_last_updated: string | null
  memory_count_at_last_generation: number | null
}

export interface CognitiveOffload {
  offload_enabled: boolean
  offload_mmd_max_tokens: number
  refs_count: number
}

export interface CognitiveDistillation {
  configured_backend: string
  local_llm_url: string
  resolved_backend: string
}

export interface CognitiveStatus {
  depthfusion: string
  persona: CognitivePersona
  offload: CognitiveOffload
  distillation: CognitiveDistillation
  rlm_enabled: boolean
  router_enabled: boolean
  session_enabled: boolean
  fusion_enabled: boolean
}

export interface CognitiveScenario {
  project_id: string
  title: string
  summary: string
}

export interface CognitiveScenariosResponse {
  scenarios: CognitiveScenario[]
  project_ids: string[]
}

export interface BridgeResponse {
  node_id: string
  session_id: string
  text: string
  error?: string
}

async function _cognitiveHeaders(): Promise<HeadersInit> {
  const tokens = await loadTokens()
  return tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {}
}

/**
 * Fetch persona, offload, and distillation status from the cognitive layer.
 * Mirrors the depthfusion_status MCP tool via a dedicated REST endpoint.
 */
export async function getCognitiveStatus(): Promise<CognitiveStatus> {
  const [serverUrl, headers] = await Promise.all([
    getServerUrl(),
    _cognitiveHeaders(),
  ])
  const resp = await fetch(`${serverUrl}/api/v1/cognitive/status`, { headers })
  if (!resp.ok) throw new Error(`cognitive/status ${resp.status}`)
  return resp.json()
}

/**
 * Retrieve raw offloaded text for a refs/ node.
 * Mirrors the depthfusion_bridge MCP tool's node_id retrieval path.
 */
export async function fetchBridgeContent(
  nodeId: string,
  sessionId = '',
): Promise<BridgeResponse> {
  const [serverUrl, headers] = await Promise.all([
    getServerUrl(),
    _cognitiveHeaders(),
  ])
  const resp = await fetch(`${serverUrl}/api/v1/cognitive/bridge`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, session_id: sessionId }),
  })
  if (!resp.ok) throw new Error(`cognitive/bridge ${resp.status}`)
  return resp.json()
}

/**
 * Fetch parsed scenario blocks from the server's discoveries directory.
 */
export async function getCognitiveScenarios(): Promise<CognitiveScenariosResponse> {
  const [serverUrl, headers] = await Promise.all([
    getServerUrl(),
    _cognitiveHeaders(),
  ])
  const resp = await fetch(`${serverUrl}/api/v1/cognitive/scenarios`, { headers })
  if (!resp.ok) throw new Error(`cognitive/scenarios ${resp.status}`)
  return resp.json()
}
