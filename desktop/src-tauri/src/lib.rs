pub mod engine;

use engine::Engine;
use serde_json::{json, Value};
use tauri::Manager;

#[tauri::command]
async fn engine_call(
    state: tauri::State<'_, Engine>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    let engine = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || engine.call(&method, params))
        .await
        .map_err(|e| format!("engine call worker error: {e}"))?
}

#[tauri::command]
fn engine_status(state: tauri::State<'_, Engine>) -> Value {
    json!({ "running": state.is_running(), "pid": state.pid() })
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            app.manage(Engine::spawn(Some(app.handle().clone()))?);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_call, engine_status])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(engine) = app_handle.try_state::<Engine>() {
                    let _ = engine.shutdown();
                }
            }
        });
}