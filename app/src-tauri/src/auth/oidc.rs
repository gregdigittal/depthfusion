//! System-browser OIDC flow with PKCE and deep-link callback handling.
//!
//! Flow:
//!   1. `build_pkce_url()`   — generate verifier + challenge + state, return auth URL
//!   2. Open URL in system browser via tauri-plugin-shell
//!   3. Browser redirects to depthfusion://callback?code=...&state=...
//!   4. `handle_callback()` — validate state, call `exchange_code()`
//!   5. `exchange_code()`   — POST to token endpoint, return TokenSet

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use once_cell::sync::Lazy;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use tokio::sync::Mutex;
use url::Url;

// ---------------------------------------------------------------------------
// Pending-session store (in-memory, single-session app)
// ---------------------------------------------------------------------------

struct PkceSession {
    code_verifier: String,
    state: String,
}

static PENDING: Lazy<Mutex<Option<PkceSession>>> = Lazy::new(|| Mutex::new(None));

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// OIDC provider configuration sourced from environment or settings.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OidcConfig {
    pub issuer: String,
    pub client_id: String,
    /// Typically `depthfusion://callback`
    pub redirect_uri: String,
    /// Space-separated scopes, e.g. `"openid profile email offline_access"`
    pub scopes: String,
}

/// Returned to the frontend after a successful token exchange.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenSet {
    pub access_token: String,
    pub id_token: Option<String>,
    pub refresh_token: Option<String>,
    pub expires_in: Option<u64>,
    pub token_type: String,
}

/// Minimal error type for IPC serialisation.
#[derive(Debug, Serialize, Deserialize)]
pub struct OidcError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for OidcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}

impl OidcError {
    fn new(code: &str, msg: impl Into<String>) -> Self {
        Self { code: code.to_string(), message: msg.into() }
    }
}

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

fn random_bytes(n: usize) -> Vec<u8> {
    let mut buf = vec![0u8; n];
    rand::thread_rng().fill_bytes(&mut buf);
    buf
}

fn base64url(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(bytes)
}

fn s256_challenge(verifier: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    base64url(&hasher.finalize())
}

// ---------------------------------------------------------------------------
// Core functions
// ---------------------------------------------------------------------------

/// Build a PKCE authorisation URL.
///
/// Stores the `code_verifier` and `state` in the in-process session store so
/// that `handle_callback` can validate the deep-link response.
///
/// Returns the full URL string the caller should open in the system browser.
pub async fn build_pkce_url(config: &OidcConfig) -> Result<String, OidcError> {
    // Generate PKCE material
    let verifier_bytes = random_bytes(32);
    let code_verifier = base64url(&verifier_bytes);
    let code_challenge = s256_challenge(&code_verifier);

    // Generate opaque state token (CSRF protection)
    let state_bytes = random_bytes(16);
    let state = base64url(&state_bytes);

    // Persist session
    {
        let mut guard = PENDING.lock().await;
        *guard = Some(PkceSession { code_verifier: code_verifier.clone(), state: state.clone() });
    }

    // Build the authorisation endpoint URL
    // Convention: issuer + "/authorize" (works for Auth0, Keycloak, etc.)
    let auth_endpoint = format!("{}/authorize", config.issuer.trim_end_matches('/'));

    let mut url = Url::parse(&auth_endpoint).map_err(|e| {
        OidcError::new("INVALID_ISSUER", format!("Cannot parse issuer URL: {e}"))
    })?;

    url.query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", &config.client_id)
        .append_pair("redirect_uri", &config.redirect_uri)
        .append_pair("scope", &config.scopes)
        .append_pair("state", &state)
        .append_pair("code_challenge", &code_challenge)
        .append_pair("code_challenge_method", "S256");

    Ok(url.to_string())
}

/// Validate the deep-link callback parameters and exchange the code.
///
/// `params` is a map parsed from the query string of the deep-link URI.
///
/// Returns a `TokenSet` on success.
pub async fn handle_callback(
    config: &OidcConfig,
    params: HashMap<String, String>,
) -> Result<TokenSet, OidcError> {
    // Extract and validate required fields
    let code = params
        .get("code")
        .ok_or_else(|| OidcError::new("MISSING_CODE", "No 'code' in callback params"))?
        .clone();

    let returned_state = params
        .get("state")
        .ok_or_else(|| OidcError::new("MISSING_STATE", "No 'state' in callback params"))?
        .clone();

    // Check for error response from IdP
    if let Some(err) = params.get("error") {
        let desc = params.get("error_description").map(|s| s.as_str()).unwrap_or("no description");
        return Err(OidcError::new("IDP_ERROR", format!("{err}: {desc}")));
    }

    // Retrieve and clear the pending session
    let session = {
        let mut guard = PENDING.lock().await;
        guard.take().ok_or_else(|| {
            OidcError::new("NO_SESSION", "No pending OIDC session found — login may have timed out")
        })?
    };

    // CSRF: validate state
    if session.state != returned_state {
        return Err(OidcError::new(
            "STATE_MISMATCH",
            "Callback state does not match the expected value",
        ));
    }

    exchange_code(config, &code, &session.code_verifier).await
}

/// POST to the token endpoint and return a `TokenSet`.
///
/// Uses `code_verifier` for PKCE proof.
pub async fn exchange_code(
    config: &OidcConfig,
    code: &str,
    code_verifier: &str,
) -> Result<TokenSet, OidcError> {
    let token_endpoint = format!("{}/token", config.issuer.trim_end_matches('/'));

    let client = reqwest::Client::new();

    let mut form = HashMap::new();
    form.insert("grant_type", "authorization_code");
    form.insert("client_id", &config.client_id);
    form.insert("code", code);
    form.insert("redirect_uri", &config.redirect_uri);
    form.insert("code_verifier", code_verifier);

    let resp = client
        .post(&token_endpoint)
        .form(&form)
        .send()
        .await
        .map_err(|e| OidcError::new("HTTP_ERROR", format!("Token request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(OidcError::new(
            "TOKEN_ERROR",
            format!("Token endpoint returned {status}: {body}"),
        ));
    }

    let raw: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| OidcError::new("PARSE_ERROR", format!("Failed to parse token response: {e}")))?;

    let access_token = raw
        .get("access_token")
        .and_then(|v| v.as_str())
        .ok_or_else(|| OidcError::new("MISSING_ACCESS_TOKEN", "Token response missing access_token"))?
        .to_string();

    Ok(TokenSet {
        access_token,
        id_token: raw.get("id_token").and_then(|v| v.as_str()).map(|s| s.to_string()),
        refresh_token: raw.get("refresh_token").and_then(|v| v.as_str()).map(|s| s.to_string()),
        expires_in: raw.get("expires_in").and_then(|v| v.as_u64()),
        token_type: raw
            .get("token_type")
            .and_then(|v| v.as_str())
            .unwrap_or("Bearer")
            .to_string(),
    })
}
