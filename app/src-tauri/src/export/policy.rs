//! Export policy: the role × classification → action matrix plus the verified
//! signed-snapshot offline gate (E-59 S-192 / S-191 AC-3).
//!
//! Two layers, evaluated in order:
//!
//! 1. **Signed snapshot gate** (when an offline snapshot is supplied). Before
//!    *any* policy is trusted, the snapshot's HMAC-SHA256 signature and expiry
//!    are verified. The canonical body is byte-for-byte identical to
//!    `depthfusion.authz.policy_snapshot._canonical_bytes`, so a snapshot signed
//!    by the Python server verifies here and vice-versa. An unsigned, tampered,
//!    or expired snapshot is **refused → deny** — never a fallback to a
//!    forgeable on-disk copy.
//!
//! 2. **Action matrix**. Maps a [`Role`] + [`Classification`] to the set of
//!    permitted [`ExportAction`]s. The defaults mirror S-191 AC-2:
//!    viewer = view only; analyst = +copy/export ≤ internal; contributor =
//!    +download ≤ confidential; admin = all (still audited).
//!
//! Fail-closed everywhere: unknown role, unknown classification, missing level
//! in the snapshot, or any verification refusal → deny.

use std::collections::BTreeMap;

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use super::ExportDenial;

type HmacSha256 = Hmac<Sha256>;

/// Environment variable holding the snapshot signing/verification key. Mirrors
/// `policy_snapshot.SNAPSHOT_KEY_ENV`. Never hardcoded; never logged.
pub const SNAPSHOT_KEY_ENV: &str = "DF_POLICY_SNAPSHOT_KEY";

// ---------------------------------------------------------------------------
// Export actions, roles, classifications
// ---------------------------------------------------------------------------

/// The export-class actions the Rust core gates. These are the five columns of
/// the S-191 policy matrix (view / copy-text / export-extract /
/// download-original / print).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExportAction {
    /// See the record on screen (no extraction).
    View,
    /// Copy rendered text to the clipboard.
    CopyText,
    /// Export an extracted artefact (e.g. a generated summary / table).
    ExportExtract,
    /// Download the original source file to disk.
    DownloadOriginal,
    /// Send the record to a printer / print-to-PDF.
    Print,
}

impl ExportAction {
    /// Stable wire string used in denials and audit events.
    pub fn as_str(&self) -> &'static str {
        match self {
            ExportAction::View => "view",
            ExportAction::CopyText => "copy-text",
            ExportAction::ExportExtract => "export-extract",
            ExportAction::DownloadOriginal => "download-original",
            ExportAction::Print => "print",
        }
    }
}

/// Roles recognised by the export matrix. Distinct from the broader auth roles
/// because the export defaults (S-191 AC-2) name viewer / analyst / contributor
/// / admin specifically.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Role {
    Viewer,
    Analyst,
    Contributor,
    Admin,
}

impl Role {
    /// Resolve a role string (case-insensitive) to a known role. `None` =>
    /// caller must fail closed with [`ExportDenial::UnknownRole`].
    pub fn parse(s: &str) -> Option<Role> {
        match s.trim().to_ascii_lowercase().as_str() {
            "viewer" => Some(Role::Viewer),
            "analyst" => Some(Role::Analyst),
            "contributor" => Some(Role::Contributor),
            "admin" => Some(Role::Admin),
            _ => None,
        }
    }
}

/// Four-tier classification, ordered least → most sensitive. Mirrors
/// `classification.ClassificationLevel`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Classification {
    Public,
    Internal,
    Confidential,
    Restricted,
}

impl Classification {
    /// Resolve a classification string (case-insensitive) to a known level.
    /// `None` => caller must fail closed with
    /// [`ExportDenial::UnknownClassification`].
    pub fn parse(s: &str) -> Option<Classification> {
        match s.trim().to_ascii_lowercase().as_str() {
            "public" => Some(Classification::Public),
            "internal" => Some(Classification::Internal),
            "confidential" => Some(Classification::Confidential),
            "restricted" => Some(Classification::Restricted),
            _ => None,
        }
    }

    /// Stable wire string.
    pub fn as_str(&self) -> &'static str {
        match self {
            Classification::Public => "public",
            Classification::Internal => "internal",
            Classification::Confidential => "confidential",
            Classification::Restricted => "restricted",
        }
    }
}

// ---------------------------------------------------------------------------
// The default action matrix (S-191 AC-2)
// ---------------------------------------------------------------------------

/// Whether `role` may perform `action` at `classification` under the built-in
/// default policy (S-191 AC-2). The server policy (T-661) is authoritative when
/// reachable; this is the on-device default and the basis the signed snapshot
/// captures.
///
/// Defaults:
/// * **viewer**     — `view` only, at any level.
/// * **analyst**    — viewer rights + `copy-text` / `export-extract` /
///   `print` up to and including `internal`.
/// * **contributor**— analyst rights + `download-original` up to and including
///   `confidential`.
/// * **admin**      — everything, at every level (still audited).
pub fn default_matrix_allows(
    role: Role,
    action: ExportAction,
    classification: Classification,
) -> bool {
    use Classification::*;
    use ExportAction::*;
    use Role::*;

    // Admin: all actions, all levels.
    if role == Admin {
        return true;
    }

    // View is permitted to every recognised role at every level (read access is
    // governed by the auth decision point, not the export matrix).
    if action == View {
        return true;
    }

    match role {
        Viewer => false, // view-only (handled above)
        Analyst => match action {
            CopyText | ExportExtract | Print => {
                // ≤ internal
                classification <= Internal
            }
            DownloadOriginal => false,
            View => true,
        },
        Contributor => match action {
            CopyText | ExportExtract | Print => {
                // contributor keeps analyst's copy/export/print but extends the
                // ceiling to confidential (it is the strictly-more-privileged
                // role).
                classification <= Confidential
            }
            DownloadOriginal => classification <= Confidential,
            View => true,
        },
        Admin => true, // handled above; exhaustive arm
    }
}

// ---------------------------------------------------------------------------
// Signed policy snapshot (verify side — mirrors policy_snapshot.py)
// ---------------------------------------------------------------------------

/// Outcome of verifying a snapshot's signature + freshness. Mirrors
/// `policy_snapshot.SnapshotVerification`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SnapshotVerification {
    Ok,
    Unsigned,
    Tampered,
    Expired,
}

impl SnapshotVerification {
    fn reason(self) -> &'static str {
        match self {
            SnapshotVerification::Ok => "ok",
            SnapshotVerification::Unsigned => "unsigned",
            SnapshotVerification::Tampered => "tampered",
            SnapshotVerification::Expired => "expired",
        }
    }
}

/// A versioned, signed capture of the classification policy, as embedded in the
/// encrypted offline cache. Mirrors `policy_snapshot.SignedPolicySnapshot`.
///
/// `policy` maps each classification level string → the sorted list of allowed
/// role value strings (exactly the `allowed_roles` the server captured). The
/// signature is a hex HMAC-SHA256 over the canonical body; empty == unsigned.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SignedPolicySnapshot {
    pub version: i64,
    pub issued_at: f64,
    pub expires_at: f64,
    pub policy: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub signature: String,
}

impl SignedPolicySnapshot {
    /// Deterministically serialise the snapshot body for signing/verification.
    ///
    /// MUST stay byte-for-byte identical to
    /// `policy_snapshot._canonical_bytes`: `json.dumps(body, sort_keys=True,
    /// separators=(",", ":"))` with timestamps rounded to 3 decimals and each
    /// role list sorted. We build the exact same compact JSON by hand so we do
    /// not depend on serde_json key-ordering guarantees for the nested map.
    fn canonical_bytes(&self) -> Vec<u8> {
        // Sort levels and roles for order-independence (a re-ordering on disk is
        // not a tamper; a value change is).
        let mut levels: Vec<(&String, Vec<String>)> = self
            .policy
            .iter()
            .map(|(level, roles)| {
                let mut r = roles.clone();
                r.sort();
                (level, r)
            })
            .collect();
        levels.sort_by(|a, b| a.0.cmp(b.0));

        let policy_json = {
            let mut parts = Vec::with_capacity(levels.len());
            for (level, roles) in &levels {
                let roles_json = roles
                    .iter()
                    .map(|r| json_string(r))
                    .collect::<Vec<_>>()
                    .join(",");
                parts.push(format!("{}:[{}]", json_string(level), roles_json));
            }
            format!("{{{}}}", parts.join(","))
        };

        // Body keys, sorted: expires_at, issued_at, policy, version. Python's
        // json.dumps(sort_keys=True) orders by key string.
        let body = format!(
            "{{\"expires_at\":{},\"issued_at\":{},\"policy\":{},\"version\":{}}}",
            format_ts(self.expires_at),
            format_ts(self.issued_at),
            policy_json,
            self.version,
        );
        body.into_bytes()
    }

    /// Verify the snapshot's signature and freshness against `key` at `now`
    /// (Unix epoch seconds). Mirrors `policy_snapshot.verify_policy_snapshot`:
    /// signature is checked *before* expiry so a tampered expiry cannot
    /// masquerade as a benign "expired".
    pub fn verify(&self, key: &[u8], now: f64) -> SnapshotVerification {
        if self.signature.is_empty() {
            return SnapshotVerification::Unsigned;
        }
        if key.is_empty() {
            // No key → cannot trust anything → tampered (deny).
            return SnapshotVerification::Tampered;
        }

        let mut mac = match HmacSha256::new_from_slice(key) {
            Ok(m) => m,
            Err(_) => return SnapshotVerification::Tampered,
        };
        mac.update(&self.canonical_bytes());
        let expected = hex_encode(&mac.finalize().into_bytes());

        if !constant_time_eq(expected.as_bytes(), self.signature.as_bytes()) {
            return SnapshotVerification::Tampered;
        }

        if now >= self.expires_at {
            return SnapshotVerification::Expired;
        }

        SnapshotVerification::Ok
    }

    /// Allowed role values for `classification`, or `None` if the level is
    /// absent from the snapshot (caller denies).
    pub fn allowed_roles_for(&self, classification: &str) -> Option<&Vec<String>> {
        self.policy.get(classification)
    }
}

// ---------------------------------------------------------------------------
// The export decision
// ---------------------------------------------------------------------------

/// Resolve the signing/verification key from the environment.
///
/// Mirrors `policy_snapshot._coerce_key`: a valid hex string is decoded to
/// bytes; otherwise the raw UTF-8 bytes are used. Returns `None` when the env
/// var is unset/empty (caller fails closed). Never logs the key.
pub fn snapshot_key_from_env() -> Option<Vec<u8>> {
    let raw = std::env::var(SNAPSHOT_KEY_ENV).ok()?;
    if raw.is_empty() {
        return None;
    }
    Some(coerce_key(&raw))
}

/// Hex-decode when valid hex, else raw UTF-8 bytes. Mirrors `_coerce_key`.
pub fn coerce_key(raw: &str) -> Vec<u8> {
    match hex_decode(raw) {
        Some(bytes) => bytes,
        None => raw.as_bytes().to_vec(),
    }
}

/// Decide whether `role_str` may perform `action` on a record classified
/// `classification_str`.
///
/// When `offline_snapshot` is `Some`, the snapshot is verified first; any
/// refusal (unsigned / tampered / expired) is a [`ExportDenial::SnapshotRefused`]
/// — offline evaluation never falls back to a forgeable on-disk policy. The
/// snapshot's captured `allowed_roles` for the level then gate access, yielding
/// the same decision the online matrix would (S-191 AC-3).
///
/// When `offline_snapshot` is `None`, the built-in default matrix is consulted
/// directly (online / server-policy-trusted path).
///
/// Returns `Ok(())` on allow; `Err(ExportDenial)` on deny. Fail-closed on every
/// unrecognised input.
pub fn decide_export(
    role_str: &str,
    action: ExportAction,
    classification_str: &str,
    offline_snapshot: Option<&SignedPolicySnapshot>,
    snapshot_key: Option<&[u8]>,
    now: f64,
) -> Result<(), ExportDenial> {
    let role = Role::parse(role_str).ok_or_else(|| ExportDenial::UnknownRole {
        role: role_str.to_string(),
    })?;
    let classification =
        Classification::parse(classification_str).ok_or_else(|| {
            ExportDenial::UnknownClassification {
                classification: classification_str.to_string(),
            }
        })?;

    if let Some(snapshot) = offline_snapshot {
        // Offline path: the snapshot must be trustworthy before we read it.
        let key = snapshot_key.unwrap_or(&[]);
        let verdict = snapshot.verify(key, now);
        if verdict != SnapshotVerification::Ok {
            return Err(ExportDenial::SnapshotRefused {
                reason: verdict.reason().to_string(),
            });
        }

        // The snapshot captures, per level, which roles may *access* the level.
        // A role absent from the level's allowed set cannot access it at all, so
        // no export action (even view) is permitted. When the role is present,
        // the same action matrix governs which actions are allowed at the level
        // — the snapshot constrains *access*, the matrix constrains *extraction*.
        let allowed_roles = snapshot.allowed_roles_for(classification.as_str());
        let role_permitted_at_level = match allowed_roles {
            Some(roles) => roles
                .iter()
                .any(|r| r.eq_ignore_ascii_case(role_label(role))),
            None => false, // level absent from snapshot → deny
        };
        if !role_permitted_at_level {
            return Err(ExportDenial::PolicyDenied {
                action: action.as_str().to_string(),
                role: role_str.to_string(),
                classification: classification_str.to_string(),
            });
        }
    }

    // Action matrix (default policy / snapshot-confirmed access).
    if default_matrix_allows(role, action, classification) {
        Ok(())
    } else {
        Err(ExportDenial::PolicyDenied {
            action: action.as_str().to_string(),
            role: role_str.to_string(),
            classification: classification_str.to_string(),
        })
    }
}

/// The canonical lower-case label for a role (used to match snapshot role
/// strings, which are role *value* strings like "analyst").
fn role_label(role: Role) -> &'static str {
    match role {
        Role::Viewer => "viewer",
        Role::Analyst => "analyst",
        Role::Contributor => "contributor",
        Role::Admin => "admin",
    }
}

// ---------------------------------------------------------------------------
// Small, dependency-free encoding helpers (kept local so the canonical body is
// produced by code we control, matching the Python byte-for-byte).
// ---------------------------------------------------------------------------

/// Format a timestamp the way `round(float(x), 3)` + `json.dumps` would render
/// it. Python emits the shortest round-trippable repr; for our snapshots the
/// timestamps are produced by `round(_, 3)` so at most 3 decimals, trailing
/// zeros stripped, and an integer value renders without a decimal point
/// (`json.dumps(1718000000.0)` → `1718000000.0`). We match Python's float repr:
/// a whole number keeps a single `.0`.
fn format_ts(value: f64) -> String {
    // round to 3 decimals to match Python's round(x, 3).
    let rounded = (value * 1000.0).round() / 1000.0;
    if rounded == rounded.trunc() {
        // Whole number → Python renders e.g. 1718000000.0
        format!("{:.1}", rounded)
    } else {
        // Up to 3 decimals, strip trailing zeros (Python's repr does this).
        let mut s = format!("{:.3}", rounded);
        while s.ends_with('0') {
            s.pop();
        }
        if s.ends_with('.') {
            s.push('0');
        }
        s
    }
}

/// JSON-encode a string with the minimal escaping `json.dumps` applies for the
/// characters we expect in role/level names (ASCII identifiers). Quotes,
/// backslashes, and control chars are escaped to stay safe for arbitrary input.
fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Lower-case hex encoding.
fn hex_encode(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{:02x}", b));
    }
    s
}

/// Decode a hex string to bytes; `None` on any non-hex char or odd length.
fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if s.is_empty() || s.len() % 2 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(s.len() / 2);
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let hi = (bytes[i] as char).to_digit(16)?;
        let lo = (bytes[i + 1] as char).to_digit(16)?;
        out.push(((hi << 4) | lo) as u8);
        i += 2;
    }
    Some(out)
}

/// Constant-time byte comparison (mirrors `hmac.compare_digest`). Length-leak is
/// acceptable here (the signature length is fixed for a given hash); the value
/// comparison is the part that must not short-circuit.
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

    // -- enum parsing (fail-closed) -----------------------------------------

    #[test]
    fn role_parse_is_case_insensitive_and_rejects_unknown() {
        assert_eq!(Role::parse("Analyst"), Some(Role::Analyst));
        assert_eq!(Role::parse("  admin "), Some(Role::Admin));
        assert_eq!(Role::parse("superuser"), None);
        assert_eq!(Role::parse(""), None);
    }

    #[test]
    fn classification_parse_is_case_insensitive_and_rejects_unknown() {
        assert_eq!(Classification::parse("RESTRICTED"), Some(Classification::Restricted));
        assert_eq!(Classification::parse("secret"), None);
    }

    // -- default matrix (S-191 AC-2) ----------------------------------------

    #[test]
    fn viewer_is_view_only() {
        use Classification::*;
        use ExportAction::*;
        for level in [Public, Internal, Confidential, Restricted] {
            assert!(default_matrix_allows(Role::Viewer, View, level));
            for action in [CopyText, ExportExtract, DownloadOriginal, Print] {
                assert!(
                    !default_matrix_allows(Role::Viewer, action, level),
                    "viewer must not {action:?} at {level:?}"
                );
            }
        }
    }

    #[test]
    fn analyst_copy_export_up_to_internal_only() {
        use Classification::*;
        use ExportAction::*;
        // ≤ internal: allowed
        assert!(default_matrix_allows(Role::Analyst, CopyText, Public));
        assert!(default_matrix_allows(Role::Analyst, ExportExtract, Internal));
        assert!(default_matrix_allows(Role::Analyst, Print, Internal));
        // > internal: denied
        assert!(!default_matrix_allows(Role::Analyst, CopyText, Confidential));
        assert!(!default_matrix_allows(Role::Analyst, ExportExtract, Restricted));
        // analyst never downloads originals
        assert!(!default_matrix_allows(Role::Analyst, DownloadOriginal, Public));
    }

    #[test]
    fn contributor_downloads_up_to_confidential() {
        use Classification::*;
        use ExportAction::*;
        assert!(default_matrix_allows(Role::Contributor, DownloadOriginal, Internal));
        assert!(default_matrix_allows(Role::Contributor, DownloadOriginal, Confidential));
        // restricted: denied even for contributor
        assert!(!default_matrix_allows(Role::Contributor, DownloadOriginal, Restricted));
        assert!(!default_matrix_allows(Role::Contributor, CopyText, Restricted));
    }

    #[test]
    fn admin_can_do_everything_everywhere() {
        use Classification::*;
        use ExportAction::*;
        for level in [Public, Internal, Confidential, Restricted] {
            for action in [View, CopyText, ExportExtract, DownloadOriginal, Print] {
                assert!(default_matrix_allows(Role::Admin, action, level));
            }
        }
    }

    // -- snapshot canonical body parity -------------------------------------

    fn sample_snapshot(version: i64, issued: f64, expires: f64) -> SignedPolicySnapshot {
        let mut policy = BTreeMap::new();
        policy.insert("public".to_string(), vec!["viewer".to_string(), "analyst".to_string()]);
        policy.insert("restricted".to_string(), vec!["admin".to_string()]);
        SignedPolicySnapshot {
            version,
            issued_at: issued,
            expires_at: expires,
            policy,
            signature: String::new(),
        }
    }

    /// The canonical body must equal what Python's `_canonical_bytes` produces:
    /// compact JSON, sorted keys (expires_at, issued_at, policy, version),
    /// sorted role lists, whole-number timestamps rendered with a single `.0`.
    #[test]
    fn canonical_body_matches_python_format() {
        let snap = sample_snapshot(3, 1718000000.0, 1718604800.0);
        let body = String::from_utf8(snap.canonical_bytes()).unwrap();
        let expected = "{\"expires_at\":1718604800.0,\"issued_at\":1718000000.0,\
\"policy\":{\"public\":[\"analyst\",\"viewer\"],\"restricted\":[\"admin\"]},\
\"version\":3}";
        assert_eq!(body, expected);
    }

    #[test]
    fn canonical_body_renders_fractional_timestamp() {
        let snap = sample_snapshot(1, 1718000000.25, 1718000000.5);
        let body = String::from_utf8(snap.canonical_bytes()).unwrap();
        assert!(body.contains("\"issued_at\":1718000000.25"));
        assert!(body.contains("\"expires_at\":1718000000.5"));
    }

    // -- sign locally, verify, and negative cases ---------------------------

    fn sign(snap: &SignedPolicySnapshot, key: &[u8]) -> String {
        let mut mac = HmacSha256::new_from_slice(key).unwrap();
        mac.update(&snap.canonical_bytes());
        hex_encode(&mac.finalize().into_bytes())
    }

    #[test]
    fn verify_ok_for_valid_signature_and_fresh() {
        let key = b"super-secret-key";
        let mut snap = sample_snapshot(1, 1000.0, 5000.0);
        snap.signature = sign(&snap, key);
        assert_eq!(snap.verify(key, 2000.0), SnapshotVerification::Ok);
    }

    #[test]
    fn verify_unsigned_when_signature_empty() {
        let snap = sample_snapshot(1, 1000.0, 5000.0);
        assert_eq!(snap.verify(b"k", 2000.0), SnapshotVerification::Unsigned);
    }

    #[test]
    fn verify_tampered_when_policy_altered() {
        let key = b"super-secret-key";
        let mut snap = sample_snapshot(1, 1000.0, 5000.0);
        snap.signature = sign(&snap, key);
        // widen the policy after signing
        snap.policy.get_mut("restricted").unwrap().push("viewer".to_string());
        assert_eq!(snap.verify(key, 2000.0), SnapshotVerification::Tampered);
    }

    #[test]
    fn verify_tampered_when_wrong_key() {
        let mut snap = sample_snapshot(1, 1000.0, 5000.0);
        snap.signature = sign(&snap, b"key-a");
        assert_eq!(snap.verify(b"key-b", 2000.0), SnapshotVerification::Tampered);
    }

    #[test]
    fn verify_expired_when_past_expiry() {
        let key = b"super-secret-key";
        let mut snap = sample_snapshot(1, 1000.0, 5000.0);
        snap.signature = sign(&snap, key);
        assert_eq!(snap.verify(key, 6000.0), SnapshotVerification::Expired);
    }

    #[test]
    fn verify_signature_checked_before_expiry() {
        // A tampered, also-expired snapshot reports Tampered, not Expired.
        let key = b"super-secret-key";
        let mut snap = sample_snapshot(1, 1000.0, 5000.0);
        snap.signature = sign(&snap, key);
        snap.expires_at = 1.0; // tamper expiry → both expired-looking and tampered
        assert_eq!(snap.verify(key, 6000.0), SnapshotVerification::Tampered);
    }

    // -- decide_export end to end -------------------------------------------

    #[test]
    fn decide_online_default_matrix_allows_and_denies() {
        // analyst copy at internal → allow (online path, no snapshot)
        assert!(decide_export("analyst", ExportAction::CopyText, "internal", None, None, 0.0).is_ok());
        // analyst copy at confidential → deny
        let err = decide_export("analyst", ExportAction::CopyText, "confidential", None, None, 0.0)
            .unwrap_err();
        assert!(matches!(err, ExportDenial::PolicyDenied { .. }));
    }

    #[test]
    fn decide_unknown_role_or_classification_fails_closed() {
        let e1 = decide_export("root", ExportAction::View, "public", None, None, 0.0).unwrap_err();
        assert!(matches!(e1, ExportDenial::UnknownRole { .. }));
        let e2 = decide_export("admin", ExportAction::View, "topsecret", None, None, 0.0).unwrap_err();
        assert!(matches!(e2, ExportDenial::UnknownClassification { .. }));
    }

    #[test]
    fn decide_offline_refuses_unsigned_snapshot() {
        let snap = sample_snapshot(1, 1000.0, 5000.0); // unsigned
        let err = decide_export(
            "admin",
            ExportAction::View,
            "public",
            Some(&snap),
            Some(b"k"),
            2000.0,
        )
        .unwrap_err();
        assert!(matches!(err, ExportDenial::SnapshotRefused { reason } if reason == "unsigned"));
    }

    #[test]
    fn decide_offline_equals_online_for_valid_snapshot() {
        let key = b"super-secret-key";
        // snapshot grants analyst access at internal
        let mut policy = BTreeMap::new();
        policy.insert("internal".to_string(), vec!["analyst".to_string(), "admin".to_string()]);
        let mut snap = SignedPolicySnapshot {
            version: 1,
            issued_at: 1000.0,
            expires_at: 5000.0,
            policy,
            signature: String::new(),
        };
        snap.signature = sign(&snap, key);

        // analyst copy-text at internal: online allows; offline (valid snapshot
        // granting analyst access) must also allow.
        let online =
            decide_export("analyst", ExportAction::CopyText, "internal", None, None, 2000.0);
        let offline = decide_export(
            "analyst",
            ExportAction::CopyText,
            "internal",
            Some(&snap),
            Some(key),
            2000.0,
        );
        assert!(online.is_ok());
        assert!(offline.is_ok());
    }

    #[test]
    fn decide_offline_denies_role_absent_from_snapshot_level() {
        let key = b"super-secret-key";
        // snapshot grants only admin at restricted
        let mut policy = BTreeMap::new();
        policy.insert("restricted".to_string(), vec!["admin".to_string()]);
        let mut snap = SignedPolicySnapshot {
            version: 1,
            issued_at: 1000.0,
            expires_at: 5000.0,
            policy,
            signature: String::new(),
        };
        snap.signature = sign(&snap, key);

        // contributor is not in the restricted allowed set → deny even view.
        let err = decide_export(
            "contributor",
            ExportAction::View,
            "restricted",
            Some(&snap),
            Some(key),
            2000.0,
        )
        .unwrap_err();
        assert!(matches!(err, ExportDenial::PolicyDenied { .. }));
    }

    #[test]
    fn key_coercion_hex_then_utf8() {
        // even-length valid hex → decoded bytes
        assert_eq!(coerce_key("4869"), vec![0x48, 0x69]);
        // not hex → raw utf-8
        assert_eq!(coerce_key("hello-key"), b"hello-key".to_vec());
    }
}
