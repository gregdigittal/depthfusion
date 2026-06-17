//! Tamper detection over the cache schema + lease table (T-651 / S-188 AC-3).
//!
//! On open, the cache computes an HMAC-SHA256 over the schema DDL + the lease
//! table contents and compares it (in constant time) to the digest persisted
//! from the last clean close. A mismatch — or a missing digest — means the
//! on-disk cache cannot be trusted: the caller must **wipe + re-sync**.
//!
//! This mirrors `depthfusion.cache.admission.compute_integrity_hmac` /
//! `verify_on_open` byte-for-byte (same field separators, same row ordering),
//! so a digest produced by either side verifies on the other.

use hmac::{Hmac, Mac};
use sha2::Sha256;

use super::{CACHE_SCHEMA, CACHE_SCHEMA_VERSION};

type HmacSha256 = Hmac<Sha256>;

/// A lease row in canonical form (the digest sorts these by `record_id`).
#[derive(Debug, Clone)]
pub struct LeaseRow {
    pub record_id: String,
    pub issued_at: i64,
    pub expires_at: i64,
    pub classification: String,
}

/// Result of the on-open integrity check.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TamperResult {
    /// Digest matched — cache is intact.
    Ok,
    /// Digest mismatch or missing — untrusted cache; wipe + re-sync.
    WipeAndResync,
}

fn canonical_lease_bytes(leases: &[LeaseRow]) -> Vec<u8> {
    let mut sorted: Vec<&LeaseRow> = leases.iter().collect();
    sorted.sort_by(|a, b| a.record_id.cmp(&b.record_id));
    let parts: Vec<String> = sorted
        .iter()
        .map(|r| {
            format!(
                "{}|{}|{}|{}",
                r.record_id, r.issued_at, r.expires_at, r.classification
            )
        })
        .collect();
    parts.join("\n").into_bytes()
}

/// Compute the integrity HMAC over the schema + lease table, hex-encoded.
///
/// Binds `schema_version`, the schema DDL, and the lease table so that neither
/// a schema change nor a silent lease-expiry extension on disk goes undetected.
pub fn compute_integrity_hmac(
    key: &[u8],
    leases: &[LeaseRow],
    schema: &str,
    schema_version: u32,
) -> String {
    let mut mac = HmacSha256::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(schema_version.to_string().as_bytes());
    mac.update(b"\x00");
    mac.update(schema.as_bytes());
    mac.update(b"\x00");
    mac.update(&canonical_lease_bytes(leases));
    let out = mac.finalize().into_bytes();
    out.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Convenience over [`compute_integrity_hmac`] using the canonical schema +
/// version constants.
pub fn compute_default(key: &[u8], leases: &[LeaseRow]) -> String {
    compute_integrity_hmac(key, leases, CACHE_SCHEMA, CACHE_SCHEMA_VERSION)
}

/// Verify the on-disk cache integrity at open time.
///
/// Returns [`TamperResult::Ok`] only when `stored_digest` is `Some(non-empty)`
/// and matches a freshly-computed HMAC over the current schema + lease table.
/// Any mismatch — or a missing/empty digest (first-open / cleared) — yields
/// [`TamperResult::WipeAndResync`]. Uses a constant-time comparison.
pub fn verify_on_open(
    key: &[u8],
    stored_digest: Option<&str>,
    leases: &[LeaseRow],
    schema: &str,
    schema_version: u32,
) -> TamperResult {
    let stored = match stored_digest {
        Some(s) if !s.is_empty() => s,
        _ => return TamperResult::WipeAndResync,
    };
    let expected = compute_integrity_hmac(key, leases, schema, schema_version);
    // Constant-time compare on the raw hex strings (equal length when both are
    // valid SHA-256 hex; differing length compares unequal without leaking).
    if constant_time_eq(expected.as_bytes(), stored.as_bytes()) {
        TamperResult::Ok
    } else {
        TamperResult::WipeAndResync
    }
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff: u8 = 0;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leases() -> Vec<LeaseRow> {
        vec![
            LeaseRow {
                record_id: "rec-a".into(),
                issued_at: 1000,
                expires_at: 1000 + 7 * 86400,
                classification: "internal".into(),
            },
            LeaseRow {
                record_id: "rec-b".into(),
                issued_at: 2000,
                expires_at: 2000 + 48 * 3600,
                classification: "confidential".into(),
            },
        ]
    }

    #[test]
    fn matching_digest_returns_ok() {
        let key = [0x6bu8; 32];
        let l = leases();
        let digest = compute_default(&key, &l);
        assert_eq!(
            verify_on_open(&key, Some(&digest), &l, CACHE_SCHEMA, CACHE_SCHEMA_VERSION),
            TamperResult::Ok
        );
    }

    #[test]
    fn missing_or_empty_digest_triggers_wipe_resync() {
        let key = [0x6bu8; 32];
        let l = leases();
        assert_eq!(
            verify_on_open(&key, None, &l, CACHE_SCHEMA, CACHE_SCHEMA_VERSION),
            TamperResult::WipeAndResync
        );
        assert_eq!(
            verify_on_open(&key, Some(""), &l, CACHE_SCHEMA, CACHE_SCHEMA_VERSION),
            TamperResult::WipeAndResync
        );
    }

    #[test]
    fn tampered_lease_expiry_triggers_wipe_resync() {
        let key = [0x6bu8; 32];
        let l = leases();
        let digest = compute_default(&key, &l);
        let mut tampered = l.clone();
        tampered[0].expires_at = 1000 + 365 * 86400; // attacker extends the lease
        assert_eq!(
            verify_on_open(
                &key,
                Some(&digest),
                &tampered,
                CACHE_SCHEMA,
                CACHE_SCHEMA_VERSION
            ),
            TamperResult::WipeAndResync
        );
    }

    #[test]
    fn tampered_schema_triggers_wipe_resync() {
        let key = [0x6bu8; 32];
        let l = leases();
        let digest = compute_default(&key, &l);
        let evil = format!("{}\nDROP TABLE cache_lease;", CACHE_SCHEMA);
        assert_eq!(
            verify_on_open(&key, Some(&digest), &l, &evil, CACHE_SCHEMA_VERSION),
            TamperResult::WipeAndResync
        );
    }

    #[test]
    fn wrong_key_triggers_wipe_resync() {
        let l = leases();
        let digest = compute_default(&[0x6bu8; 32], &l);
        assert_eq!(
            verify_on_open(&[0x77u8; 32], Some(&digest), &l, CACHE_SCHEMA, CACHE_SCHEMA_VERSION),
            TamperResult::WipeAndResync
        );
    }

    #[test]
    fn digest_independent_of_lease_row_order() {
        let key = [0x6bu8; 32];
        let l = leases();
        let mut reordered = l.clone();
        reordered.reverse();
        assert_eq!(compute_default(&key, &l), compute_default(&key, &reordered));
    }

    #[test]
    fn schema_version_bump_changes_digest() {
        let key = [0x6bu8; 32];
        let l = leases();
        let d1 = compute_integrity_hmac(&key, &l, CACHE_SCHEMA, CACHE_SCHEMA_VERSION);
        let d2 = compute_integrity_hmac(&key, &l, CACHE_SCHEMA, CACHE_SCHEMA_VERSION + 1);
        assert_ne!(d1, d2);
    }

    #[test]
    fn constant_time_eq_basic() {
        assert!(constant_time_eq(b"abc", b"abc"));
        assert!(!constant_time_eq(b"abc", b"abd"));
        assert!(!constant_time_eq(b"abc", b"ab"));
    }
}
