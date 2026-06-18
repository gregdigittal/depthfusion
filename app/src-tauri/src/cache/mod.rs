//! Encrypted offline cache (E-58 S-188).
//!
//! Three concerns, one per submodule:
//!
//! * [`keywrap`] — per-device cache key: generated once, wrapped (stored) in the
//!   OS keychain (Keychain / DPAPI / Secret Service via the `keyring` crate),
//!   **never written to disk in plaintext** (T-649 / AC-1).
//! * [`tamper`] — integrity HMAC over the schema DDL + lease table, verified on
//!   open; mismatch ⇒ wipe + re-sync (T-651 / AC-3). Mirrors the Python
//!   `depthfusion.cache.admission` implementation byte-for-byte.
//! * [`store`] — the SQLCipher database itself, opened via rusqlite + `PRAGMA
//!   key` using the unwrapped per-device key (AC-1 / AC-2).
//!
//! The security-critical key-wrap and tamper paths are unit-tested without
//! requiring a successful SQLCipher C build, so the AC's "key-wrap path passes"
//! requirement holds even in a constrained CI.
//!
//! Several items here form the stable public surface that the lease / purge /
//! offline-query tasks (T-657 … T-660) will consume; they are not yet called
//! from a Tauri command, so `dead_code` is allowed at the module root.
#![allow(dead_code)]

pub mod keywrap;
pub mod store;
pub mod tamper;

/// Schema version. Bumping it changes the tamper HMAC (a deliberate migration
/// signal) — must stay in lock-step with
/// `depthfusion.cache.admission.CACHE_SCHEMA_VERSION`.
pub const CACHE_SCHEMA_VERSION: u32 = 1;

/// Authoritative cache DDL. MUST stay byte-for-byte identical to
/// `depthfusion.cache.admission.CACHE_SCHEMA` because the tamper HMAC is
/// computed over this exact text on both sides.
pub const CACHE_SCHEMA: &str = concat!(
    "CREATE TABLE IF NOT EXISTS cached_record (",
    "record_id TEXT PRIMARY KEY, ",
    "principal_id TEXT NOT NULL, ",
    "classification TEXT NOT NULL, ",
    "acl_allow TEXT NOT NULL, ",
    "lease_expires_at INTEGER NOT NULL, ",
    "content BLOB",
    ");\n",
    "CREATE TABLE IF NOT EXISTS cached_chunk (",
    "chunk_id TEXT PRIMARY KEY, ",
    "record_id TEXT NOT NULL REFERENCES cached_record(record_id) ON DELETE CASCADE, ",
    "ordinal INTEGER NOT NULL, ",
    "text BLOB",
    ");\n",
    "CREATE TABLE IF NOT EXISTS cached_embedding (",
    "chunk_id TEXT PRIMARY KEY REFERENCES cached_chunk(chunk_id) ON DELETE CASCADE, ",
    "dim INTEGER NOT NULL, ",
    "vector BLOB",
    ");\n",
    "CREATE TABLE IF NOT EXISTS cache_lease (",
    "record_id TEXT PRIMARY KEY REFERENCES cached_record(record_id) ON DELETE CASCADE, ",
    "issued_at INTEGER NOT NULL, ",
    "expires_at INTEGER NOT NULL, ",
    "classification TEXT NOT NULL",
    ");"
);

/// Minimal serialisable error for IPC transport, matching `auth::vault`'s shape.
#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct CacheError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for CacheError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}

impl std::error::Error for CacheError {}
