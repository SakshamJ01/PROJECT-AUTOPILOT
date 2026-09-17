//! Manages the Python bridge child process (stdio JSON-RPC over newline framing).
//!
//! The Rust shell handles process lifetime and request/response routing only.
//! No business logic lives here; the engine remains authoritative.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const SHUTDOWN_GRACE: Duration = Duration::from_millis(600);

#[derive(serde::Deserialize, serde::Serialize)]
#[serde(default)]
struct RpcFrame {
    id: Option<i64>,
    result: Option<Value>,
    error: Option<RpcError>,
    method: Option<String>,
    params: Option<Value>,
}

impl Default for RpcFrame {
    fn default() -> Self {
        Self {
            id: None,
            result: None,
            error: None,
            method: None,
            params: None,
        }
    }
}

#[derive(serde::Deserialize, serde::Serialize, Default)]
struct RpcError {
    code: i64,
    message: String,
    data: Option<Value>,
}

struct EngineInternal {
    child: Mutex<Child>,
    stdin: Mutex<ChildStdin>,
    pending: Mutex<HashMap<u64, Sender<Result<Value, String>>>>,
}

pub struct Engine {
    internal: Arc<EngineInternal>,
    next_id: AtomicU64,
    app: Option<AppHandle>,
}

impl Engine {
    pub fn spawn(app: Option<AppHandle>) -> Result<Self, String> {
        let python_cmd = std::env::var("AUTOPILOT_PYTHON").unwrap_or_else(|_| "python".to_string());

        let mut cmd = Command::new(&python_cmd);
        cmd.arg("-u").arg("-m").arg("autopilot.bridge");
        if let Ok(db) = std::env::var("AUTOPILOT_DB_PATH") {
            cmd.env("AUTOPILOT_DB_PATH", db);
        }
        if let Ok(artifacts) = std::env::var("AUTOPILOT_ARTIFACTS_DIR") {
            cmd.env("AUTOPILOT_ARTIFACTS_DIR", artifacts);
        }
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| format!("failed to spawn bridge: {e}"))?;
        let stdin = child.stdin.take().ok_or("bridge stdin unavailable")?;
        let stdout = child.stdout.take().ok_or("bridge stdout unavailable")?;
        let stderr = child.stderr.take().ok_or("bridge stderr unavailable")?;

        let internal = Arc::new(EngineInternal {
            child: Mutex::new(child),
            stdin: Mutex::new(stdin),
            pending: Mutex::new(HashMap::new()),
        });

        let engine = Engine {
            internal,
            next_id: AtomicU64::new(0),
            app,
        };

        engine.start_reader(stdout, true);
        engine.start_reader(stderr, false);
        Ok(engine)
    }

    fn start_reader<R: std::io::Read + Send + 'static>(&self, stream: R, parse_json: bool) {
        let internal = Arc::clone(&self.internal);
        let app = self.app.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stream);
            for line in reader.lines().map_while(Result::ok) {
                if !parse_json {
                    if let Some(app) = &app {
                        let _ = app.emit("bridge://stderr", line);
                    }
                    continue;
                }
                let value: Value = match serde_json::from_str(&line) {
                    Ok(value) => value,
                    Err(_) => continue,
                };
                let frame: RpcFrame = match serde_json::from_value(value.clone()) {
                    Ok(frame) => frame,
                    Err(_) => continue,
                };

                match frame.id {
                    Some(id) => {
                        let reply = match frame.error {
                            Some(err) => Err(format!("engine error {}: {}", err.code, err.message)),
                            None => Ok(frame.result.unwrap_or(Value::Null)),
                        };
                        if let Some(tx) = internal.pending.lock().unwrap().remove(&(id as u64)) {
                            let _ = tx.send(reply);
                        }
                    }
                    None => {
                        if let (Some(app), Some(method)) = (&app, frame.method) {
                            let payload = json!({ "method": method, "params": frame.params });
                            let _ = app.emit("bridge://event", payload);
                        }
                    }
                }
            }
        });
    }

    fn send_line(&self, line: &str) -> Result<(), String> {
        let mut stdin = self.internal.stdin.lock().unwrap();
        let bytes = line.as_bytes();
        stdin
            .write_all(bytes)
            .and_then(|_| stdin.flush())
            .map_err(|e| format!("write to engine stdin failed: {e}"))
    }

    pub fn call(&self, method: &str, params: Option<Value>) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = channel::<Result<Value, String>>();

        {
            let mut pending = self.internal.pending.lock().unwrap();
            pending.insert(id, tx);
        }

        let mut req = serde_json::Map::new();
        req.insert("jsonrpc".into(), "2.0".into());
        req.insert("id".into(), Value::from(id as i64));
        req.insert("method".into(), Value::from(method));
        if let Some(params) = params {
            req.insert("params".into(), params);
        }
        let line = Value::Object(req).to_string();
        self.send_line(&format!("{line}\n"))?;

        let result = rx.recv_timeout(REQUEST_TIMEOUT);
        match result {
            Ok(reply) => reply,
            Err(RecvTimeoutError::Timeout) => {
                self.internal.pending.lock().unwrap().remove(&id);
                Err(format!("engine call '{method}' timed out"))
            }
            Err(RecvTimeoutError::Disconnected) => Err("engine process disconnected".to_string()),
        }
    }

    pub fn is_running(&self) -> bool {
        match self.internal.child.lock().unwrap().try_wait() {
            Ok(None) => true,
            Ok(Some(_)) => false,
            Err(_) => false,
        }
    }

pub fn pid(&self) -> Option<u32> {
        Some(self.internal.child.lock().unwrap().id())
    }

    /// Graceful shutdown: ask the bridge to exit, then reclaim the child.
    pub fn shutdown(&self) -> Result<i32, String> {
        let request = json!({ "jsonrpc": "2.0", "id": Value::Null, "method": "system.shutdown" });
        let _ = self.send_line(&format!("{request}\n"));

        let mut child = self.internal.child.lock().unwrap();
        let deadline = std::time::Instant::now() + SHUTDOWN_GRACE;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => return Ok(status.code().unwrap_or(-1)),
                Ok(None) => {
                    if std::time::Instant::now() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Ok(-1);
                    }
                    std::thread::sleep(Duration::from_millis(25));
                }
                Err(e) => return Err(format!("wait on bridge child failed: {e}")),
            }
        }
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}