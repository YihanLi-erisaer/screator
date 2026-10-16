#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

#[cfg(target_os = "macos")]
mod macos_install;
#[cfg(target_os = "macos")]
mod macos_uninstall;

use serde_json::{json, Value};
use std::{collections::HashMap, fs::OpenOptions, io::{BufRead, BufReader, Write}, path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio}, sync::{Arc, Mutex, OnceLock, atomic::{AtomicBool, AtomicU64, Ordering}}, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
use tauri::{Emitter, Manager, State};
use tokio::sync::{oneshot, watch};

type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Value, String>>>>>;
static SHELL_LOG: OnceLock<PathBuf> = OnceLock::new();
static EXPECTED_EXIT: AtomicBool = AtomicBool::new(false);
static FRONTEND_READY_LOGGED: AtomicBool = AtomicBool::new(false);

fn log_shell(message: &str) {
    let Some(path) = SHELL_LOG.get() else { return };
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{stamp} {message}");
    }
}
struct Worker {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Pending,
    next: AtomicU64,
    #[cfg(windows)]
    job: Mutex<Option<usize>>,
}

#[derive(Clone)]
enum WorkerStatus {
    Starting,
    Ready(Arc<Worker>),
    Failed(String),
    Stopped,
}

#[derive(Clone)]
struct WorkerManager {
    status: Arc<watch::Sender<WorkerStatus>>,
    closing: Arc<Mutex<bool>>,
    startup: Arc<Mutex<Option<std::thread::JoinHandle<()>>>>,
}

impl WorkerManager {
    fn new() -> Self {
        let (status, _) = watch::channel(WorkerStatus::Starting);
        Self { status: Arc::new(status), closing: Arc::new(Mutex::new(false)),
            startup: Arc::new(Mutex::new(None)) }
    }

    fn publish(&self, result: Result<Worker, String>) {
        let closing = self.closing.lock().unwrap();
        if *closing {
            drop(closing);
            if let Ok(worker) = result {
                force_stop_worker(take_worker_process(&worker));
            }
            return;
        }
        let status = match result {
            Ok(worker) => WorkerStatus::Ready(Arc::new(worker)),
            Err(error) => WorkerStatus::Failed(error),
        };
        self.status.send_replace(status);
    }

    async fn get(&self) -> Result<Arc<Worker>, String> {
        let mut receiver = self.status.subscribe();
        let wait = async {
            loop {
                let current = receiver.borrow_and_update().clone();
                match current {
                    WorkerStatus::Ready(worker) => return Ok(worker),
                    WorkerStatus::Failed(error) => return Err(format!("后台启动失败：{error}")),
                    WorkerStatus::Stopped => return Err("后台已停止，请重新启动应用。".into()),
                    WorkerStatus::Starting => receiver.changed().await
                        .map_err(|_| "后台启动状态已断开。".to_string())?,
                }
            }
        };
        tokio::time::timeout(Duration::from_secs(60), wait).await
            .map_err(|_| "后台启动超过 60 秒，请检查日志后重新启动应用。".to_string())?
    }

    fn stop(&self) -> Option<WorkerProcess> {
        let mut closing = self.closing.lock().unwrap();
        *closing = true;
        match self.status.send_replace(WorkerStatus::Stopped) {
            WorkerStatus::Ready(worker) => Some(take_worker_process(&worker)),
            _ => None,
        }
    }

    fn set_startup_thread(&self, thread: std::thread::JoinHandle<()>) {
        *self.startup.lock().unwrap() = Some(thread);
    }

    fn join_startup(&self) {
        if let Some(thread) = self.startup.lock().unwrap().take() {
            let _ = thread.join();
        }
    }
}

struct WorkerProcess {
    child: Option<Child>,
    #[cfg(windows)]
    job: Option<usize>,
}

async fn request_worker(worker: &Worker, method: String, params: Value) -> Result<Value, String> {
    if method.len() > 100 || !params.is_object() { return Err("无效请求".into()); }
    let id = worker.next.fetch_add(1, Ordering::Relaxed).to_string();
    let (tx, rx) = oneshot::channel();
    worker.pending.lock().unwrap().insert(id.clone(), tx);
    let timeout = match method.as_str() {
        "system.finish_shutdown" => Duration::from_secs(3),
        "system.prepare_shutdown" | "system.shutdown_status" => Duration::from_secs(5),
        _ => Duration::from_secs(90),
    };
    let message = json!({"protocol_version":2,"request_id":id,"method":method,"params":params}).to_string();
    if message.len() > 1_000_000 {
        worker.pending.lock().unwrap().remove(&id);
        return Err("请求超过大小限制".into());
    }
    let result = {
        let mut input = worker.stdin.lock().unwrap();
        match input.as_mut() {
            Some(pipe) => writeln!(pipe, "{}", message).and_then(|_| pipe.flush()).map_err(|e| e.to_string()),
            None => Err("后台已停止，请重新启动应用。".into()),
        }
    };
    if let Err(error) = result { worker.pending.lock().unwrap().remove(&id); return Err(error); }
    match tokio::time::timeout(timeout, rx).await {
        Ok(Ok(value)) => value,
        _ => { worker.pending.lock().unwrap().remove(&id); Err("后台响应超时；请刷新状态后再操作，勿重复投稿。".into()) }
    }
}

#[tauri::command]
async fn backend_request(manager: State<'_, WorkerManager>, method: String, params: Value) -> Result<Value, String> {
    let health = method == "system.health";
    if health { log_shell("frontend health request"); }
    let worker = manager.get().await?;
    let result = request_worker(&worker, method, params).await;
    if health { log_shell(if result.is_ok() { "frontend health response" } else { "frontend health failed" }); }
    result
}

fn take_worker_process(worker: &Worker) -> WorkerProcess {
    worker.stdin.lock().unwrap().take();
    WorkerProcess {
        child: worker.child.lock().unwrap().take(),
        #[cfg(windows)]
        job: worker.job.lock().unwrap().take(),
    }
}

fn reap_worker(mut process: WorkerProcess, deadline: Instant) {
    if let Some(mut child) = process.child.take() {
        loop {
            if child.try_wait().ok().flatten().is_some() {
                kill_worker_group(&child);
                return close_worker_job(process);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() { break; }
            std::thread::sleep(remaining.min(Duration::from_millis(20)));
        }
        kill_worker_group(&child);
        let _ = child.kill();
        let _ = child.wait();
    }
    close_worker_job(process);
}

fn force_stop_worker(mut process: WorkerProcess) {
    // Exit callbacks run on the UI event thread. Never wait for Python here:
    // closing the Windows job handle terminates its whole process tree.
    #[cfg(windows)]
    if let Some(job) = process.job.take() {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(job as _); }
    }
    if let Some(mut child) = process.child.take() {
        kill_worker_group(&child);
        let _ = child.kill();
    }
}

#[cfg(unix)]
fn kill_worker_group(child: &Child) {
    // The worker is a process-group leader; stop its FFmpeg and Ollama children too.
    unsafe { libc::killpg(child.id() as libc::pid_t, libc::SIGKILL); }
}

#[cfg(windows)]
fn kill_worker_group(_: &Child) {}

#[cfg(windows)]
fn close_worker_job(mut process: WorkerProcess) {
    if let Some(job) = process.job.take() {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(job as _); }
    }
}

#[cfg(not(windows))]
fn close_worker_job(_: WorkerProcess) {}

#[tauri::command]
async fn finish_close(app: tauri::AppHandle, manager: State<'_, WorkerManager>) -> Result<(), String> {
    let worker = manager.get().await?;
    let status = request_worker(&worker, "system.shutdown_status".into(), json!({})).await?;
    if status["ready"] != true { return Err("后台仍在收尾，请等待上传完成。".into()); }
    log_shell("confirmed shutdown: worker is ready");
    let started = Instant::now();
    let deadline = started + Duration::from_secs(3);
    match request_worker(&worker, "system.finish_shutdown".into(), json!({})).await {
        Ok(_) => log_shell("shutdown resources finalized"),
        Err(error) => log_shell(&format!("shutdown finalization fallback: {error}")),
    }
    EXPECTED_EXIT.store(true, Ordering::Release);
    let process = manager.stop().ok_or("后台已停止，请重新启动应用。")?;
    tauri::async_runtime::spawn_blocking(move || reap_worker(process, deadline)).await
        .map_err(|_| "后台退出任务未完成，请重试。".to_string())?;
    log_shell(&format!("shutdown reap elapsed_ms={}", started.elapsed().as_millis()));
    record_shutdown_smoke("graceful", started.elapsed());
    app.exit(0);
    Ok(())
}

#[tauri::command]
async fn force_close(app: tauri::AppHandle, manager: State<'_, WorkerManager>) -> Result<(), String> {
    let started = Instant::now();
    log_shell("forced shutdown: stopping worker process tree; in-flight submissions need review");
    if let Some(process) = manager.stop() {
        tauri::async_runtime::spawn_blocking(move || force_stop_worker(process)).await
            .map_err(|_| "无法停止后台进程，请重试。".to_string())?;
    }
    EXPECTED_EXIT.store(true, Ordering::Release);
    record_shutdown_smoke("forced", started.elapsed());
    app.exit(0);
    Ok(())
}

#[tauri::command]
fn frontend_ready(app: tauri::AppHandle, health: Value) {
    if !FRONTEND_READY_LOGGED.swap(true, Ordering::AcqRel) {
        log_shell("frontend health ready");
    }
    if smoke_enabled() {
        if let Some(report) = std::env::var_os("SCREATOR_NATIVE_SMOKE_REPORT") {
            let value = json!({"ok":health["protocol_version"] == 2,"webview_loaded":true,
                "frontend_ipc":true,"protocol_version":health["protocol_version"]});
            let _ = std::fs::write(report, value.to_string());
            if let Ok(mode) = std::env::var("SCREATOR_NATIVE_SMOKE_SHUTDOWN") {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    let manager = handle.state::<WorkerManager>();
                    let result = if mode == "graceful" {
                        match manager.get().await {
                            Ok(worker) => match request_worker(&worker, "system.prepare_shutdown".into(), json!({})).await {
                                Ok(_) => finish_close(handle.clone(), manager).await,
                                Err(error) => Err(error),
                            },
                            Err(error) => Err(error),
                        }
                    } else if mode == "forced" {
                        force_close(handle.clone(), manager).await
                    } else { Err("无效退出测试模式".into()) };
                    if let Err(error) = result {
                        log_shell(&format!("shutdown smoke failed: {error}"));
                        handle.exit(1);
                    }
                });
                return;
            }
            if let Some(seconds) = smoke_inspect_seconds() {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    tokio::time::sleep(Duration::from_secs(seconds)).await;
                    EXPECTED_EXIT.store(true, Ordering::Release);
                    handle.exit(0);
                });
                return;
            }
            EXPECTED_EXIT.store(true, Ordering::Release);
            app.exit(0);
        }
    }
}

fn record_shutdown_smoke(mode: &str, elapsed: Duration) {
    if !smoke_enabled() { return; }
    if std::env::var("SCREATOR_NATIVE_SMOKE_SHUTDOWN").ok().as_deref() != Some(mode) { return; }
    if let Some(report) = std::env::var_os("SCREATOR_NATIVE_SMOKE_REPORT") {
        if let Ok(data) = std::fs::read_to_string(&report) {
            if let Ok(mut value) = serde_json::from_str::<Value>(&data) {
                value["shutdown"] = json!({"mode":mode,"completed":true,"elapsed_ms":elapsed.as_millis()});
                let _ = std::fs::write(report, value.to_string());
            }
        }
    }
}

fn smoke_enabled() -> bool {
    std::env::var_os("SCREATOR_NATIVE_SMOKE_REPORT").is_some()
        && std::env::var_os("SCREATOR_DESKTOP_DATA").is_some()
        && (cfg!(debug_assertions) || std::env::args().any(|arg| arg == "--smoke-test"))
}

fn smoke_inspect_seconds() -> Option<u64> {
    if !smoke_enabled() || std::env::var_os("SCREATOR_NATIVE_SMOKE_SHUTDOWN").is_some() {
        return None;
    }
    std::env::var("SCREATOR_NATIVE_SMOKE_INSPECT_SECONDS").ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|seconds| (1..=120).contains(seconds))
}

#[cfg(windows)]
fn attach_job(child: &Child) -> Result<usize, String> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::*;
    use windows_sys::Win32::Foundation::CloseHandle;
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() { return Err("无法创建后台进程组".into()); }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info as *const _ as *const _, std::mem::size_of_val(&info) as u32) == 0
            || AssignProcessToJobObject(job, child.as_raw_handle() as _) == 0 {
            CloseHandle(job);
            return Err("无法保护后台进程生命周期".into());
        }
        Ok(job as usize)
    }
}

fn start_worker(app: &tauri::AppHandle) -> Result<Worker, Box<dyn std::error::Error>> {
    let project = std::env::var_os("SCREATOR_PROJECT_ROOT").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().to_path_buf());
    let data = if let Some(override_dir) = std::env::var_os("SCREATOR_DESKTOP_DATA") {
        PathBuf::from(override_dir)
    } else {
        let data_parent = app.path().local_data_dir()?.join("StarDazz");
        let current_data = data_parent.join("screator");
        let legacy_data = data_parent.join("yt2bili");
        if !current_data.exists() && legacy_data.exists() { legacy_data } else { current_data }
    };
    let log_dir = data.join("logs");
    std::fs::create_dir_all(&log_dir)?;
    let _ = SHELL_LOG.set(log_dir.join("desktop-shell.log"));
    log_shell("desktop starting");
    let resources = if cfg!(debug_assertions) {
        std::env::var_os("SCREATOR_RESOURCES").map(PathBuf::from).unwrap_or_else(|| project.clone())
    } else {
        app.path().resource_dir()?
    };
    let mut command = if !cfg!(debug_assertions) {
        Command::new(resources.join("worker").join(if cfg!(windows) { "screator-worker.exe" } else { "screator-worker" }))
    } else if let Some(frozen) = std::env::var_os("SCREATOR_WORKER") {
        Command::new(frozen)
    } else {
        let python = std::env::var_os("SCREATOR_PYTHON").map(PathBuf::from).unwrap_or_else(|| {
            project.join(if cfg!(windows) { ".desktop-venv/Scripts/python.exe" } else { ".desktop-venv/bin/python" })
        });
        let mut cmd = Command::new(python);
        cmd.args(["-u", "-m", "screator.desktop_worker"]);
        cmd
    };
    command.arg("--data-dir").arg(data).arg("--resources").arg(&resources)
        .current_dir(if cfg!(debug_assertions) { &project } else { &resources }).env("PYTHONIOENCODING", "utf-8")
        .env("SCREATOR_APP_PID", std::process::id().to_string())
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    #[cfg(unix)] {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    if smoke_enabled() {
        if let Some(milliseconds) = std::env::var("SCREATOR_NATIVE_SMOKE_SPAWN_DELAY_MS").ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| (1..=15_000).contains(value)) {
            log_shell(&format!("smoke worker spawn delay_ms={milliseconds}"));
            std::thread::sleep(Duration::from_millis(milliseconds));
        }
    }
    log_shell("worker spawn begin");
    let spawn_started = Instant::now();
    let mut child = command.spawn()?;
    log_shell(&format!("worker spawned pid={} spawn_ms={}", child.id(), spawn_started.elapsed().as_millis()));
    #[cfg(windows)]
    let job = match attach_job(&child) { Ok(job) => job, Err(error) => { let _ = child.kill(); return Err(error.into()); } };
    let stdin = child.stdin.take();
    let stdout = child.stdout.take().ok_or("worker stdout missing")?;
    let stderr = child.stderr.take().ok_or("worker stderr missing")?;
    // Drain diagnostic output; protocol logs are already sanitized by Python.
    std::thread::spawn(move || { for line in BufReader::new(stderr).lines() { if line.is_err() { break; } } });
    let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
    let replies = pending.clone();
    let handle = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            let Ok(value) = serde_json::from_str::<Value>(&line) else { continue };
            if value.get("event").and_then(Value::as_str) == Some("worker.ready") {
                log_shell("worker ready");
            }
            if let Some(id) = value.get("request_id").and_then(Value::as_str) {
                if let Some(sender) = replies.lock().unwrap().remove(id) {
                    let answer = if let Some(error) = value.get("error") {
                        Err(error.get("message").and_then(Value::as_str).unwrap_or("后台操作失败").to_owned())
                    } else { Ok(value["result"].clone()) };
                    let _ = sender.send(answer);
                }
            } else if value.get("event").and_then(Value::as_str) == Some("task.log") {
                // The UI reads logs through logs.tail. Forwarding every log line
                // also sends high-volume validation output into the WebView.
            } else if value.get("event").is_some() {
                let _ = handle.emit("backend-event", value);
            }
        }
        log_shell("worker stdout closed");
        for (_, sender) in replies.lock().unwrap().drain() { let _ = sender.send(Err("后台进程已停止，请重新启动应用。".into())); }
        let _ = handle.emit("backend-disconnected", ());
    });
    Ok(Worker { child: Mutex::new(Some(child)), stdin: Mutex::new(stdin), pending, next: AtomicU64::new(1),
        #[cfg(windows)] job: Mutex::new(Some(job)) })
}

fn main() {
    let context = tauri::generate_context!();
    #[cfg(target_os = "macos")]
    let context = match macos_uninstall::handle_uninstall_launch(context) {
        Some(context) => context,
        None => return,
    };
    // Installation must precede the single-instance plugin: an installed copy
    // may already be running when the user opens a new DMG.
    #[cfg(target_os = "macos")]
    let context = if smoke_enabled() { context } else {
        match macos_install::handle_disk_image_launch(context) {
            Some(context) => context,
            None => return,
        }
    };
    std::panic::set_hook(Box::new(|info| log_shell(&format!("desktop panic: {info}"))));
    // A packaged smoke test must run even when the installed app is open.
    let builder = if smoke_enabled() { tauri::Builder::default() } else {
        tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); }
        }))
    };
    let app = builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            app.manage(WorkerManager::new());
            let manager = app.state::<WorkerManager>().inner().clone();
            let handle = app.handle().clone();
            let startup = std::thread::Builder::new().name("screator-worker-start".into()).spawn(move || {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| start_worker(&handle)))
                    .map_err(|_| "后台启动时发生内部错误。".to_string())
                    .and_then(|result| result.map_err(|error| error.to_string()));
                if let Err(error) = &result { log_shell(&format!("worker startup failed: {error}")); }
                manager.publish(result);
            })?;
            app.state::<WorkerManager>().set_startup_thread(startup);
            if smoke_enabled() {
                if let Some(milliseconds) = std::env::var("SCREATOR_NATIVE_SMOKE_EARLY_EXIT_MS").ok()
                    .and_then(|value| value.parse::<u64>().ok())
                    .filter(|value| (1..=5_000).contains(value)) {
                    let handle = app.handle().clone();
                    tauri::async_runtime::spawn(async move {
                        tokio::time::sleep(Duration::from_millis(milliseconds)).await;
                        EXPECTED_EXIT.store(true, Ordering::Release);
                        handle.exit(0);
                    });
                }
                if let Some(window) = app.get_webview_window("main") {
                    if smoke_inspect_seconds().is_some() { let _ = window.show(); }
                    else { let _ = window.hide(); }
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_request, finish_close, force_close, frontend_ready])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                log_shell("window close requested");
                api.prevent_close();
                let _ = window.emit("app-close-requested", ());
            }
        })
        .build(context)
        .expect("无法启动桌面应用；请检查 Python 环境与后台日志。");
    app.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            log_shell(if EXPECTED_EXIT.load(Ordering::Acquire) { "desktop expected exit" } else { "desktop unexpected event-loop exit" });
            let manager = app.state::<WorkerManager>();
            if let Some(process) = manager.stop() {
                force_stop_worker(process);
            }
            manager.join_startup();
        }
    });
}
