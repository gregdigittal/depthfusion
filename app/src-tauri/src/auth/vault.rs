//! Token vault backed by the OS keychain.
//!
//! Platform mapping:
//!   macOS   → Security framework keychain (via the `keyring` crate's apple-native feature)
//!   Windows → DPAPI / Windows Credential Manager (windows-native feature)
//!   Linux   → Secret Service (DBus) via the oo7-based sync-secret-service feature
//!
//! The three public functions are intentionally synchronous at the Rust level so
//! they can be wrapped as simple Tauri commands without extra async overhead.
//! The `keyring` crate manages the OS calls internally.

use keyring::Entry;
use serde::{Deserialize, Serialize};

/// Mirror of `oidc::TokenSet`, re-exported here for vault serialisation.
/// We store the full set as a JSON blob under a single keychain entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenSet {
    pub access_token: String,
    pub id_token: Option<String>,
    pub refresh_token: Option<String>,
    /// Relative TTL in seconds, as issued by the IdP (seconds-from-issue).
    pub expires_in: Option<u64>,
    pub token_type: String,
    /// Unix epoch seconds captured at the moment the tokens were written to the
    /// vault. This is the absolute anchor that makes the relative `expires_in`
    /// usable for expiry checks. `serde(default)` keeps deserialisation
    /// backward-compatible with blobs written before this field existed; such
    /// legacy blobs deserialise with `stored_at == None` and are treated as
    /// expired (see `is_expired`).
    #[serde(default)]
    pub stored_at: Option<u64>,
}

/// Wall-clock now in Unix epoch seconds.
///
/// On the impossible pre-epoch clock error, falls back to 0 (which, combined
/// with `is_expired`'s saturating arithmetic, errs on the side of treating
/// tokens as expired rather than handing out a stale one).
fn now_unix_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

impl TokenSet {
    /// Returns `true` when the access token should be considered expired at
    /// `now` (Unix epoch seconds), applying a `skew_secs` safety margin so a
    /// token about to lapse is treated as already gone.
    ///
    /// Policy:
    ///   - `stored_at == None` (legacy/unknown blob) → expired (forces fresh login).
    ///   - `expires_in == None` (IdP omitted a TTL) → never expires.
    ///   - otherwise expired when `now + skew_secs >= stored_at + expires_in`.
    ///
    /// All arithmetic is saturating to avoid overflow panics.
    pub fn is_expired(&self, now: u64, skew_secs: u64) -> bool {
        let stored_at = match self.stored_at {
            Some(s) => s,
            None => return true,
        };
        let expires_in = match self.expires_in {
            Some(e) => e,
            None => return false,
        };
        now.saturating_add(skew_secs) >= stored_at.saturating_add(expires_in)
    }
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
    store_tokens_in(&entry()?, tokens)
}

/// Load tokens from the OS keychain.
///
/// Returns `None` when no entry is found (first run, or after `clear_tokens`).
/// Returns an error only when the keychain itself fails (e.g. locked, permission denied).
pub fn load_tokens() -> Result<Option<TokenSet>, VaultError> {
    load_tokens_from(&entry()?)
}

/// Delete the stored tokens from the OS keychain.
///
/// Succeeds silently when no entry exists (idempotent).
pub fn clear_tokens() -> Result<(), VaultError> {
    clear_tokens_in(&entry()?)
}

// ---------------------------------------------------------------------------
// Entry-scoped helpers.
//
// The public functions build the canonical SERVICE/ACCOUNT entry and delegate
// here. Splitting the I/O from entry construction lets tests drive the full
// store/load/clear logic (including `stored_at` stamping) against a single,
// persistent mock-backed `Entry` — the keyring mock keeps its secret *inside*
// the credential object, so state only survives when the same `Entry` is
// reused, which `Entry::new(SERVICE, ACCOUNT)` cannot guarantee under the mock.
// Production behaviour is unchanged: each public call builds its own entry,
// exactly as before.
// ---------------------------------------------------------------------------

/// Stamp `stored_at`, serialise, and write the blob to `e`.
fn store_tokens_in(e: &Entry, tokens: &TokenSet) -> Result<(), VaultError> {
    // Stamp the storage time without mutating the caller's struct: serialise a
    // stamped clone so the absolute expiry anchor is co-located with the token.
    let stamped = TokenSet {
        stored_at: Some(now_unix_secs()),
        ..tokens.clone()
    };
    let json = serde_json::to_string(&stamped).map_err(|e| VaultError {
        code: "SERIALISE".to_string(),
        message: e.to_string(),
    })?;

    e.set_password(&json).map_err(|e| VaultError {
        code: "VAULT_WRITE".to_string(),
        message: e.to_string(),
    })
}

/// Read and deserialise the blob from `e`. `None` when absent.
fn load_tokens_from(e: &Entry) -> Result<Option<TokenSet>, VaultError> {
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

/// Delete the blob from `e`; idempotent on an absent entry.
fn clear_tokens_in(e: &Entry) -> Result<(), VaultError> {
    match e.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(err) => Err(VaultError {
            code: "VAULT_DELETE".to_string(),
            message: err.to_string(),
        }),
    }
}

/// Test-only: install the keyring in-memory mock as the process-wide default
/// credential builder, so vault calls never touch the live OS Secret Service
/// (which is unavailable in headless CI — no `$DISPLAY`/DBus). Idempotent and
/// safe to call from every keychain-touching test in the crate.
#[cfg(test)]
pub(crate) fn install_mock_keystore() {
    use std::sync::Once;
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        keyring::set_default_credential_builder(keyring::mock::default_credential_builder());
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(expires_in: Option<u64>, stored_at: Option<u64>) -> TokenSet {
        TokenSet {
            access_token: "access-abc".to_string(),
            id_token: Some("id-xyz".to_string()),
            refresh_token: Some("refresh-123".to_string()),
            expires_in,
            token_type: "Bearer".to_string(),
            stored_at,
        }
    }

    /// Build a single mock-backed `Entry` whose in-memory credential persists
    /// across helper calls. The keyring mock stores its secret *inside* the
    /// `MockCredential`, so reusing one `Entry` is the only way to observe a
    /// store→load round-trip — `Entry::new(SERVICE, ACCOUNT)` would build a
    /// fresh empty credential on every call under the mock and never persist.
    #[cfg(test)]
    fn mock_entry() -> Entry {
        let credential = keyring::mock::default_credential_builder()
            .build(None, "depthfusion-test", "session_tokens_roundtrip")
            .expect("build mock credential");
        Entry::new_with_credential(credential)
    }

    // -----------------------------------------------------------------------
    // Pure expiry logic (no system dependency — always runs)
    // -----------------------------------------------------------------------

    #[test]
    fn is_expired_false_when_well_within_ttl() {
        // stored at t=1000, ttl=3600 → expiry at 4600. now=2000, skew=30.
        let t = sample(Some(3600), Some(1000));
        assert!(!t.is_expired(2000, 30));
    }

    #[test]
    fn is_expired_true_when_past_expiry() {
        // expiry at 4600; now=5000 is well past.
        let t = sample(Some(3600), Some(1000));
        assert!(t.is_expired(5000, 30));
    }

    #[test]
    fn is_expired_true_exactly_at_boundary() {
        // expiry at stored_at + expires_in = 4600; now + skew == 4600 → expired.
        let t = sample(Some(3600), Some(1000));
        assert!(t.is_expired(4600, 0));
    }

    #[test]
    fn is_expired_true_within_skew_margin() {
        // expiry at 4600; now=4580, skew=30 → 4610 >= 4600 → expired (skew pulls
        // the effective deadline earlier so a token about to lapse is rejected).
        let t = sample(Some(3600), Some(1000));
        assert!(t.is_expired(4580, 30));
        // Without skew the same token would still be live.
        assert!(!t.is_expired(4580, 0));
    }

    #[test]
    fn is_expired_true_when_stored_at_missing_legacy_blob() {
        // Legacy blob with no anchor → treated as expired regardless of ttl.
        let t = sample(Some(3600), None);
        assert!(t.is_expired(0, 30));
        assert!(t.is_expired(u64::MAX, 30));
    }

    #[test]
    fn is_expired_false_when_expires_in_missing_never_expires() {
        // IdP omitted a TTL → never expires (per chosen policy).
        let t = sample(None, Some(1000));
        assert!(!t.is_expired(u64::MAX, 30));
    }

    #[test]
    fn is_expired_saturating_does_not_panic_on_overflow() {
        // now + skew saturates at u64::MAX; stored_at + expires_in saturates too.
        let t = sample(Some(u64::MAX), Some(u64::MAX));
        // u64::MAX + skew (sat) == u64::MAX >= u64::MAX (sat) → expired, no panic.
        assert!(t.is_expired(u64::MAX, 30));
    }

    // -----------------------------------------------------------------------
    // Serde round-trip (verifies stored_at survives + backward compat)
    // -----------------------------------------------------------------------

    #[test]
    fn serde_round_trip_preserves_all_fields_including_stored_at() {
        let original = sample(Some(3600), Some(1718000000));
        let json = serde_json::to_string(&original).expect("serialise");
        let back: TokenSet = serde_json::from_str(&json).expect("deserialise");

        assert_eq!(back.access_token, original.access_token);
        assert_eq!(back.id_token, original.id_token);
        assert_eq!(back.refresh_token, original.refresh_token);
        assert_eq!(back.expires_in, original.expires_in);
        assert_eq!(back.token_type, original.token_type);
        assert_eq!(back.stored_at, original.stored_at);
    }

    #[test]
    fn deserialise_legacy_blob_without_stored_at_defaults_to_none() {
        // A blob written before `stored_at` existed must still deserialise, with
        // stored_at == None → treated as expired by is_expired (forces re-login).
        let legacy = r#"{
            "access_token": "a",
            "id_token": null,
            "refresh_token": null,
            "expires_in": 3600,
            "token_type": "Bearer"
        }"#;
        let t: TokenSet = serde_json::from_str(legacy).expect("legacy deserialise");
        assert_eq!(t.stored_at, None);
        assert!(t.is_expired(0, 30), "legacy blob must be treated as expired");
    }

    #[test]
    fn corrupt_blob_yields_deserialise_error_not_panic() {
        // Mirrors load_tokens' error mapping: garbage JSON → DESERIALISE error.
        let garbage = "{ not valid json";
        let result: Result<TokenSet, VaultError> =
            serde_json::from_str::<TokenSet>(garbage).map_err(|e| VaultError {
                code: "DESERIALISE".to_string(),
                message: e.to_string(),
            });
        let err = result.expect_err("garbage must not deserialise");
        assert_eq!(err.code, "DESERIALISE");
    }

    // -----------------------------------------------------------------------
    // Keychain round-trip via the keyring in-memory mock store.
    //
    // The mock keystore is `EntryOnly` persistence: each `Entry::new` builds a
    // fresh empty credential, so state does NOT survive across separate
    // `Entry::new` calls. We therefore exercise set/get/delete on a single
    // `Entry` to verify the keychain primitives behave (and that a stored JSON
    // blob deserialises back to an identical `TokenSet`). The mock removes the
    // live-OS dependency so this runs in headless CI.
    // -----------------------------------------------------------------------

    #[test]
    fn keychain_mock_set_get_delete_round_trip() {
        install_mock_keystore();

        let e = keyring::Entry::new("depthfusion-test", "session_tokens_mock")
            .expect("mock entry");

        // Empty store → NoEntry.
        assert!(matches!(e.get_password(), Err(keyring::Error::NoEntry)));

        // Store a stamped blob and read it back.
        let tokens = sample(Some(3600), Some(1718000000));
        let json = serde_json::to_string(&tokens).expect("serialise");
        e.set_password(&json).expect("set_password");

        let loaded_json = e.get_password().expect("get_password after set");
        let loaded: TokenSet = serde_json::from_str(&loaded_json).expect("deserialise");
        assert_eq!(loaded.access_token, tokens.access_token);
        assert_eq!(loaded.stored_at, tokens.stored_at);

        // Overwrite replaces the prior value.
        let tokens2 = sample(Some(7200), Some(1718009999));
        e.set_password(&serde_json::to_string(&tokens2).expect("serialise2"))
            .expect("overwrite");
        let loaded2: TokenSet =
            serde_json::from_str(&e.get_password().expect("get2")).expect("deser2");
        assert_eq!(loaded2.expires_in, Some(7200));
        assert_eq!(loaded2.stored_at, Some(1718009999));

        // Delete then read → NoEntry again (idempotency of the absent case).
        e.delete_credential().expect("delete");
        assert!(matches!(e.get_password(), Err(keyring::Error::NoEntry)));
    }

    // -----------------------------------------------------------------------
    // Full vault store→load→clear round-trip against a single persistent
    // mock-backed `Entry`. This exercises the real public-API logic path
    // (store_tokens_in stamps `stored_at`; load_tokens_from deserialises;
    // clear_tokens_in deletes) without the live OS Secret Service, so it runs
    // in headless CI. The same `Entry` is reused so the mock's in-credential
    // secret survives between calls.
    // -----------------------------------------------------------------------

    #[test]
    fn vault_store_then_load_round_trip_populates_stored_at() {
        let e = mock_entry();

        // Empty store → Ok(None), never an error.
        assert!(matches!(load_tokens_from(&e), Ok(None)));

        // Store a set WITHOUT a stored_at; the vault must stamp one.
        let tokens = sample(Some(3600), None);
        store_tokens_in(&e, &tokens).expect("store");

        // Load it back: identical token fields, and stored_at now populated.
        let loaded = load_tokens_from(&e)
            .expect("load ok")
            .expect("load returns Some after store");

        assert_eq!(loaded.access_token, tokens.access_token);
        assert_eq!(loaded.id_token, tokens.id_token);
        assert_eq!(loaded.refresh_token, tokens.refresh_token);
        assert_eq!(loaded.expires_in, tokens.expires_in);
        assert_eq!(loaded.token_type, tokens.token_type);
        assert!(
            loaded.stored_at.is_some(),
            "store_tokens must stamp stored_at on write"
        );
    }

    #[test]
    fn vault_store_overwrites_prior_entry() {
        let e = mock_entry();

        store_tokens_in(&e, &sample(Some(3600), None)).expect("first store");
        store_tokens_in(&e, &sample(Some(7200), None)).expect("overwrite store");

        let loaded = load_tokens_from(&e).expect("load ok").expect("some");
        assert_eq!(loaded.expires_in, Some(7200), "overwrite must replace blob");
        assert!(loaded.stored_at.is_some());
    }

    #[test]
    fn vault_load_empty_is_none_and_clear_is_idempotent() {
        let e = mock_entry();

        // Empty store → Ok(None).
        assert!(matches!(load_tokens_from(&e), Ok(None)));

        // Clear on an absent entry is Ok (idempotent), repeatedly.
        assert!(clear_tokens_in(&e).is_ok());
        assert!(clear_tokens_in(&e).is_ok());

        // After a store, clear removes it; load is None again; clear stays Ok.
        store_tokens_in(&e, &sample(Some(3600), None)).expect("store");
        assert!(matches!(load_tokens_from(&e), Ok(Some(_))));
        assert!(clear_tokens_in(&e).is_ok());
        assert!(matches!(load_tokens_from(&e), Ok(None)));
        assert!(clear_tokens_in(&e).is_ok());
    }

    #[test]
    fn vault_load_corrupt_blob_yields_deserialise_error() {
        let e = mock_entry();
        // Write garbage directly to the credential, bypassing the serialiser.
        e.set_password("{ not valid json").expect("set garbage");

        let err = load_tokens_from(&e).expect_err("garbage must not load");
        assert_eq!(err.code, "DESERIALISE");
    }

    // Smoke-check the public entry-building wrappers resolve the mock store and
    // never touch the live Secret Service. (Round-trip cannot be asserted
    // through the public fns: each builds a fresh mock credential.)
    #[test]
    fn public_api_wrappers_resolve_mock_store() {
        install_mock_keystore();
        assert!(matches!(load_tokens(), Ok(None)));
        assert!(store_tokens(&sample(Some(3600), None)).is_ok());
        assert!(clear_tokens().is_ok());
        assert!(clear_tokens().is_ok());
    }

    #[test]
    fn store_tokens_does_not_mutate_caller_struct() {
        install_mock_keystore();
        // Caller passes a set with no stored_at; store stamps a clone, leaving
        // the caller's value untouched (immutability invariant).
        let tokens = sample(Some(3600), None);
        let _ = store_tokens(&tokens);
        assert_eq!(tokens.stored_at, None);
    }
}
