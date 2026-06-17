//! Policy-gated export IPC commands (E-59 S-192 AC-1, T-663).
//!
//! Clipboard, file-save, and print become **Rust-mediated** Tauri IPC commands.
//! The webview cannot copy/save/print directly — it asks the core, the core
//! consults the export policy ([`super::policy::decide_export`]), and either
//! performs the action or returns a typed [`ExportDenial`] the UI explains.
//!
//! Each command resolves the snapshot signing key from the environment
//! (`DF_POLICY_SNAPSHOT_KEY`) — never hardcoded, never logged — and threads an
//! optional offline policy snapshot through to the decision so offline
//! evaluation is gated by the verified signed snapshot (S-191 AC-3).
//!
//! Wall-clock `now` for snapshot expiry is taken once per command via
//! [`now_unix_secs`]; the pure decision/streaming logic in [`super::policy`] and
//! [`super::stream`] takes `now` as a parameter so it stays unit-testable
//! without a clock.

use std::path::PathBuf;

use super::policy::{decide_export, snapshot_key_from_env, ExportAction, SignedPolicySnapshot};
use super::stream::{stream_original_file, StreamResult};
use super::{ExportDenial, ExportOutcome};

/// Wall-clock now in Unix epoch seconds (as `f64` to match the snapshot's
/// timestamp type). Falls back to `0.0` on the impossible pre-epoch error,
/// which — combined with the verifier's `now >= expires_at` check — errs toward
/// treating snapshots as not-yet-expired only when the clock is sane; a `0.0`
/// clock simply never trips the expiry branch, while signature verification
/// (the security-critical check) is unaffected.
fn now_unix_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Shared pre-flight: resolve the snapshot key and evaluate the export policy
/// for `action`. Returns `Ok(())` on allow, `Err(ExportOutcome::Denied)` on
/// deny so callers can early-return the typed outcome.
fn gate(
    role: &str,
    action: ExportAction,
    classification: &str,
    offline_snapshot: Option<&SignedPolicySnapshot>,
) -> Result<(), ExportDenial> {
    let key = snapshot_key_from_env();
    let key_ref = key.as_deref();
    decide_export(
        role,
        action,
        classification,
        offline_snapshot,
        key_ref,
        now_unix_secs(),
    )
}

// ---------------------------------------------------------------------------
// Clipboard
// ---------------------------------------------------------------------------

/// Copy `text` to the clipboard **iff** the principal's role may `copy-text` at
/// `classification`. On allow the (possibly footer-augmented) text is returned
/// to the webview as the result of an allowed outcome; the webview then has the
/// core's blessing to place it on the system clipboard via the platform
/// clipboard plugin.
///
/// Returning the text (rather than writing the clipboard here) keeps this
/// command pure and unit-testable while still making the *decision* in the
/// core: a denied copy yields no text, only a denial. The provenance footer
/// (T-665) will be appended to `value` on the allow path for confidential+.
#[tauri::command]
pub fn export_copy_text(
    role: String,
    classification: String,
    text: String,
    offline_snapshot: Option<SignedPolicySnapshot>,
) -> ExportOutcome<String> {
    match gate(
        &role,
        ExportAction::CopyText,
        &classification,
        offline_snapshot.as_ref(),
    ) {
        Ok(()) => ExportOutcome::allowed(text),
        Err(denial) => ExportOutcome::denied(denial),
    }
}

// ---------------------------------------------------------------------------
// File save (export-extract)
// ---------------------------------------------------------------------------

/// Save an extracted artefact's `content` to `dest_path` **iff** the role may
/// `export-extract` at `classification`. On allow the bytes are written by the
/// core and the on-disk path is returned; on deny nothing is written.
///
/// This is the *extract* path (generated summaries/tables), distinct from the
/// *original-file* streaming gate ([`export_download_original`]). Extract
/// content originates inside the app, so passing it as a `String` payload is
/// acceptable; originals must never transit the webview, hence their separate
/// path.
#[tauri::command]
pub fn export_save_extract(
    role: String,
    classification: String,
    content: String,
    dest_path: String,
    offline_snapshot: Option<SignedPolicySnapshot>,
) -> ExportOutcome<StreamResult> {
    if let Err(denial) = gate(
        &role,
        ExportAction::ExportExtract,
        &classification,
        offline_snapshot.as_ref(),
    ) {
        return ExportOutcome::denied(denial);
    }

    let path = PathBuf::from(&dest_path);
    match std::fs::write(&path, content.as_bytes()) {
        Ok(()) => ExportOutcome::allowed(StreamResult {
            bytes_written: content.as_bytes().len() as u64,
            destination: dest_path,
        }),
        Err(e) => ExportOutcome::denied(ExportDenial::Internal {
            message: format!("save failed: {e}"),
        }),
    }
}

// ---------------------------------------------------------------------------
// Print
// ---------------------------------------------------------------------------

/// A print job the webview may dispatch once the core has authorised it.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PrintJob {
    pub record_id: String,
    pub classification: String,
    /// Opaque token the webview presents to the print bridge; its presence is
    /// the proof the core authorised this specific job.
    pub authorised: bool,
}

/// Authorise a print of `record_id` **iff** the role may `print` at
/// `classification`. On allow a [`PrintJob`] with `authorised: true` is
/// returned; on deny a typed denial. The webview cannot print without first
/// obtaining an authorised job from the core.
#[tauri::command]
pub fn export_print(
    role: String,
    classification: String,
    record_id: String,
    offline_snapshot: Option<SignedPolicySnapshot>,
) -> ExportOutcome<PrintJob> {
    match gate(
        &role,
        ExportAction::Print,
        &classification,
        offline_snapshot.as_ref(),
    ) {
        Ok(()) => ExportOutcome::allowed(PrintJob {
            record_id,
            classification,
            authorised: true,
        }),
        Err(denial) => ExportOutcome::denied(denial),
    }
}

// ---------------------------------------------------------------------------
// Original-file streaming gate (T-664)
// ---------------------------------------------------------------------------

/// Stream an original file from `source_path` to `dest_path` through the Rust
/// core **iff** the role may `download-original` at `classification`. The bytes
/// never reach the webview — only the result (a byte count + path) or a typed
/// denial. On deny the destination is never created.
#[tauri::command]
pub fn export_download_original(
    role: String,
    classification: String,
    source_path: String,
    dest_path: String,
    offline_snapshot: Option<SignedPolicySnapshot>,
) -> ExportOutcome<StreamResult> {
    let key = snapshot_key_from_env();
    stream_original_file(
        &role,
        &classification,
        offline_snapshot.as_ref(),
        key.as_deref(),
        now_unix_secs(),
        &PathBuf::from(&source_path),
        &PathBuf::from(&dest_path),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copy_text_allowed_returns_text() {
        let out = export_copy_text(
            "analyst".into(),
            "internal".into(),
            "hello".into(),
            None,
        );
        match out {
            ExportOutcome::Allowed { value } => assert_eq!(value, "hello"),
            ExportOutcome::Denied { denial } => panic!("expected allow: {denial:?}"),
        }
    }

    #[test]
    fn copy_text_denied_for_viewer() {
        let out = export_copy_text(
            "viewer".into(),
            "public".into(),
            "secret".into(),
            None,
        );
        assert!(out.is_denied());
    }

    #[test]
    fn copy_text_denied_above_analyst_ceiling() {
        let out = export_copy_text(
            "analyst".into(),
            "confidential".into(),
            "x".into(),
            None,
        );
        match out {
            ExportOutcome::Denied { denial } => {
                assert!(matches!(denial, ExportDenial::PolicyDenied { .. }))
            }
            ExportOutcome::Allowed { .. } => panic!("analyst must be denied at confidential"),
        }
    }

    #[test]
    fn print_authorised_for_admin() {
        let out = export_print(
            "admin".into(),
            "restricted".into(),
            "rec-1".into(),
            None,
        );
        match out {
            ExportOutcome::Allowed { value } => {
                assert!(value.authorised);
                assert_eq!(value.record_id, "rec-1");
            }
            ExportOutcome::Denied { denial } => panic!("admin print must be allowed: {denial:?}"),
        }
    }

    #[test]
    fn print_denied_for_viewer() {
        let out = export_print(
            "viewer".into(),
            "internal".into(),
            "rec-2".into(),
            None,
        );
        assert!(out.is_denied());
    }

    #[test]
    fn save_extract_denied_for_viewer_writes_nothing() {
        use std::time::{SystemTime, UNIX_EPOCH};
        let tag = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dst = std::env::temp_dir().join(format!("df-extract-{tag}.txt"));

        let out = export_save_extract(
            "viewer".into(),
            "public".into(),
            "content".into(),
            dst.to_string_lossy().to_string(),
            None,
        );
        assert!(out.is_denied());
        assert!(!dst.exists(), "denied extract must not be written");
    }

    #[test]
    fn save_extract_allowed_for_analyst_writes_file() {
        use std::time::{SystemTime, UNIX_EPOCH};
        let tag = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dst = std::env::temp_dir().join(format!("df-extract-ok-{tag}.txt"));

        let out = export_save_extract(
            "analyst".into(),
            "internal".into(),
            "extracted summary".into(),
            dst.to_string_lossy().to_string(),
            None,
        );
        assert!(out.is_allowed());
        assert_eq!(std::fs::read_to_string(&dst).unwrap(), "extracted summary");
        let _ = std::fs::remove_file(&dst);
    }

    #[test]
    fn download_original_denied_for_analyst() {
        use std::time::{SystemTime, UNIX_EPOCH};
        let tag = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir();
        let src = dir.join(format!("df-orig-src-{tag}.bin"));
        let dst = dir.join(format!("df-orig-dst-{tag}.bin"));
        std::fs::write(&src, b"orig").unwrap();

        let out = export_download_original(
            "analyst".into(),
            "internal".into(),
            src.to_string_lossy().to_string(),
            dst.to_string_lossy().to_string(),
            None,
        );
        assert!(out.is_denied());
        assert!(!dst.exists());
        let _ = std::fs::remove_file(&src);
    }
}
