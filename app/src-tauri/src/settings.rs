//! Settings commands backed by `tauri-plugin-store`.
//!
//! Provides typed IPC access to persistent key/value configuration.
//! The store file is `settings.json` inside the app's data directory.

use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_PATH: &str = "settings.json";
const SERVER_URL_KEY: &str = "server_url";
const DEFAULT_SERVER_URL: &str = "http://176.9.147.206:7300";

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
