//! Settings commands backed by `tauri-plugin-store`.
//!
//! Provides typed IPC access to persistent key/value configuration.
//! The store file is `settings.json` inside the app's data directory.

use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_PATH: &str = "settings.json";
const SERVER_URL_KEY: &str = "server_url";
const WIZARD_COMPLETED_KEY: &str = "wizard_completed";
const DEPLOYMENT_MODE_KEY: &str = "deployment_mode";
const DEFAULT_SERVER_URL: &str = "https://mcp.tonracein.com";

/// Retrieve the configured server URL.
///
/// Returns the stored URL when set, or the default `https://localhost:8000`.
#[tauri::command]
pub fn get_server_url(app: AppHandle) -> Result<String, String> {
    let store = app
        .store(STORE_PATH)
        .map_err(|e| format!("Failed to open settings store: {e}"))?;

    let url = store
        .get(SERVER_URL_KEY)
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| DEFAULT_SERVER_URL.to_string());

    Ok(url)
}

/// Persist the server URL.
///
/// Validates that the value is a non-empty string before writing.
#[tauri::command]
pub fn set_server_url(app: AppHandle, url: String) -> Result<(), String> {
    if url.trim().is_empty() {
        return Err("server_url must not be empty".to_string());
    }

    let store = app
        .store(STORE_PATH)
        .map_err(|e| format!("Failed to open settings store: {e}"))?;

    store.set(SERVER_URL_KEY, serde_json::Value::String(url));
    store.save().map_err(|e| format!("Failed to persist settings: {e}"))?;

    Ok(())
}

/// Retrieve whether the first-run setup wizard has been completed.
///
/// Returns `false` when the key is absent (fresh install) so the frontend
/// shows the wizard on first launch.
#[tauri::command]
pub fn get_wizard_completed(app: AppHandle) -> Result<bool, String> {
    let store = app
        .store(STORE_PATH)
        .map_err(|e| format!("Failed to open settings store: {e}"))?;

    let completed = store
        .get(WIZARD_COMPLETED_KEY)
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    Ok(completed)
}

/// Persist the wizard-completed flag.
#[tauri::command]
pub fn set_wizard_completed(app: AppHandle, completed: bool) -> Result<(), String> {
    let store = app
        .store(STORE_PATH)
        .map_err(|e| format!("Failed to open settings store: {e}"))?;

    store.set(WIZARD_COMPLETED_KEY, serde_json::Value::Bool(completed));
    store.save().map_err(|e| format!("Failed to persist settings: {e}"))?;

    Ok(())
}

/// Retrieve the configured deployment mode (`solo`, `vps`, or `connect`).
///
/// Returns `None` when the key is absent (no mode chosen yet).
#[tauri::command]
pub fn get_deployment_mode(app: AppHandle) -> Result<Option<String>, String> {
    let store = app
        .store(STORE_PATH)
        .map_err(|e| format!("Failed to open settings store: {e}"))?;

    let mode = store
        .get(DEPLOYMENT_MODE_KEY)
        .and_then(|v| v.as_str().map(|s| s.to_string()));

    Ok(mode)
}

/// Persist the deployment mode.
///
/// Validates that the value is a non-empty string before writing.
#[tauri::command]
pub fn set_deployment_mode(app: AppHandle, mode: String) -> Result<(), String> {
    if mode.trim().is_empty() {
        return Err("deployment_mode must not be empty".to_string());
    }

    let store = app
        .store(STORE_PATH)
        .map_err(|e| format!("Failed to open settings store: {e}"))?;

    store.set(DEPLOYMENT_MODE_KEY, serde_json::Value::String(mode));
    store.save().map_err(|e| format!("Failed to persist settings: {e}"))?;

    Ok(())
}
