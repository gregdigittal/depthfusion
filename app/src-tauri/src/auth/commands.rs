//! Tauri IPC commands for the OIDC auth flow, token vault, and sign-out.

use std::collections::HashMap;
use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

use super::logout;
use super::oidc::{self, OidcConfig, OidcError, TokenSet};
use super::vault;

/// Skew margin (seconds) so a token about to lapse is treated as already
/// expired rather than handed out and rejected one network hop later.
const SKEW: u64 = 30;

fn default_config() -> OidcConfig {
    OidcConfig {
        issuer: std::env::var("OIDC_ISSUER")
            .unwrap_or_else(|_| "https://login.tonracein.com/realms/depthfusion".to_string()),
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

    let oidc_tokens = oidc::handle_callback(&config, params)
        .await
        .map_err(|e: OidcError| e.to_string())?;

    // S-181 AC-2: the token must land in the OS keychain vault, not just JS
    // memory. `poll_auth_state` reads the vault as the canonical source of
    // truth, so without this write a "successful" login leaves no durable
    // session. Persist before returning; a vault write failure is fatal.
    persist_token_set(oidc_tokens).await
}

/// Convert an `oidc::TokenSet` to a `vault::TokenSet` and persist it in the OS
/// keychain, returning the original oidc shape for the frontend.
///
/// The oidc shape has no `stored_at`; we leave it `None` so `store_tokens_in`
/// stamps the absolute expiry anchor at write time. A vault write failure is
/// fatal (returned as `Err`) — AC-2 is not met if the token is not durably
/// stored, so we must not silently drop it.
///
/// Extracted from the `#[tauri::command]` wrapper (which needs an `AppHandle`)
/// so the persistence path is unit-testable in isolation.
///
/// Credential safety: token contents are never logged at any level.
async fn persist_token_set(oidc_tokens: TokenSet) -> Result<TokenSet, String> {
    let vault_set = oidc_to_vault(&oidc_tokens);

    vault::store_tokens(&vault_set)
        .map_err(|e| format!("Failed to persist tokens to keychain vault: {e}"))?;

    // Return the oidc shape (no internal `stored_at`) for backward
    // compatibility with the existing deep-link listener on the frontend.
    Ok(oidc_tokens)
}

/// Map an `oidc::TokenSet` onto a `vault::TokenSet`, mapping all five shared
/// fields and leaving `stored_at: None` so `vault::store_tokens_in` stamps the
/// absolute expiry anchor at write time. Pure (no I/O), so unit tests can
/// assert the field mapping without a keychain.
fn oidc_to_vault(oidc_tokens: &TokenSet) -> vault::TokenSet {
    vault::TokenSet {
        access_token: oidc_tokens.access_token.clone(),
        id_token: oidc_tokens.id_token.clone(),
        refresh_token: oidc_tokens.refresh_token.clone(),
        expires_in: oidc_tokens.expires_in,
        token_type: oidc_tokens.token_type.clone(),
        stored_at: None,
    }
}

/// Poll the current auth state.
///
/// Returns the cached `TokenSet` from the OS keychain if one exists, otherwise
/// `None` (frontend should then trigger `startLogin()`).
#[tauri::command]
pub async fn poll_auth_state() -> Option<TokenSet> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    // T-630: check the vault for a cached session.
    poll_auth_state_from(vault::load_tokens(), now)
}

/// Map a vault load result onto the frontend OIDC token shape when the cached
/// session is usable. Legacy/unanchored blobs, expired tokens, absent tokens,
/// and vault read failures all resolve to `None` so startup recovery can fall
/// back to login without crashing.
///
/// Credential safety: token contents are never logged at any level.
fn poll_auth_state_from(
    loaded: Result<Option<vault::TokenSet>, vault::VaultError>,
    now: u64,
) -> Option<oidc::TokenSet> {
    let vt = loaded.ok().flatten()?;

    // Reject expired (or legacy/unanchored) sessions so the frontend
    // re-triggers `start_login` instead of using a stale access token.
    if vt.is_expired(now, SKEW) {
        return None;
    }

    // Re-export vault::TokenSet as the canonical oidc::TokenSet shape.
    // `stored_at` is internal vault bookkeeping and is deliberately dropped at
    // the IPC boundary (oidc::TokenSet has no such field).
    Some(oidc::TokenSet {
        access_token: vt.access_token,
        id_token: vt.id_token,
        refresh_token: vt.refresh_token,
        expires_in: vt.expires_in,
        token_type: vt.token_type,
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_oidc() -> TokenSet {
        TokenSet {
            access_token: "deep-link-access-token".to_string(),
            id_token: Some("deep-link-id-token".to_string()),
            refresh_token: Some("deep-link-refresh-token".to_string()),
            expires_in: Some(3600),
            token_type: "Bearer".to_string(),
        }
    }

    fn sample_vault(expires_in: Option<u64>, stored_at: Option<u64>) -> vault::TokenSet {
        vault::TokenSet {
            access_token: "vault-access-token".to_string(),
            id_token: Some("vault-id-token".to_string()),
            refresh_token: Some("vault-refresh-token".to_string()),
            expires_in,
            token_type: "Bearer".to_string(),
            stored_at,
        }
    }

    #[test]
    fn poll_auth_state_valid_token() {
        let loaded = Ok(Some(sample_vault(Some(3600), Some(1000))));

        let token = poll_auth_state_from(loaded, 1200).expect("token should be valid");

        assert_eq!(token.access_token, "vault-access-token");
        assert_eq!(token.id_token, Some("vault-id-token".to_string()));
        assert_eq!(token.refresh_token, Some("vault-refresh-token".to_string()));
        assert_eq!(token.expires_in, Some(3600));
        assert_eq!(token.token_type, "Bearer");

        let serialized = serde_json::to_value(&token).expect("serialize oidc token");
        assert!(
            serialized.get("stored_at").is_none(),
            "oidc token shape must not expose vault stored_at"
        );
    }

    #[test]
    fn poll_auth_state_expired_token() {
        let loaded = Ok(Some(sample_vault(Some(3600), Some(1000))));

        assert!(poll_auth_state_from(loaded, 4570).is_none());
    }

    #[test]
    fn poll_auth_state_legacy_no_stored_at() {
        let loaded = Ok(Some(sample_vault(Some(3600), None)));

        assert!(poll_auth_state_from(loaded, 1200).is_none());
    }

    #[test]
    fn poll_auth_state_ok_none() {
        assert!(poll_auth_state_from(Ok(None), 1200).is_none());
    }

    #[test]
    fn poll_auth_state_vault_error() {
        let err = vault::VaultError {
            code: "VAULT_READ".to_string(),
            message: "read failed".to_string(),
        };

        assert!(poll_auth_state_from(Err(err), 1200).is_none());
    }

    /// The oidc→vault converter maps all five shared fields and leaves
    /// `stored_at` unset so the vault stamps it on write.
    #[test]
    fn oidc_to_vault_maps_all_fields_and_leaves_stored_at_none() {
        let oidc = sample_oidc();
        let v = oidc_to_vault(&oidc);

        assert_eq!(v.access_token, oidc.access_token);
        assert_eq!(v.id_token, oidc.id_token);
        assert_eq!(v.refresh_token, oidc.refresh_token);
        assert_eq!(v.expires_in, oidc.expires_in);
        assert_eq!(v.token_type, oidc.token_type);
        assert_eq!(v.stored_at, None, "converter must leave stored_at for the vault to stamp");
    }

    /// AC-2 proof: running the deep-link persistence path writes the token to
    /// the keychain such that a subsequent load returns it with a populated
    /// `stored_at`.
    ///
    /// Under the keyring in-memory mock, each `Entry::new(SERVICE, ACCOUNT)`
    /// builds a *fresh* empty credential, so a round-trip is only observable on
    /// a single reused `Entry`. We therefore drive the same conversion +
    /// entry-scoped store/load the production path uses, against one persistent
    /// mock-backed entry — proving the conversion + write reach the keychain and
    /// the absolute expiry anchor is stamped.
    #[test]
    fn persist_path_writes_token_to_keychain_with_stored_at() {
        let oidc = sample_oidc();

        // Mirror persist_token_set's conversion, then exercise the vault's
        // entry-scoped store/load against one persistent mock entry.
        let vault_set = oidc_to_vault(&oidc);
        let e = vault::mock_entry_for("depthfusion-test", "deep_link_persist");

        // Nothing stored yet.
        assert!(matches!(vault::load_tokens_from_entry(&e), Ok(None)));

        vault::store_tokens_in_entry(&e, &vault_set).expect("store must succeed");

        let loaded = vault::load_tokens_from_entry(&e)
            .expect("load ok")
            .expect("deep-link path must leave a token in the keychain");

        assert_eq!(
            loaded.access_token, oidc.access_token,
            "stored access_token must match the exchanged token"
        );
        assert_eq!(loaded.id_token, oidc.id_token);
        assert_eq!(loaded.refresh_token, oidc.refresh_token);
        assert_eq!(loaded.expires_in, oidc.expires_in);
        assert_eq!(loaded.token_type, oidc.token_type);
        assert!(
            loaded.stored_at.is_some(),
            "store must stamp stored_at so poll_auth_state can evaluate expiry"
        );
    }

    /// The public persist path (the production seam used by `handle_deep_link`)
    /// calls `vault::store_tokens` without error and returns the oidc shape
    /// unchanged.
    ///
    /// `persist_token_set` is `async` but never crosses a real `.await`
    /// suspension point, so we drive it to completion with a no-op waker rather
    /// than pulling in a runtime — keeping this task independent of the
    /// tokio test-feature wiring added in a later task.
    #[test]
    fn persist_token_set_succeeds_and_returns_oidc_shape() {
        vault::install_mock_keystore();
        let oidc = sample_oidc();

        let returned =
            block_on(persist_token_set(oidc.clone())).expect("persist must succeed under mock");

        // Backward compatibility: the frontend still receives the oidc shape.
        assert_eq!(returned.access_token, oidc.access_token);
        assert_eq!(returned.token_type, oidc.token_type);
    }

    /// Minimal executor for futures that complete without yielding. Avoids a
    /// runtime dependency for the synchronous-in-practice persist path.
    fn block_on<F: std::future::Future>(mut fut: F) -> F::Output {
        use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

        fn noop(_: *const ()) {}
        fn clone(_: *const ()) -> RawWaker {
            RawWaker::new(std::ptr::null(), &VTABLE)
        }
        static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, noop, noop, noop);

        let raw = RawWaker::new(std::ptr::null(), &VTABLE);
        let waker = unsafe { Waker::from_raw(raw) };
        let mut cx = Context::from_waker(&waker);

        // SAFETY: `fut` is owned, stack-pinned here, and never moved again.
        let mut fut = unsafe { std::pin::Pin::new_unchecked(&mut fut) };
        loop {
            match fut.as_mut().poll(&mut cx) {
                Poll::Ready(v) => return v,
                Poll::Pending => std::hint::spin_loop(),
            }
        }
    }
}
