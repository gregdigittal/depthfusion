/// Token vault backed by the OS keychain.
///
/// Platform mapping:
///   macOS   → Security framework keychain (via the `keyring` crate's apple-native feature)
///   Windows → DPAPI / Windows Credential Manager (windows-native feature)
///   Linux   → Secret Service (DBus) via the oo7-based sync-secret-service feature
///
/// The three public functions are intentionally synchronous at the Rust level so
/// they can be wrapped as simple Tauri commands without extra async overhead.
/// The `keyring` crate manages the OS calls internally.

use keyring::Entry;
use serde::{Deserialize, Serialize};

/// Mirror of `oidc::TokenSet`, re-exported here for vault serialisation.
/// We store the full set as a JSON blob under a single keychain entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenSet {
    pub access_token: String,
    pub id_token: Option<String>,
    pub refresh_token: Option<String>,
    pub expires_in: Option<u64>,
    pub token_type: String,
}

/// Human-readable service label shown in Keychain Access / Credential Manager.
const SERVICE: &str = "depthfusion";
/// Account key under which the JSON blob is stored.
const ACCOUNT: &str = "session_tokens";

fn entry() -> Result<Entry, VaultError> {
    Entry::new(SERVICE, ACCOUNT).map_err(|e| VaultError {
        code: "VAULT_INIT".to_string(),
        message: e.to_string(),
    })
}

/// Minimal serialisable error for IPC transport.
#[derive(Debug, Serialize, Deserialize)]
pub struct VaultError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for VaultError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}

/// Persist `tokens` in the OS keychain as a JSON blob.
///
/// Overwrites any existing entry for this service/account pair.
pub fn store_tokens(tokens: &TokenSet) -> Result<(), VaultError> {
    let json = serde_json::to_string(tokens).map_err(|e| VaultError {
        code: "SERIALISE".to_string(),
        message: e.to_string(),
    })?;

    let e = entry()?;
    e.set_password(&json).map_err(|e| VaultError {
        code: "VAULT_WRITE".to_string(),
        message: e.to_string(),
    })
}

/// Load tokens from the OS keychain.
///
/// Returns `None` when no entry is found (first run, or after `clear_tokens`).
/// Returns an error only when the keychain itself fails (e.g. locked, permission denied).
pub fn load_tokens() -> Result<Option<TokenSet>, VaultError> {
    let e = entry()?;

    match e.get_password() {
        Ok(json) => {
            let tokens: TokenSet = serde_json::from_str(&json).map_err(|e| VaultError {
                code: "DESERIALISE".to_string(),
                message: e.to_string(),
            })?;
            Ok(Some(tokens))
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(err) => Err(VaultError {
            code: "VAULT_READ".to_string(),
            message: err.to_string(),
        }),
    }
}

/// Delete the stored tokens from the OS keychain.
///
/// Succeeds silently when no entry exists (idempotent).
pub fn clear_tokens() -> Result<(), VaultError> {
    let e = entry()?;

    match e.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(err) => Err(VaultError {
            code: "VAULT_DELETE".to_string(),
            message: err.to_string(),
        }),
    }
}
