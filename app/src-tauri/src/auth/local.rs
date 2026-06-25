//! Solo-mode local authentication.
//!
//! Solo mode runs DepthFusion entirely on the user's own machine using MLX for
//! inference and the user's own Anthropic API key. There is no OIDC / Keycloak
//! server in this mode — instead the Anthropic API key is stored in the OS
//! keychain vault as the session `access_token`, mirroring how OIDC tokens are
//! persisted so the rest of the app can read credentials uniformly.
//!
//! Credential safety: the API key is never logged at any level.

use crate::auth::vault;
use crate::settings;
use tauri::AppHandle;

/// Anthropic API keys always carry this prefix. Validating it client-side gives
/// the user a fast, clear error before we ever attempt to store a malformed key.
const ANTHROPIC_KEY_PREFIX: &str = "sk-ant-";

/// One year, in seconds. Anthropic API keys do not expire on a fixed schedule
/// like OIDC access tokens, so we stamp a long TTL purely to satisfy the vault's
/// `expires_in` field and keep `is_expired` checks from treating the key as
/// stale during normal use.
const SOLO_KEY_TTL_SECS: u64 = 365 * 24 * 3600;

/// Validate an Anthropic API key and build the vault `TokenSet` that represents
/// a solo-mode session.
///
/// Pure (no I/O): the key-format check and field mapping are isolated here so
/// they can be unit-tested without a keychain or an `AppHandle`.
///
/// Returns `Err` when the key does not start with `sk-ant-`.
fn build_solo_token_set(api_key: &str) -> Result<vault::TokenSet, String> {
    if !api_key.starts_with(ANTHROPIC_KEY_PREFIX) {
        return Err(format!(
            "Invalid Anthropic API key: expected a key starting with '{ANTHROPIC_KEY_PREFIX}'"
        ));
    }

    Ok(vault::TokenSet {
        access_token: api_key.to_string(),
        id_token: None,
        refresh_token: None,
        expires_in: Some(SOLO_KEY_TTL_SECS),
        token_type: "ApiKey".to_string(),
        // Left `None` so `vault::store_tokens` stamps the absolute anchor at
        // write time, exactly as the OIDC path does.
        stored_at: None,
    })
}

/// TTL for a static bearer token (10 years). Tokens managed by a VPS admin
/// don't expire on a schedule, so a very long TTL prevents `is_expired` checks
/// from treating the token as stale during normal use.
const CONNECT_TOKEN_TTL_SECS: u64 = 10 * 365 * 24 * 3600;

/// Configure connect mode from a static bearer token.
///
/// Steps (in order):
///   1. Reject empty/whitespace tokens before any write.
///   2. Store the token in the OS keychain vault with `token_type = "Bearer"`.
///   3. Persist `deployment_mode = "connect"`.
///   4. Mark the setup wizard as completed.
///
/// Credential safety: the token is never logged.
#[tauri::command]
pub fn setup_connect_auth(app: AppHandle, bearer_token: String) -> Result<(), String> {
    if bearer_token.trim().is_empty() {
        return Err("Bearer token must not be empty.".to_string());
    }

    let token_set = vault::TokenSet {
        access_token: bearer_token,
        id_token: None,
        refresh_token: None,
        expires_in: Some(CONNECT_TOKEN_TTL_SECS),
        token_type: "Bearer".to_string(),
        stored_at: None,
    };

    vault::store_tokens(&token_set)
        .map_err(|e| format!("Failed to persist bearer token to keychain vault: {e}"))?;

    settings::set_deployment_mode(app.clone(), "connect".to_string())?;
    settings::set_wizard_completed(app, true)?;

    Ok(())
}

/// Configure solo mode from an Anthropic API key.
///
/// Steps (in order):
///   1. Validate the `sk-ant-` prefix (rejects malformed keys before any write).
///   2. Store the key in the OS keychain vault as the session access token,
///      with `token_type = "ApiKey"` and a one-year TTL.
///   3. Persist `deployment_mode = "solo"`.
///   4. Mark the setup wizard as completed.
///
/// Credential safety: the API key is never logged.
#[tauri::command]
pub fn setup_solo_auth(app: AppHandle, api_key: String) -> Result<(), String> {
    let token_set = build_solo_token_set(&api_key)?;

    vault::store_tokens(&token_set)
        .map_err(|e| format!("Failed to persist API key to keychain vault: {e}"))?;

    settings::set_deployment_mode(app.clone(), "solo".to_string())?;
    settings::set_wizard_completed(app, true)?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::vault;

    #[test]
    fn build_solo_token_set_rejects_non_anthropic_prefix() {
        let result = build_solo_token_set("not-a-real-key");
        assert!(result.is_err(), "keys without sk-ant- prefix must be rejected");
        let msg = result.unwrap_err();
        assert!(
            msg.contains("sk-ant-"),
            "error message must mention the expected prefix"
        );
    }

    #[test]
    fn build_solo_token_set_rejects_empty_key() {
        assert!(build_solo_token_set("").is_err());
    }

    #[test]
    fn build_solo_token_set_accepts_valid_key_with_apikey_token_type() {
        let set = build_solo_token_set("sk-ant-api03-abc123").expect("valid key accepted");
        assert_eq!(set.access_token, "sk-ant-api03-abc123");
        assert_eq!(set.token_type, "ApiKey");
        assert_eq!(set.expires_in, Some(365 * 24 * 3600));
        assert!(set.refresh_token.is_none());
        assert!(set.id_token.is_none());
    }

    #[test]
    fn valid_key_round_trips_through_the_vault() {
        // Drive the real store/load path against the in-memory keyring mock so we
        // confirm a TokenSet with token_type "ApiKey" is what lands in the vault.
        // The keyring mock keeps its secret *inside* the credential object, so we
        // must reuse one persistent `Entry` (per the vault test-helper contract);
        // the public `store_tokens`/`load_tokens` fns each build a fresh mock
        // credential and therefore cannot observe a round-trip.
        let entry = vault::mock_entry_for("depthfusion", "session_tokens");

        let set = build_solo_token_set("sk-ant-api03-xyz789").expect("valid key");
        vault::store_tokens_in_entry(&entry, &set).expect("store into mock vault");

        let loaded = vault::load_tokens_from_entry(&entry)
            .expect("vault load ok")
            .expect("a token set is present");
        assert_eq!(loaded.access_token, "sk-ant-api03-xyz789");
        assert_eq!(loaded.token_type, "ApiKey");
        assert_eq!(loaded.expires_in, Some(365 * 24 * 3600));
    }
}
