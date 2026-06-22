use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct AppInfo {
    pub version: String,
    pub name: String,
}

#[tauri::command]
pub fn get_app_info() -> AppInfo {
    AppInfo {
        version: env!("CARGO_PKG_VERSION").to_string(),
        name: env!("CARGO_PKG_NAME").to_string(),
    }
}

#[tauri::command]
pub fn ping(message: String) -> String {
    format!("pong: {}", message)
}

/// Check whether a DepthFusion server is reachable and healthy.
///
/// Issues a `GET {url}/health` with a 5-second timeout. Returns:
///   - `Ok(true)`  when the server responds with a 2xx status,
///   - `Ok(false)` when the server responds with a non-2xx status,
///   - `Ok(false)` on any network-level error (timeout, DNS failure,
///     connection refused, etc.).
///
/// This command never returns `Err`: an unreachable host is a normal,
/// expected outcome during the setup wizard (the user may not have started
/// their server yet), so the frontend treats it as "not ready" rather than a
/// hard failure.
#[tauri::command]
pub async fn check_server_health(url: String) -> Result<bool, String> {
    Ok(probe_health(&url).await)
}

/// Pure async probe extracted from the `#[tauri::command]` wrapper so it is
/// directly unit-testable. Builds a 5-second-timeout client, GETs the
/// `{url}/health` endpoint, and maps the result to a bool. Any error (client
/// build failure or request failure) resolves to `false` — never propagated.
async fn probe_health(url: &str) -> bool {
    let endpoint = format!("{}/health", url.trim_end_matches('/'));

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };

    match client.get(&endpoint).send().await {
        Ok(resp) => resp.status().is_success(),
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn probe_health_returns_true_on_2xx() {
        let mut server = mockito::Server::new_async().await;
        let m = server
            .mock("GET", "/health")
            .with_status(200)
            .create_async()
            .await;

        let ok = probe_health(&server.url()).await;
        assert!(ok, "2xx /health must yield true");
        m.assert_async().await;
    }

    #[tokio::test]
    async fn probe_health_returns_false_on_5xx() {
        let mut server = mockito::Server::new_async().await;
        let _m = server
            .mock("GET", "/health")
            .with_status(503)
            .create_async()
            .await;

        let ok = probe_health(&server.url()).await;
        assert!(!ok, "non-2xx /health must yield false");
    }

    #[tokio::test]
    async fn probe_health_returns_false_on_unreachable_host() {
        // Reserved-for-documentation IP (TEST-NET-1, RFC 5737) on an unused
        // port: guaranteed unroutable, so the request errors rather than
        // connecting. Must resolve to false, never panic or propagate.
        let ok = probe_health("http://192.0.2.1:1").await;
        assert!(!ok, "unreachable host must yield false, not an error");
    }

    #[tokio::test]
    async fn probe_health_trims_trailing_slash() {
        let mut server = mockito::Server::new_async().await;
        let m = server
            .mock("GET", "/health")
            .with_status(200)
            .create_async()
            .await;

        // A trailing slash on the base URL must not produce a double slash.
        let base = format!("{}/", server.url());
        let ok = probe_health(&base).await;
        assert!(ok);
        m.assert_async().await;
    }
}
