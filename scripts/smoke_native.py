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
executable = args.release.resolve() if args.release else root / "desktop/src-tauri/target/debug" / ("screator-desktop.exe" if os.name == "nt" else "screator-desktop")
with tempfile.TemporaryDirectory(prefix="screator-native-") as folder:
    report = Path(folder) / "native.json"
    environment = {**os.environ, "SCREATOR_NATIVE_SMOKE_REPORT": str(report),
                   "SCREATOR_DESKTOP_DATA": folder, "SCREATOR_PROJECT_ROOT": str(root),
                   "SCREATOR_PYTHON": os.environ.get("SCREATOR_PYTHON", str(root / (".desktop-venv/Scripts/python.exe" if os.name == "nt" else ".desktop-venv/bin/python")))}
    if args.shutdown:
        environment["SCREATOR_NATIVE_SMOKE_SHUTDOWN"] = args.shutdown
    else:
        environment.pop("SCREATOR_NATIVE_SMOKE_SHUTDOWN", None)
    if args.release:
        for key in ("SCREATOR_PROJECT_ROOT", "SCREATOR_PYTHON", "SCREATOR_WORKER", "SCREATOR_RESOURCES", "PYTHONPATH"):
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
