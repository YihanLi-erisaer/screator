"""Offline startup timings using temporary profiles, never real credentials/tasks.

Run from the repository: .desktop-venv/bin/python scripts/benchmark_startup.py
The first worker sample is not an OS cold-boot measurement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class EmptyVault:
    def get_password(self, *_):
        return ""


def worker_ready(profile):
    messages = queue.Queue()
    with tempfile.TemporaryFile(mode="w+") as errors:
        started = time.perf_counter()
        process = subprocess.Popen([sys.executable, "-u", "-m", "yt2bili.desktop_worker",
            "--data-dir", str(profile), "--resources", str(ROOT)], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

        def read_ready():
            for line in process.stdout:
                if json.loads(line).get("event") == "worker.ready":
                    messages.put((time.perf_counter() - started) * 1000)
                    return
            messages.put(None)

        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        try:
            elapsed = messages.get(timeout=30)
            reader.join(timeout=1)
            process.communicate(timeout=15)
            if elapsed is None or process.returncode:
                errors.seek(0)
                raise RuntimeError(errors.read()[-2000:])
            return round(elapsed, 2)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def median_ms(function, samples):
    measured = []
    for _ in range(samples):
        started = time.perf_counter()
        function()
        measured.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(measured), 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--tasks", type=int, nargs="+", default=[0, 1000, 10000])
    args = parser.parse_args()
    if args.samples < 1 or any(count < 0 for count in args.tasks):
        parser.error("samples must be positive and task counts nonnegative")
    with tempfile.TemporaryDirectory(prefix="screator-startup-") as temporary:
        base = Path(temporary)
        samples = [worker_ready(base / f"worker-{index}") for index in range(args.samples)]
        from yt2bili.db import TaskStore
        from yt2bili.desktop_service import DesktopService
        from yt2bili.paths import AppPaths
        scale = []
        for index, count in enumerate(args.tasks):
            paths = AppPaths.default(str(base / f"scale-{index}"), str(ROOT))
            store = TaskStore(paths.root / "data/tasks.sqlite")
            with store.transaction() as db:
                db.executemany("INSERT INTO tasks(task_id,video_id,url,status,bv_id,updated_at) VALUES(?,?,?,?,?,?)",
                    ((f"task-{i}", f"{i:011d}", f"https://youtu.be/{i:011d}", "submitted",
                      "BV1234567890", "2026-01-01T00:00:00+00:00") for i in range(count)))
                db.executemany("""INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status,remote_id)
                    VALUES(?,?,'bilibili',?,'submitted','BV1234567890')""",
                    ((f"pub-{i}", f"task-{i}", f"{i:011d}") for i in range(count)))
            store.close()
            started = time.perf_counter()
            service = DesktopService(paths, lambda *_: None, vault=EmptyVault())
            initialized = (time.perf_counter() - started) * 1000
            try:
                scale.append({"completed_tasks": count, "service_init_ms": round(initialized, 2),
                    "health_median_ms": median_ms(service.health, args.samples),
                    "list_tasks_median_ms": median_ms(service.list_tasks, args.samples),
                    "queue_snapshot_median_ms": median_ms(service.scheduler.snapshot, args.samples)})
            finally:
                service.close()
        print(json.dumps({"source_worker_ready_ms": samples,
            "source_worker_ready_median_ms": statistics.median(samples), "scale": scale}, indent=2))


if __name__ == "__main__":
    main()
