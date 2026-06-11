/// Tauri IPC commands for the OIDC auth flow.

use std::collections::HashMap;
use tauri::AppHandle;
use tauri_plugin_opener::OpenerExt;

use super::oidc::{self, OidcConfig, OidcError, TokenSet};

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
/// The frontend calls this after `startLogin()` and retries until either a
/// `TokenSet` is available (set by `handle_deep_link`) or the user cancels.
///
/// This command is intentionally thin — the actual state is held server-side
/// until the deep-link fires and the Rust side calls `handle_deep_link`.
/// For now it returns `null` (None) so the frontend can detect "not yet authed".
///
/// A richer implementation (T-630) will check the vault for a cached session.
#[tauri::command]
pub async fn poll_auth_state() -> Option<TokenSet> {
    // Phase 1: no persistent vault yet — the deep-link command drives completion.
    // The TypeScript layer will call handle_deep_link once the URL arrives via the
    // deep-link plugin event, so this stub is sufficient for the OIDC handshake.
    None
}
