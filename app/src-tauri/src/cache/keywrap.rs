//! Per-device cache key management (T-649 / S-188 AC-1).
//!
//! The encrypted offline cache is keyed by a 256-bit random key generated once
//! per device. That key is **wrapped** — i.e. stored — in the OS keychain
//! (macOS Keychain, Windows DPAPI/Credential Manager, Linux Secret Service) via
//! the `keyring` crate, exactly as `auth::vault` does for session tokens.
//!
//! Invariant: the plaintext key is held only in memory ([`CacheKey`]) and in the
//! OS-protected keychain. It is **never** written to disk in plaintext — the
//! SQLCipher database file on disk is encrypted *with* this key, and the key
//! itself lives behind the OS credential store.
//!
//! `CacheKey` zeroizes its bytes on drop so the plaintext does not linger in
//! freed memory.

use base64::Engine;
use keyring::Entry;
use rand::RngCore;
use zeroize::Zeroize;

use super::CacheError;

/// Keychain service label (shared product namespace with `auth::vault`).
const SERVICE: &str = "depthfusion";
/// Keychain account under which the wrapped (base64) cache key is stored.
const ACCOUNT: &str = "offline_cache_key";

/// Length of the per-device cache key in bytes (256-bit).
pub const KEY_LEN: usize = 32;

/// An in-memory cache key. Zeroized on drop.
pub struct CacheKey(pub [u8; KEY_LEN]);

impl CacheKey {
    /// Generate a fresh cryptographically-random per-device key.
    pub fn generate() -> Self {
        let mut bytes = [0u8; KEY_LEN];
        rand::thread_rng().fill_bytes(&mut bytes);
        CacheKey(bytes)
    }

    /// Borrow the raw key bytes (e.g. to feed SQLCipher `PRAGMA key`).
    pub fn as_bytes(&self) -> &[u8; KEY_LEN] {
        &self.0
    }

    /// Hex-encode the key for SQLCipher's `PRAGMA key = "x'<hex>'"` raw-key form,
    /// which bypasses the PBKDF2 passphrase derivation and uses the bytes
    /// directly as the AES key.
    pub fn to_sqlcipher_hex(&self) -> String {
        self.0.iter().map(|b| format!("{:02x}", b)).collect()
    }

    fn to_b64(&self) -> String {
        base64::engine::general_purpose::STANDARD.encode(self.0)
    }

    fn from_b64(s: &str) -> Result<Self, CacheError> {
        let raw = base64::engine::general_purpose::STANDARD
            .decode(s.as_bytes())
            .map_err(|e| CacheError {
                code: "KEY_DECODE".to_string(),
                message: e.to_string(),
            })?;
        if raw.len() != KEY_LEN {
            return Err(CacheError {
                code: "KEY_LEN".to_string(),
                message: format!("expected {} bytes, got {}", KEY_LEN, raw.len()),
            });
        }
        let mut bytes = [0u8; KEY_LEN];
        bytes.copy_from_slice(&raw);
        Ok(CacheKey(bytes))
    }
}

impl Drop for CacheKey {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

/// Redacting `Debug` — never prints key bytes (so a stray `{:?}` or
/// `expect`/`expect_err` message cannot leak the plaintext key into logs).
impl std::fmt::Debug for CacheKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "CacheKey([REDACTED; {} bytes])", KEY_LEN)
    }
}

fn entry() -> Result<Entry, CacheError> {
    Entry::new(SERVICE, ACCOUNT).map_err(|e| CacheError {
        code: "KEY_VAULT_INIT".to_string(),
        message: e.to_string(),
    })
}

/// Load the existing per-device key from the keychain, or generate-and-store a
/// new one on first run. The returned key is the same on every subsequent call
/// for this device (so the SQLCipher DB remains decryptable across launches).
pub fn get_or_create_device_key() -> Result<CacheKey, CacheError> {
    get_or_create_in(&entry()?)
}

/// Delete the wrapped key from the keychain (e.g. on logout / device-revoke).
/// Idempotent — succeeds when no entry exists.
pub fn clear_device_key() -> Result<(), CacheError> {
    clear_in(&entry()?)
}

// ---------------------------------------------------------------------------
// Entry-scoped helpers (mirrors auth::vault's split so tests can drive a single
// persistent mock-backed Entry — the keyring mock keeps its secret inside the
// credential object, so the same Entry must be reused to observe persistence).
// ---------------------------------------------------------------------------

fn get_or_create_in(e: &Entry) -> Result<CacheKey, CacheError> {
    match e.get_password() {
        Ok(b64) => CacheKey::from_b64(&b64),
        Err(keyring::Error::NoEntry) => {
            let key = CacheKey::generate();
            e.set_password(&key.to_b64()).map_err(|err| CacheError {
                code: "KEY_VAULT_WRITE".to_string(),
                message: err.to_string(),
            })?;
            Ok(key)
        }
        Err(err) => Err(CacheError {
            code: "KEY_VAULT_READ".to_string(),
            message: err.to_string(),
        }),
    }
}

fn clear_in(e: &Entry) -> Result<(), CacheError> {
    match e.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(err) => Err(CacheError {
            code: "KEY_VAULT_DELETE".to_string(),
            message: err.to_string(),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Once;

    /// Install the keyring in-memory mock process-wide (headless-CI safe).
    fn install_mock() {
        static INIT: Once = Once::new();
        INIT.call_once(|| {
            keyring::set_default_credential_builder(
                keyring::mock::default_credential_builder(),
            );
        });
    }

    /// A single persistent mock-backed Entry (the mock stores its secret inside
    /// the credential, so the same Entry must be reused across calls).
    fn mock_entry(account: &str) -> Entry {
        install_mock();
        let credential = keyring::mock::default_credential_builder()
            .build(None, "depthfusion-cache-test", account)
            .expect("build mock credential");
        Entry::new_with_credential(credential)
    }

    #[test]
    fn generate_produces_full_length_nonzero_key() {
        let k = CacheKey::generate();
        assert_eq!(k.as_bytes().len(), KEY_LEN);
        // Astronomically unlikely to be all-zero from a CSPRNG.
        assert!(k.as_bytes().iter().any(|&b| b != 0));
    }

    #[test]
    fn two_generated_keys_differ() {
        let a = CacheKey::generate();
        let b = CacheKey::generate();
        assert_ne!(a.as_bytes(), b.as_bytes());
    }

    #[test]
    fn sqlcipher_hex_is_64_lowercase_hex_chars() {
        let k = CacheKey::generate();
        let hex = k.to_sqlcipher_hex();
        assert_eq!(hex.len(), KEY_LEN * 2);
        assert!(hex.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }

    #[test]
    fn b64_round_trip_preserves_key() {
        let k = CacheKey::generate();
        let original = *k.as_bytes();
        let b64 = k.to_b64();
        let back = CacheKey::from_b64(&b64).expect("decode");
        assert_eq!(back.as_bytes(), &original);
    }

    #[test]
    fn from_b64_rejects_wrong_length() {
        // 16 bytes base64 → KEY_LEN mismatch.
        let short = base64::engine::general_purpose::STANDARD.encode([7u8; 16]);
        let err = CacheKey::from_b64(&short).expect_err("must reject short key");
        assert_eq!(err.code, "KEY_LEN");
    }

    #[test]
    fn from_b64_rejects_garbage() {
        let err = CacheKey::from_b64("!!! not base64 !!!").expect_err("reject garbage");
        assert_eq!(err.code, "KEY_DECODE");
    }

    // -----------------------------------------------------------------------
    // The key-wrap path (AC-1): generate → wrap in keychain → unwrap; the key
    // is never on disk in plaintext (it lives base64 inside the OS keychain).
    // -----------------------------------------------------------------------

    #[test]
    fn first_open_generates_and_wraps_key_in_keychain() {
        let e = mock_entry("key_first_open");
        // Empty keychain → NoEntry on read.
        assert!(matches!(e.get_password(), Err(keyring::Error::NoEntry)));

        let key = get_or_create_in(&e).expect("first open generates key");
        assert_eq!(key.as_bytes().len(), KEY_LEN);

        // The wrapped form is now present in the keychain — and it is the
        // base64 of the key, NOT plaintext bytes on disk.
        let wrapped = e.get_password().expect("wrapped key present after first open");
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(wrapped.as_bytes())
            .expect("wrapped value is valid base64");
        assert_eq!(decoded.as_slice(), key.as_bytes());
    }

    #[test]
    fn second_open_returns_same_key() {
        let e = mock_entry("key_stable");
        let k1 = get_or_create_in(&e).expect("first");
        let bytes1 = *k1.as_bytes();
        drop(k1);

        let k2 = get_or_create_in(&e).expect("second");
        assert_eq!(
            k2.as_bytes(),
            &bytes1,
            "device key must be stable across opens so the DB stays decryptable"
        );
    }

    #[test]
    fn clear_then_open_rotates_to_a_new_key() {
        let e = mock_entry("key_rotate");
        let k1 = *get_or_create_in(&e).expect("first").as_bytes();

        clear_in(&e).expect("clear");
        // After a clear, the next open generates a fresh key.
        let k2 = *get_or_create_in(&e).expect("regen").as_bytes();
        assert_ne!(k1, k2, "post-clear open must mint a new key");
    }

    #[test]
    fn clear_is_idempotent_on_absent_entry() {
        let e = mock_entry("key_clear_idem");
        assert!(clear_in(&e).is_ok());
        assert!(clear_in(&e).is_ok());
    }

    #[test]
    fn corrupt_wrapped_value_yields_decode_error_not_panic() {
        let e = mock_entry("key_corrupt");
        e.set_password("not-valid-base64-$$$").expect("set garbage");
        let err = get_or_create_in(&e).expect_err("garbage must not unwrap");
        assert!(err.code == "KEY_DECODE" || err.code == "KEY_LEN");
    }
}
