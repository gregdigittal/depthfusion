/// Tauri IPC commands for the OIDC auth flow, token vault, and sign-out.

use std::collections::HashMap;
use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

use super::logout;
use super::oidc::{self, OidcConfig, OidcError, TokenSet};
use super::vault;

fn default_config() -> OidcConfig {
    OidcConfig {
        issuer: std::env::var("OIDC_ISSUER")
            .unwrap_or_else(|_| "https://auth.depthfusion.ai".to_string()),
        client_id: std::env::var("OIDC_CLIENT_ID")
            .unwrap_or_else(|_| "depthfusion-desktop".to_string()),
        redirect_uri: "depthfusion://callback".to_string(),
        scopes: "openid profile email offline_access".to_string(),
    }
}

/// Start the login flow: build a PKCE URL and open it in the system browser.
///
/// The frontend should call `pollAuthState` afterwards to wait for the result.
#[tauri::command]
pub async fn start_login(app: AppHandle) -> Result<String, String> {
    let config = default_config();

    let url = oidc::build_pkce_url(&config).await.map_err(|e| e.to_string())?;

    app.opener().open_url(&url, None::<&str>).map_err(|e| format!("Failed to open browser: {e}"))?;

    Ok(url)
}

/// Receive the deep-link callback from the system browser.
///
/// `raw_url` is the full deep-link URI, e.g.
/// `depthfusion://callback?code=abc&state=xyz`.
///
/// Returns a serialised `TokenSet` on success.
#[tauri::command]
pub async fn handle_deep_link(raw_url: String) -> Result<TokenSet, String> {
    let config = default_config();

    let parsed = url::Url::parse(&raw_url)
        .map_err(|e| format!("Invalid deep-link URL: {e}"))?;

    let params: HashMap<String, String> = parsed.query_pairs().into_owned().collect();

    oidc::handle_callback(&config, params).await.map_err(|e: OidcError| e.to_string())
}

/// Poll the current auth state.
///
/// Returns the cached `TokenSet` from the OS keychain if one exists, otherwise
/// `None` (frontend should then trigger `startLogin()`).
#[tauri::command]
pub async fn poll_auth_state() -> Option<TokenSet> {
    // T-630: check the vault for a cached session.
    match vault::load_tokens() {
        Ok(Some(vt)) => {
            // Re-export vault::TokenSet as the canonical oidc::TokenSet shape.
            Some(TokenSet {
                access_token: vt.access_token,
                id_token: vt.id_token,
                refresh_token: vt.refresh_token,
                expires_in: vt.expires_in,
                token_type: vt.token_type,
            })
        }
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Vault IPC commands (T-630)
// ---------------------------------------------------------------------------

/// Store a `TokenSet` in the OS keychain.
///
/// Overwrites any previously stored session.
#[tauri::command]
pub fn store_tokens(tokens: vault::TokenSet) -> Result<(), String> {
    vault::store_tokens(&tokens).map_err(|e| e.to_string())
}

/// Load the `TokenSet` from the OS keychain.
///
/// Returns `null` (None) when no entry is present — not an error.
#[tauri::command]
pub fn load_tokens() -> Result<Option<vault::TokenSet>, String> {
    vault::load_tokens().map_err(|e| e.to_string())
}

/// Delete the stored `TokenSet` from the OS keychain.
///
/// Idempotent — succeeds even when no entry exists.
#[tauri::command]
pub fn clear_tokens() -> Result<(), String> {
    vault::clear_tokens().map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// Sign-out / local wipe (T-631)
// ---------------------------------------------------------------------------

/// Sign the user out and wipe all locally stored session data.
///
/// Clears:
///   - OS keychain token vault
///   - Tauri `AppData` / `userData` directory
///   - Any `depthfusion-*` temp files
///
/// Non-fatal wipe errors (e.g. a temp file already removed by the OS) are
/// collected and returned as a single error string so the frontend can decide
/// whether to surface a warning. If the vault itself fails to clear, that is
/// considered fatal and returned as `Err`.
#[tauri::command]
pub fn logout(app: AppHandle) -> Result<(), String> {
    // Retrieve the app's local data directory from Tauri's path resolver.
    let app_data_dir: Option<std::path::PathBuf> = app
        .path()
        .app_local_data_dir()
        .ok();

    let errors = logout::wipe_local_state(app_data_dir);

    // If any error came from the vault step, treat as fatal.
    let fatal: Vec<_> = errors.iter().filter(|e| e.step == "vault").collect();
    if !fatal.is_empty() {
        return Err(fatal.iter().map(|e| e.to_string()).collect::<Vec<_>>().join("; "));
    }

    // Non-fatal errors (temp files, app_data) — log them but don't block sign-out.
    for e in &errors {
        log::warn!("[logout] non-fatal wipe error: {e}");
    }

    Ok(())
}
