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
    next_id: AtomicU64,
}

#[derive(Clone)]
pub struct Engine {
    internal: Arc<EngineInternal>,
    app: Option<AppHandle>,
}

use std::path::{Path, PathBuf};

fn find_project_root() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(cur) = std::env::current_dir() {
        candidates.push(cur);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            candidates.push(parent.to_path_buf());
        }
    }

    for start_dir in candidates {
        let mut dir = start_dir.as_path();
        for _ in 0..10 {
            if dir.join("autopilot").join("bridge").join("__main__.py").is_file() {
                return Some(dir.to_path_buf());
            }
            if dir.join("autopilot").join("autopilot").join("bridge").join("__main__.py").is_file() {
                return Some(dir.join("autopilot"));
            }
            match dir.parent() {
                Some(p) => dir = p,
                None => break,
            }
        }
    }
    None
}

fn resolve_python(root: Option<&Path>) -> String {
    if let Ok(custom) = std::env::var("AUTOPILOT_PYTHON") {
        if !custom.trim().is_empty() {
            return custom;
        }
    }

    if let Some(r) = root {
        #[cfg(target_os = "windows")]
        let venvs = [
            r.join(".venv").join("Scripts").join("python.exe"),
            r.join("venv").join("Scripts").join("python.exe"),
        ];
        #[cfg(not(target_os = "windows"))]
        let venvs = [
            r.join(".venv").join("bin").join("python"),
            r.join("venv").join("bin").join("python"),
        ];

        for venv in &venvs {
            if venv.is_file() {
                return venv.to_string_lossy().to_string();
            }
        }
    }

    #[cfg(target_os = "windows")]
    {
        let common_paths = [
            r"C:\Python314\python.exe",
            r"C:\Python313\python.exe",
            r"C:\Python312\python.exe",
            r"C:\Python311\python.exe",
            r"C:\Python310\python.exe",
        ];
        for p in &common_paths {
            if Path::new(p).is_file() {
                return (*p).to_string();
            }
        }
    }

    "python".to_string()
}

impl Engine {
    pub fn spawn(app: Option<AppHandle>) -> Result<Self, String> {
        let project_root = find_project_root();
        let python_cmd = resolve_python(project_root.as_deref());

        let mut cmd = Command::new(&python_cmd);
        cmd.arg("-u").arg("-m").arg("autopilot.bridge");

        if let Some(ref root) = project_root {
            cmd.current_dir(root);
            let pythonpath = match std::env::var("PYTHONPATH") {
                Ok(existing) if !existing.is_empty() => {
                    #[cfg(target_os = "windows")]
                    {
                        format!("{};{}", root.display(), existing)
                    }
                    #[cfg(not(target_os = "windows"))]
                    {
                        format!("{}:{}", root.display(), existing)
                    }
                }
                _ => root.display().to_string(),
            };
            cmd.env("PYTHONPATH", pythonpath);
        }

        if let Ok(db) = std::env::var("AUTOPILOT_DB_PATH") {
            cmd.env("AUTOPILOT_DB_PATH", db);
        }
        if let Ok(artifacts) = std::env::var("AUTOPILOT_ARTIFACTS_DIR") {
            cmd.env("AUTOPILOT_ARTIFACTS_DIR", artifacts);
        }
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
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
            next_id: AtomicU64::new(0),
        });

        let engine = Engine {
            internal,
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
            if parse_json {
                let mut pending = internal.pending.lock().unwrap();
                for (_, tx) in pending.drain() {
                    let _ = tx.send(Err("engine process disconnected unexpectedly".to_string()));
                }
                if let Some(app) = &app {
                    let _ = app.emit("bridge://disconnect", json!({ "state": "disconnected" }));
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
        let id = self.internal.next_id.fetch_add(1, Ordering::SeqCst);
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

impl Drop for EngineInternal {
    fn drop(&mut self) {
        let request = json!({ "jsonrpc": "2.0", "id": Value::Null, "method": "system.shutdown" });
        if let Ok(mut stdin) = self.stdin.lock() {
            let line = format!("{request}\n");
            let _ = stdin.write_all(line.as_bytes()).and_then(|_| stdin.flush());
        }

        if let Ok(mut child) = self.child.lock() {
            let deadline = std::time::Instant::now() + SHUTDOWN_GRACE;
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) => {
                        if std::time::Instant::now() >= deadline {
                            let _ = child.kill();
                            let _ = child.wait();
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(25));
                    }
                    Err(_) => break,
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rpc_frame_success_deserialization() {
        let raw = r#"{"jsonrpc":"2.0","id":42,"result":{"status":"ok"}}"#;
        let frame: RpcFrame = serde_json::from_str(raw).expect("valid frame");
        assert_eq!(frame.id, Some(42));
        assert!(frame.error.is_none());
        assert_eq!(frame.result, Some(json!({"status": "ok"})));
    }

    #[test]
    fn test_rpc_frame_error_deserialization() {
        let raw = r#"{"jsonrpc":"2.0","id":99,"error":{"code":-32600,"message":"Invalid Request"}}"#;
        let frame: RpcFrame = serde_json::from_str(raw).expect("valid error frame");
        assert_eq!(frame.id, Some(99));
        assert!(frame.result.is_none());
        let err = frame.error.expect("error present");
        assert_eq!(err.code, -32600);
        assert_eq!(err.message, "Invalid Request");
    }

    #[test]
    fn test_rpc_frame_event_deserialization() {
        let raw = r#"{"jsonrpc":"2.0","method":"job.progress","params":{"job_id":"j1","pct":50}}"#;
        let frame: RpcFrame = serde_json::from_str(raw).expect("valid event frame");
        assert_eq!(frame.id, None);
        assert_eq!(frame.method, Some("job.progress".to_string()));
        assert_eq!(frame.params, Some(json!({"job_id":"j1","pct":50})));
    }

    #[test]
    fn test_concurrent_pending_routing() {
        let pending = Arc::new(Mutex::new(HashMap::new()));
        let mut rxs = Vec::new();

        for id in 0..10u64 {
            let (tx, rx) = channel::<Result<Value, String>>();
            pending.lock().unwrap().insert(id, tx);
            rxs.push((id, rx));
        }

        // Fulfill in reverse order to ensure non-blocking multiplexing
        for (id, _) in rxs.iter().rev() {
            if let Some(tx) = pending.lock().unwrap().remove(id) {
                let _ = tx.send(Ok(json!({ "id": id })));
            }
        }

        for (id, rx) in rxs {
            let res = rx.recv_timeout(Duration::from_millis(100)).expect("received");
            assert_eq!(res, Ok(json!({ "id": id })));
        }
    }

    #[test]
    fn test_find_project_root_locates_autopilot() {
        let root = find_project_root();
        assert!(root.is_some(), "should find project root");
        let path = root.unwrap();
        assert!(path.join("autopilot").join("bridge").join("__main__.py").is_file());
    }

    #[test]
    fn test_resolve_python_prefers_custom_env() {
        std::env::set_var("AUTOPILOT_PYTHON", "my_custom_python_executable");
        let resolved = resolve_python(None);
        std::env::remove_var("AUTOPILOT_PYTHON");
        assert_eq!(resolved, "my_custom_python_executable");
    }

    #[test]
    fn test_engine_clone_drop_safety() {
        let mut child = Command::new("cmd")
            .arg("/c")
            .arg("exit 0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn dummy");
        let stdin = child.stdin.take().expect("stdin available");
        let internal = Arc::new(EngineInternal {
            child: Mutex::new(child),
            stdin: Mutex::new(stdin),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(0),
        });
        let engine = Engine {
            internal: Arc::clone(&internal),
            app: None,
        };
        assert_eq!(Arc::strong_count(&internal), 2);
        {
            let clone = engine.clone();
            assert_eq!(Arc::strong_count(&internal), 3);
            drop(clone);
        }
        assert_eq!(Arc::strong_count(&internal), 2);
    }
}