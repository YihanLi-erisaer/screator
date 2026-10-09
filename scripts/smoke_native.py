"""Native WebView -> Rust -> Python check; --release tests a packaged app offline."""
from pathlib import Path
import argparse
import json
import os
import sys
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--release", type=Path)
parser.add_argument("--shutdown", choices=("graceful", "forced"), help="Exercise the real native close command after frontend startup")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
executable = args.release.resolve() if args.release else root / "desktop/src-tauri/target/debug" / ("yt2bili-desktop.exe" if os.name == "nt" else "yt2bili-desktop")
with tempfile.TemporaryDirectory(prefix="yt2bili-native-") as folder:
    report = Path(folder) / "native.json"
    environment = {**os.environ, "YT2BILI_NATIVE_SMOKE_REPORT": str(report),
                   "YT2BILI_DESKTOP_DATA": folder, "YT2BILI_PROJECT_ROOT": str(root),
                   "YT2BILI_PYTHON": os.environ.get("YT2BILI_PYTHON", str(root / (".desktop-venv/Scripts/python.exe" if os.name == "nt" else ".desktop-venv/bin/python")))}
    if args.shutdown:
        environment["YT2BILI_NATIVE_SMOKE_SHUTDOWN"] = args.shutdown
    else:
        environment.pop("YT2BILI_NATIVE_SMOKE_SHUTDOWN", None)
    if args.release:
        for key in ("YT2BILI_PROJECT_ROOT", "YT2BILI_PYTHON", "YT2BILI_WORKER", "YT2BILI_RESOURCES", "PYTHONPATH"):
            environment.pop(key, None)
        environment["PATH"] = (str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt"
                               else "/usr/bin:/bin:/usr/sbin:/sbin")
    result = subprocess.run([str(executable), "--smoke-test"], env=environment, cwd=folder, timeout=60, capture_output=True,
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
    print(json.dumps(value))
