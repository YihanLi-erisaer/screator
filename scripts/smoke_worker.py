"""Check source/frozen workers from an unrelated cwd with isolated user data."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--frozen")
parser.add_argument("--resources", type=Path)
parser.add_argument("--protocol-only", action="store_true", help="Skip the FFmpeg media self-test")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from screator.db import SCHEMA_VERSION, TaskStore
from screator.materials import MaterialLibrary
with tempfile.TemporaryDirectory(prefix="screator-smoke-") as folder:
    library_folder = Path(folder) / "素材库"
    library_folder.mkdir()
    store = TaskStore(Path(folder) / "data/tasks.sqlite")
    try:
        library = MaterialLibrary(Path(folder) / "materials", store).create(str(library_folder))
    finally:
        store.close()
    sources = []
    for name in ("图片.png", "音频.mp3", "视频.mp4"):
        source = Path(folder) / name
        source.write_bytes(b"local material import smoke fixture")
        sources.append(str(source))
    command = [str(Path(args.frozen).resolve())] if args.frozen else [sys.executable, "-u", "-m", "screator.desktop_worker"]
    command += ["--data-dir", folder, "--resources", str(args.resources.resolve() if args.resources else root)]
    environment = {**os.environ, "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONIOENCODING": "utf-8"}
    methods = ["system.health", "settings.get", "tasks.list", "system.diagnostics", "system.resources", "materials.libraries", "xiaohongshu.auth.status", "materials.import"]
    params = {"materials.import": {"library_id": library["id"], "paths": sources}}
    requests = "".join(json.dumps({"protocol_version": 2, "request_id": str(i), "method": name, "params": params.get(name, {})}) + "\n"
                       for i, name in enumerate(methods))
    result = subprocess.run(command, cwd=folder, env=environment, input=requests, text=True,
                            encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    responses = {item["request_id"]: item for item in messages if "request_id" in item}
    assert len(responses) == len(methods), messages
    assert all("error" not in item for item in responses.values()), responses
    assert responses["0"]["result"]["protocol_version"] == 2
    assert {"materials_v1", "local_publish_v1", "image_posts_v1", "xiaohongshu_sync_v1"}.issubset(responses["0"]["result"]["capabilities"])
    assert responses["0"]["result"]["schema_version"] == SCHEMA_VERSION
    assert responses["5"]["result"]["items"][0]["id"] == library["id"]
    assert responses["7"]["result"] == {"added": 3, "skipped": 0}
    for value in sources:
        source = Path(value)
        assert source.read_bytes() == (library_folder / source.name).read_bytes()
    assert responses["6"]["result"]["account"] is None and not responses["6"]["result"]["can_sync"]
    assert not responses["2"]["result"]["items"]
    resources = responses["4"]["result"]
    assert resources["process_count"] >= 1 and resources["memory_bytes"] > 0, resources
    assert resources["root_pid"] in [process["pid"] for process in resources["processes"]], resources
    if args.resources:
        bundled = {item["name"]: item for item in responses["3"]["result"]["tools"]}
        for name in ("ffmpeg", "ffprobe", "biliup", "node"):
            assert bundled[name]["available"], bundled[name]
            assert Path(bundled[name]["path"]).resolve().is_relative_to(args.resources.resolve()), bundled[name]
    print(json.dumps({"worker": "frozen" if args.frozen else "source", "protocol": "passed", "responses": len(responses)}))
    if args.protocol_only:
        raise SystemExit(0)
    result = subprocess.run(command + ["--self-test"], cwd=folder, env=environment, text=True,
                            encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    report = json.loads(result.stdout.strip())
    assert report["ok"] and not report["submitted"]
    print(json.dumps(report))
