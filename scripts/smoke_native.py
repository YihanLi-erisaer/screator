"""Native WebView -> Rust -> Python check; --release tests a packaged app offline."""
from pathlib import Path
import argparse
import json
import os
import sys
import subprocess
import tempfile
import time


def inspect_process_tree(command, environment, folder, seconds, release):
    import psutil

    def is_active(child):
        try:
            return child.is_running() and child.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    observed, rows = {}, {}
    with subprocess.Popen(command, env=environment, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {})) as process:
        try:
            owner = psutil.Process(process.pid)
            root_row = {"pid": owner.pid, "parent_pid": owner.ppid(), "name": owner.name()}
            deadline = time.monotonic() + 60 + seconds
            while process.poll() is None:
                try:
                    descendants = owner.children(recursive=True)
                except psutil.NoSuchProcess:
                    descendants = []
                for child in descendants:
                    try:
                        identity = (child.pid, child.create_time())
                        row = {"pid": child.pid, "parent_pid": child.ppid(), "name": child.name()}
                        observed.setdefault(identity, child)
                        rows.setdefault(identity, row)
                    except psutil.NoSuchProcess:
                        continue
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, 60 + seconds)
                time.sleep(0.1)
            output, error = process.communicate(timeout=5)
            deadline = time.monotonic() + 5
            while True:
                alive = [child for child in observed.values() if is_active(child)]
                if not alive or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            assert not alive, f"Native smoke left child processes running: {[child.pid for child in alive]}"
            proof = {"root_pid": owner.pid, "processes": [root_row, *rows.values()],
                     "descendants_exited": True, "source": "observed native process descendants"}
            if release and os.name == "nt" and process.returncode == 0:
                assert root_row["name"].lower() == "screator.exe", proof
                names = {row["name"].lower() for row in rows.values()}
                assert {"screator-worker.exe", "msedgewebview2.exe"} <= names, proof
            return subprocess.CompletedProcess(command, process.returncode, output, error), proof
        finally:
            # Only touch descendants observed beneath this isolated smoke app.
            # psutil checks their creation times before killing, preventing PID reuse.
            for child in reversed(list(observed.values())):
                try:
                    if child.is_running():
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--release", type=Path)
parser.add_argument("--shutdown", choices=("graceful", "forced"), help="Exercise the real native close command after frontend startup")
parser.add_argument("--inspect-seconds", type=int, default=0, help="Show the isolated smoke app for 1-120 seconds and inspect its process tree")
args = parser.parse_args()
if not 0 <= args.inspect_seconds <= 120:
    parser.error("--inspect-seconds must be between 0 and 120")
if args.inspect_seconds and args.shutdown:
    parser.error("--inspect-seconds cannot be combined with --shutdown")
root = Path(__file__).resolve().parent.parent
executable = args.release.resolve() if args.release else root / "desktop/src-tauri/target/debug" / ("screator.exe" if os.name == "nt" else "screator")
with tempfile.TemporaryDirectory(prefix="screator-native-") as folder:
    report = Path(folder) / "native.json"
    environment = {**os.environ, "SCREATOR_NATIVE_SMOKE_REPORT": str(report),
                   "SCREATOR_DESKTOP_DATA": folder, "SCREATOR_PROJECT_ROOT": str(root),
                   "SCREATOR_NATIVE_SMOKE_INSPECT_SECONDS": str(args.inspect_seconds),
                   "SCREATOR_PYTHON": os.environ.get("SCREATOR_PYTHON", str(root / (".desktop-venv/Scripts/python.exe" if os.name == "nt" else ".desktop-venv/bin/python")))}
    if os.name == "nt":
        # A separate WebView2 profile also prevents sharing browser processes
        # with an already running installed app during process-tree inspection.
        environment["WEBVIEW2_USER_DATA_FOLDER"] = str(Path(folder) / "webview2")
    if args.shutdown:
        environment["SCREATOR_NATIVE_SMOKE_SHUTDOWN"] = args.shutdown
    else:
        environment.pop("SCREATOR_NATIVE_SMOKE_SHUTDOWN", None)
    if args.release:
        for key in ("SCREATOR_PROJECT_ROOT", "SCREATOR_PYTHON", "SCREATOR_WORKER", "SCREATOR_RESOURCES", "PYTHONPATH"):
            environment.pop(key, None)
        environment["PATH"] = (str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt"
                               else "/usr/bin:/bin:/usr/sbin:/sbin")
    command = [str(executable), "--smoke-test"]
    process_tree = None
    if args.inspect_seconds:
        result, process_tree = inspect_process_tree(command, environment, folder, args.inspect_seconds, bool(args.release))
    else:
        result = subprocess.run(command, env=environment, cwd=folder, timeout=60, capture_output=True,
                                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    value = json.loads(report.read_text(encoding="utf-8"))
    assert value.get("ok") and value.get("protocol_version") == 2, value
    shell_log = (Path(folder) / "logs/desktop-shell.log").read_text(encoding="utf-8")
    assert "worker spawned pid=" in shell_log and "desktop expected exit" in shell_log, shell_log
    if args.shutdown:
        assert value["shutdown"]["mode"] == args.shutdown and value["shutdown"]["completed"], value
        if args.shutdown == "graceful":
            assert "shutdown resources finalized" in shell_log, shell_log
    if process_tree:
        value["process_tree"] = process_tree
    print(json.dumps(value))
