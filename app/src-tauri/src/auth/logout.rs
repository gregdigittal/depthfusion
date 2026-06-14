//! Sign-out and local-wipe logic for the DepthFusion Tauri app.
//!
//! `wipe_local_state()` performs a best-effort purge of every piece of locally
//! stored session data:
//!
//!   1. OS keychain token vault  (via `vault::clear_tokens`)
//!   2. Tauri `AppData` / `userData` directory  (platform-specific app data)
//!   3. Any temporary files written under the system temp dir that carry the
//!      `depthfusion-` prefix
//!
//! Each step is attempted independently so a failure in one does not prevent
//! the others from running. The returned `Vec<WipeError>` lists every step that
//! failed; an empty vec means a clean wipe.

use std::path::PathBuf;

use super::vault;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Records a single failed wipe step so the caller can decide whether to
/// surface a warning to the user.
#[derive(Debug)]
pub struct WipeError {
    pub step: &'static str,
    pub message: String,
}

impl WipeError {
    fn new(step: &'static str, message: impl Into<String>) -> Self {
        Self { step, message: message.into() }
    }
}

impl std::fmt::Display for WipeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.step, self.message)
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Wipe all locally stored session / user-data for a clean sign-out.
///
/// Returns a list of non-fatal errors encountered during the wipe.  The wipe
/// is considered successful (from the security perspective) as long as the
/// vault is cleared — file-system cleanup failures are surfaced but do not
/// prevent the sign-out from completing.
pub fn wipe_local_state(app_data_dir: Option<PathBuf>) -> Vec<WipeError> {
    let mut errors: Vec<WipeError> = Vec::new();

    // Step 1 — Clear the OS keychain token vault.
    if let Err(e) = vault::clear_tokens() {
        errors.push(WipeError::new("vault", e.to_string()));
    }

    // Step 2 — Remove the Tauri AppData / userData directory.
    if let Some(dir) = app_data_dir {
        if dir.exists() {
            if let Err(e) = std::fs::remove_dir_all(&dir) {
                errors.push(WipeError::new(
                    "app_data",
                    format!("Failed to remove {}: {}", dir.display(), e),
                ));
            }
        }
        // If the directory doesn't exist there is nothing to wipe — not an error.
    }

    // Step 3 — Remove any `depthfusion-*` temp files.
    errors.extend(wipe_temp_files());

    errors
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

fn wipe_temp_files() -> Vec<WipeError> {
    let mut errors = Vec::new();

    let temp_dir = std::env::temp_dir();
    let entries = match std::fs::read_dir(&temp_dir) {
        Ok(e) => e,
        Err(e) => {
            errors.push(WipeError::new(
                "temp_files",
                format!("Cannot read temp dir {}: {}", temp_dir.display(), e),
            ));
            return errors;
        }
    };

    for entry in entries.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if name_str.starts_with("depthfusion-") {
            let path = entry.path();
            let result = if path.is_dir() {
                std::fs::remove_dir_all(&path)
            } else {
                std::fs::remove_file(&path)
            };
            if let Err(e) = result {
                errors.push(WipeError::new(
                    "temp_files",
                    format!("Failed to remove {}: {}", path.display(), e),
                ));
            }
        }
    }

    errors
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: create a fresh vault entry so `clear_tokens` has something to delete.
    fn seed_vault() {
        let tokens = vault::TokenSet {
            access_token: "test-access-token".to_string(),
            id_token: Some("test-id-token".to_string()),
            refresh_token: Some("test-refresh-token".to_string()),
            expires_in: Some(3600),
            token_type: "Bearer".to_string(),
            stored_at: Some(std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs()),
        };
        // Ignore errors — the keychain may not be available in CI.
        let _ = vault::store_tokens(&tokens);
    }

    #[test]
    fn wipe_clears_vault() {
        // Use the in-memory keyring mock so this test does not depend on a live
        // OS Secret Service (unavailable in headless CI — no $DISPLAY/DBus).
        vault::install_mock_keystore();
        seed_vault();

        // Run the wipe without an app_data_dir so we only exercise the vault step.
        let errors = wipe_local_state(None);

        // After the wipe, loading tokens should return None (or fail outright on
        // platforms without a keychain — both outcomes mean the vault is clear).
        match vault::load_tokens() {
            Ok(Some(_)) => panic!("vault still contains tokens after wipe"),
            Ok(None) | Err(_) => {} // expected
        }

        // Any errors that occurred must NOT be from the vault step.
        for e in &errors {
            assert_ne!(
                e.step, "vault",
                "vault wipe step should not error, but got: {}",
                e.message
            );
        }
    }

    #[test]
    fn wipe_is_idempotent_when_vault_empty() {
        // Use the in-memory keyring mock so this test does not depend on a live
        // OS Secret Service (unavailable in headless CI — no $DISPLAY/DBus).
        vault::install_mock_keystore();
        // Ensure vault is empty first.
        let _ = vault::clear_tokens();

        // Calling wipe on an already-empty vault should not produce vault errors.
        let errors = wipe_local_state(None);
        let vault_errors: Vec<_> = errors.iter().filter(|e| e.step == "vault").collect();
        assert!(
            vault_errors.is_empty(),
            "unexpected vault errors on empty vault: {:?}",
            vault_errors
        );
    }

    #[test]
    fn wipe_removes_app_data_dir() {
        // Create a temporary directory to simulate the app data dir.
        let temp_base = std::env::temp_dir();
        let app_data = temp_base.join("depthfusion-test-app-data");
        std::fs::create_dir_all(&app_data).expect("create test app_data dir");

        // Place a sentinel file inside.
        let sentinel = app_data.join("session.db");
        std::fs::write(&sentinel, b"test").expect("write sentinel");

        assert!(app_data.exists());

        let errors = wipe_local_state(Some(app_data.clone()));

        // Directory should no longer exist.
        assert!(!app_data.exists(), "app_data dir should be removed after wipe");

        // No app_data errors expected.
        let dir_errors: Vec<_> = errors.iter().filter(|e| e.step == "app_data").collect();
        assert!(dir_errors.is_empty(), "unexpected app_data errors: {:?}", dir_errors);
    }

    #[test]
    fn wipe_removes_temp_files_with_prefix() {
        let temp_dir = std::env::temp_dir();

        // Create two files: one with the prefix, one without.
        let prefixed = temp_dir.join("depthfusion-cache-test.tmp");
        let unrelated = temp_dir.join("unrelated-app-test.tmp");
        std::fs::write(&prefixed, b"sensitive").expect("write prefixed temp file");
        std::fs::write(&unrelated, b"other").expect("write unrelated temp file");

        let _errors = wipe_local_state(None);

        assert!(!prefixed.exists(), "prefixed temp file should be removed");
        assert!(unrelated.exists(), "unrelated temp file should be untouched");

        // Clean up.
        let _ = std::fs::remove_file(&unrelated);
    }
}
