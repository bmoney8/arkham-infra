//! Native commands exposed to the cockpit webview (V6.3.4 D4.1).
//!
//! Kept in a dedicated module: `generate_handler!` path-qualifies command
//! macro names, which collides when the command lives in the crate root
//! alongside the handler registration.

use serde::Serialize;

/// Result of the native OmniRouter health check.
///
/// The webview cannot fetch `http://100.64.0.3:8000` directly: mesh IPs send
/// no CORS headers, so the browser-context fetch is always blocked and the
/// status dot falsely reports "Gateway unreachable". This command performs
/// the check from the Rust core (reqwest, no CORS) and returns the result.
#[derive(Serialize)]
pub struct OmnirtHealth {
    pub ok: bool,
    pub status_code: u16,
    pub models: u32,
    pub error: Option<String>,
}

#[tauri::command]
pub async fn check_omnirt_health() -> Result<OmnirtHealth, String> {
    let url = std::env::var("OMNIRT_HEALTH_URL")
        .unwrap_or_else(|_| "http://100.64.0.3:8000/v1/models".to_string());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(6))
        .build()
        .map_err(|e| format!("client build failed: {e}"))?;
    match client
        .get(&url)
        .header("Authorization", "Bearer arkham-cockpit")
        .send()
        .await
    {
        Ok(resp) => {
            let code = resp.status().as_u16();
            let models = if code == 200 {
                resp.json::<serde_json::Value>()
                    .await
                    .ok()
                    .and_then(|v| {
                        v.get("data")
                            .and_then(|d| d.as_array())
                            .map(|a| a.len() as u32)
                    })
                    .unwrap_or(0)
            } else {
                0
            };
            Ok(OmnirtHealth {
                ok: code == 200,
                status_code: code,
                models,
                error: None,
            })
        }
        Err(e) => Ok(OmnirtHealth {
            ok: false,
            status_code: 0,
            models: 0,
            error: Some(e.to_string()),
        }),
    }
}
