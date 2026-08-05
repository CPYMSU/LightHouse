//! LightHouse native code execution sidecar.
//!
//! The JSONL contract is intentionally LightHouse-owned. Codex informed the
//! production requirements (PTY, sandbox policy, lifecycle and streaming), but
//! no Codex protocol or product code is embedded here.

use parking_lot::Mutex;
use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::io::{self, BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use uuid::Uuid;

const PROTOCOL: &str = "lighthouse-code-kernel/v1";
const MAX_CHUNK: usize = 64 * 1024;
const MAX_OUTPUT_BYTES: usize = 2_000_000;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Request {
    id: String,
    method: String,
    #[serde(default)]
    params: Value,
}

#[derive(Debug, Serialize)]
struct Response<'a> {
    id: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<Value>,
}

#[derive(Clone)]
struct Output(Arc<Mutex<io::Stdout>>);

impl Output {
    fn emit(&self, value: Value) {
        let mut stdout = self.0.lock();
        let _ = writeln!(stdout, "{}", value);
        let _ = stdout.flush();
    }

    fn response(&self, id: &str, result: Result<Value, KernelError>) {
        let value = match result {
            Ok(result) => serde_json::to_value(Response {
                id,
                result: Some(result),
                error: None,
            })
            .expect("response serialization"),
            Err(error) => serde_json::to_value(Response {
                id,
                result: None,
                error: Some(json!({"code": error.code(), "message": error.to_string()})),
            })
            .expect("error serialization"),
        };
        self.emit(value);
    }
}

#[derive(Debug)]
enum KernelError {
    Invalid(String),
    Policy(String),
    Io(String),
    NotFound(String),
}

impl KernelError {
    fn code(&self) -> &'static str {
        match self {
            Self::Invalid(_) => "invalid_request",
            Self::Policy(_) => "policy_denied",
            Self::Io(_) => "io_error",
            Self::NotFound(_) => "not_found",
        }
    }
}

impl std::fmt::Display for KernelError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value)
            | Self::Policy(value)
            | Self::Io(value)
            | Self::NotFound(value) => {
                write!(f, "{value}")
            }
        }
    }
}

#[derive(Clone)]
struct ProcessHandle {
    child: Arc<Mutex<Box<dyn Child + Send + Sync>>>,
    writer: Arc<Mutex<Option<Box<dyn Write + Send>>>>,
    master: Arc<Mutex<Box<dyn MasterPty + Send>>>,
    command: Vec<String>,
    started: Instant,
}

type ProcessMap = Arc<Mutex<HashMap<Uuid, ProcessHandle>>>;

struct Kernel {
    output: Output,
    processes: ProcessMap,
}

impl Kernel {
    fn new() -> Self {
        Self {
            output: Output(Arc::new(Mutex::new(io::stdout()))),
            processes: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn handle(&self, request: Request) -> Result<Value, KernelError> {
        match request.method.as_str() {
            "hello" => Ok(json!({
                "protocol": PROTOCOL,
                "version": env!("CARGO_PKG_VERSION"),
                "platform": env::consts::OS,
                "features": ["pty", "streaming", "timeout", "resize", "terminate", "sandbox-policy"]
            })),
            "process/spawn" => self.spawn(request.params),
            "process/write" => self.write(request.params),
            "process/resize" => self.resize(request.params),
            "process/terminate" => self.terminate(request.params),
            "process/list" => self.list(),
            _ => Err(KernelError::Invalid(format!(
                "unsupported method: {}",
                request.method
            ))),
        }
    }

    fn spawn(&self, params: Value) -> Result<Value, KernelError> {
        let object = params
            .as_object()
            .ok_or_else(|| KernelError::Invalid("params must be an object".into()))?;
        let command = string_array(object.get("command"), "command")?;
        if command.is_empty() {
            return Err(KernelError::Invalid("command must not be empty".into()));
        }
        let cwd = canonical_directory(object.get("cwd"))?;
        let policy = object
            .get("sandboxPolicy")
            .and_then(Value::as_str)
            .unwrap_or("workspaceWrite");
        let writable_roots = string_array_optional(object.get("writableRoots"))?;
        let network_access = object
            .get("networkAccess")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let timeout_ms = object
            .get("timeoutMs")
            .and_then(Value::as_u64)
            .unwrap_or(600_000)
            .clamp(1_000, 3_600_000);
        let rows = object.get("rows").and_then(Value::as_u64).unwrap_or(24) as u16;
        let cols = object.get("cols").and_then(Value::as_u64).unwrap_or(120) as u16;
        let wrapped = sandbox_command(&command, &cwd, policy, &writable_roots, network_access)?;

        let pty_system = native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| KernelError::Io(error.to_string()))?;
        let mut builder = CommandBuilder::new(&wrapped[0]);
        for arg in wrapped.iter().skip(1) {
            builder.arg(arg);
        }
        builder.cwd(&cwd);
        let mut environment = object
            .get("env")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        environment.insert("TERM".into(), Value::String("xterm-256color".into()));
        for (key, value) in environment {
            if let Some(value) = value.as_str() {
                builder.env(key, value);
            }
        }
        let child = pair
            .slave
            .spawn_command(builder)
            .map_err(|error| KernelError::Io(error.to_string()))?;
        drop(pair.slave);
        let reader = pair
            .master
            .try_clone_reader()
            .map_err(|error| KernelError::Io(error.to_string()))?;
        let writer = pair
            .master
            .take_writer()
            .map_err(|error| KernelError::Io(error.to_string()))?;
        let process_id = Uuid::new_v4();
        let handle = ProcessHandle {
            child: Arc::new(Mutex::new(child)),
            writer: Arc::new(Mutex::new(Some(writer))),
            master: Arc::new(Mutex::new(pair.master)),
            command: command.clone(),
            started: Instant::now(),
        };
        self.processes.lock().insert(process_id, handle.clone());
        spawn_output_reader(process_id, reader, self.output.clone());
        spawn_process_monitor(
            process_id,
            handle,
            self.processes.clone(),
            self.output.clone(),
            Duration::from_millis(timeout_ms),
        );
        Ok(json!({
            "processId": process_id,
            "command": command,
            "effectiveCommand": wrapped,
            "cwd": cwd,
            "sandboxPolicy": policy,
            "pty": true,
            "timeoutMs": timeout_ms
        }))
    }

    fn write(&self, params: Value) -> Result<Value, KernelError> {
        let process_id = process_id(&params)?;
        let data = params
            .get("data")
            .and_then(Value::as_str)
            .ok_or_else(|| KernelError::Invalid("data must be a string".into()))?;
        let close = params
            .get("close")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let handle = self
            .processes
            .lock()
            .get(&process_id)
            .cloned()
            .ok_or_else(|| KernelError::NotFound("process not found".into()))?;
        let mut writer = handle.writer.lock();
        let stream = writer
            .as_mut()
            .ok_or_else(|| KernelError::NotFound("process stdin is closed".into()))?;
        stream
            .write_all(data.as_bytes())
            .and_then(|_| stream.flush())
            .map_err(|error| KernelError::Io(error.to_string()))?;
        if close {
            writer.take();
        }
        Ok(json!({"processId": process_id, "writtenBytes": data.len(), "closed": close}))
    }

    fn resize(&self, params: Value) -> Result<Value, KernelError> {
        let process_id = process_id(&params)?;
        let rows = params.get("rows").and_then(Value::as_u64).unwrap_or(24) as u16;
        let cols = params.get("cols").and_then(Value::as_u64).unwrap_or(120) as u16;
        let handle = self
            .processes
            .lock()
            .get(&process_id)
            .cloned()
            .ok_or_else(|| KernelError::NotFound("process not found".into()))?;
        handle
            .master
            .lock()
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| KernelError::Io(error.to_string()))?;
        Ok(json!({"processId": process_id, "rows": rows, "cols": cols}))
    }

    fn terminate(&self, params: Value) -> Result<Value, KernelError> {
        let process_id = process_id(&params)?;
        let handle = self
            .processes
            .lock()
            .get(&process_id)
            .cloned()
            .ok_or_else(|| KernelError::NotFound("process not found".into()))?;
        handle
            .child
            .lock()
            .kill()
            .map_err(|error| KernelError::Io(error.to_string()))?;
        Ok(json!({"processId": process_id, "terminated": true}))
    }

    fn list(&self) -> Result<Value, KernelError> {
        let items: Vec<Value> = self
            .processes
            .lock()
            .iter()
            .map(|(id, handle)| {
                json!({
                    "processId": id,
                    "command": handle.command,
                    "elapsedMs": handle.started.elapsed().as_millis()
                })
            })
            .collect();
        Ok(json!({"processes": items}))
    }
}

fn string_array(value: Option<&Value>, field: &str) -> Result<Vec<String>, KernelError> {
    value
        .and_then(Value::as_array)
        .ok_or_else(|| KernelError::Invalid(format!("{field} must be an array")))?
        .iter()
        .map(|item| {
            item.as_str()
                .filter(|value| !value.is_empty() && !value.contains('\0'))
                .map(str::to_owned)
                .ok_or_else(|| KernelError::Invalid(format!("{field} contains an invalid item")))
        })
        .collect()
}

fn string_array_optional(value: Option<&Value>) -> Result<Vec<String>, KernelError> {
    match value {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(value) => string_array(Some(value), "writableRoots"),
    }
}

fn canonical_directory(value: Option<&Value>) -> Result<PathBuf, KernelError> {
    let raw = value
        .and_then(Value::as_str)
        .ok_or_else(|| KernelError::Invalid("cwd must be an absolute directory".into()))?;
    let path = Path::new(raw);
    if !path.is_absolute() || raw.contains('\0') {
        return Err(KernelError::Invalid(
            "cwd must be an absolute directory".into(),
        ));
    }
    path.canonicalize()
        .map_err(|error| KernelError::Io(format!("cannot resolve cwd: {error}")))
}

fn process_id(params: &Value) -> Result<Uuid, KernelError> {
    params
        .get("processId")
        .and_then(Value::as_str)
        .ok_or_else(|| KernelError::Invalid("processId is required".into()))?
        .parse()
        .map_err(|_| KernelError::Invalid("processId must be a UUID".into()))
}

fn command_exists(name: &str) -> bool {
    let candidate = Path::new(name);
    if candidate.components().count() > 1 {
        return candidate.is_file();
    }
    env::var_os("PATH")
        .map(|paths| env::split_paths(&paths).any(|path| path.join(name).is_file()))
        .unwrap_or(false)
}

fn sandbox_command(
    command: &[String],
    cwd: &Path,
    policy: &str,
    writable_roots: &[String],
    network_access: bool,
) -> Result<Vec<String>, KernelError> {
    match policy {
        "dangerFullAccess" => Ok(command.to_vec()),
        "readOnly" | "workspaceWrite" => {
            #[cfg(target_os = "linux")]
            {
                if !command_exists("bwrap") {
                    return Err(KernelError::Policy(
                        "bubblewrap is required for Linux sandboxed execution".into(),
                    ));
                }
                let mut value = vec![
                    "bwrap".into(),
                    "--die-with-parent".into(),
                    "--new-session".into(),
                    "--proc".into(),
                    "/proc".into(),
                    "--dev".into(),
                    "/dev".into(),
                    "--ro-bind".into(),
                    "/".into(),
                    "/".into(),
                    "--chdir".into(),
                    cwd.to_string_lossy().into_owned(),
                ];
                if !network_access {
                    value.push("--unshare-net".into());
                }
                if policy == "workspaceWrite" {
                    let roots: Vec<String> = if writable_roots.is_empty() {
                        vec![cwd.to_string_lossy().into_owned()]
                    } else {
                        writable_roots.to_vec()
                    };
                    for root in roots {
                        let path = Path::new(&root);
                        if !path.is_absolute() {
                            return Err(KernelError::Policy(
                                "writable roots must be absolute".into(),
                            ));
                        }
                        value.extend(["--bind".into(), root.clone(), root]);
                    }
                }
                value.push("--".into());
                value.extend(command.iter().cloned());
                Ok(value)
            }
            #[cfg(target_os = "macos")]
            {
                if !command_exists("sandbox-exec") {
                    return Err(KernelError::Policy(
                        "sandbox-exec is required for macOS sandboxed execution".into(),
                    ));
                }
                let mut profile =
                    String::from("(version 1)(deny default)(allow process*)(allow file-read*)");
                if network_access {
                    profile.push_str("(allow network*)");
                }
                if policy == "workspaceWrite" {
                    let roots: Vec<String> = if writable_roots.is_empty() {
                        vec![cwd.to_string_lossy().into_owned()]
                    } else {
                        writable_roots.to_vec()
                    };
                    for root in roots {
                        let escaped = root.replace('\\', "\\\\").replace('"', "\\\"");
                        profile.push_str(&format!("(allow file-write* (subpath \"{}\"))", escaped));
                    }
                }
                let mut value = vec!["sandbox-exec".into(), "-p".into(), profile, "--".into()];
                value.extend(command.iter().cloned());
                Ok(value)
            }
            #[cfg(target_os = "windows")]
            {
                Err(KernelError::Policy(
                    "Windows Restricted Token sandbox adapter is not available in this build"
                        .into(),
                ))
            }
            #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
            {
                Err(KernelError::Policy("unsupported sandbox platform".into()))
            }
        }
        "externalSandbox" => Err(KernelError::Policy(
            "external sandbox must be supplied by the calling environment".into(),
        )),
        _ => Err(KernelError::Invalid(format!(
            "unsupported sandboxPolicy: {policy}"
        ))),
    }
}

fn spawn_output_reader(process_id: Uuid, mut reader: Box<dyn Read + Send>, output: Output) {
    thread::spawn(move || {
        let mut total = 0usize;
        let mut buffer = vec![0u8; MAX_CHUNK];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) => break,
                Ok(count) => {
                    if total >= MAX_OUTPUT_BYTES {
                        continue;
                    }
                    let accepted = count.min(MAX_OUTPUT_BYTES - total);
                    total += accepted;
                    output.emit(json!({
                        "method": "process/output",
                        "params": {
                            "processId": process_id,
                            "stream": "pty",
                            "data": String::from_utf8_lossy(&buffer[..accepted]),
                            "totalBytes": total,
                            "truncated": total >= MAX_OUTPUT_BYTES
                        }
                    }));
                }
                Err(error) => {
                    output.emit(json!({
                        "method": "process/outputError",
                        "params": {"processId": process_id, "error": error.to_string()}
                    }));
                    break;
                }
            }
        }
    });
}

fn spawn_process_monitor(
    process_id: Uuid,
    handle: ProcessHandle,
    processes: ProcessMap,
    output: Output,
    timeout: Duration,
) {
    thread::spawn(move || loop {
        if handle.started.elapsed() >= timeout {
            let _ = handle.child.lock().kill();
            output.emit(json!({
                "method": "process/exited",
                "params": {"processId": process_id, "status": "timedOut", "elapsedMs": handle.started.elapsed().as_millis()}
            }));
            processes.lock().remove(&process_id);
            break;
        }
        match handle.child.lock().try_wait() {
            Ok(Some(status)) => {
                output.emit(json!({
                    "method": "process/exited",
                    "params": {
                        "processId": process_id,
                        "status": "exited",
                        "exitCode": status.exit_code(),
                        "elapsedMs": handle.started.elapsed().as_millis()
                    }
                }));
                processes.lock().remove(&process_id);
                break;
            }
            Ok(None) => thread::sleep(Duration::from_millis(50)),
            Err(error) => {
                output.emit(json!({
                    "method": "process/exited",
                    "params": {"processId": process_id, "status": "unknown", "error": error.to_string()}
                }));
                processes.lock().remove(&process_id);
                break;
            }
        }
    });
}

fn main() {
    let kernel = Kernel::new();
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(value) => value,
            Err(error) => {
                kernel
                    .output
                    .emit(json!({"error": {"code": "io_error", "message": error.to_string()}}));
                break;
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<Request>(&line) {
            Ok(request) => {
                let id = request.id.clone();
                kernel.output.response(&id, kernel.handle(request));
            }
            Err(error) => kernel.output.emit(json!({
                "id": Value::Null,
                "error": {"code": "invalid_json", "message": error.to_string()}
            })),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn danger_mode_preserves_command() {
        let command = vec!["echo".into(), "ok".into()];
        assert_eq!(
            sandbox_command(&command, Path::new("/tmp"), "dangerFullAccess", &[], false).unwrap(),
            command
        );
    }

    #[test]
    fn invalid_policy_fails_closed() {
        let command = vec!["echo".into()];
        assert!(sandbox_command(&command, Path::new("/tmp"), "unknown", &[], false).is_err());
    }
}
