"""Offline timings for worker exit, retry cancellation and history lookups."""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from screator import events
from screator.db import Task, TaskStore


def worker_exit():
    with tempfile.TemporaryDirectory(prefix="screator-close-") as folder:
        process = subprocess.Popen([sys.executable, "-u", "-m", "screator.desktop_worker",
                                    "--data-dir", folder, "--resources", str(root)],
                                   cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            assert json.loads(process.stdout.readline())["event"] == "worker.ready"
            started = time.perf_counter()
            requests = "".join(json.dumps({"protocol_version": 2, "request_id": str(i), "method": method, "params": {}}) + "\n"
                               for i, method in enumerate(("system.prepare_shutdown", "system.finish_shutdown")))
            output, errors = process.communicate(requests, timeout=5)
            elapsed = (time.perf_counter() - started) * 1000
            assert process.returncode == 0, errors
            replies = {message["request_id"]: message for message in map(json.loads, output.splitlines()) if "request_id" in message}
            assert replies["1"]["result"]["ready"], replies
            return elapsed
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def retry_cancel():
    cancel, started, stopped = threading.Event(), threading.Event(), threading.Event()
    def run():
        try:
            with events.task_context(None, cancel, lambda *_: None):
                started.set()
                events.wait(60)
        except events.Cancelled:
            stopped.set()
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(2)
        stamp = time.perf_counter()
        cancel.set()
        assert stopped.wait(1), "Cancellation did not wake the backoff"
        thread.join(1)
        return (time.perf_counter() - stamp) * 1000
    finally:
        cancel.set()
        thread.join(2)


def history_status(records, runs):
    with tempfile.TemporaryDirectory() as folder:
        store = TaskStore(Path(folder) / "tasks.sqlite")
        try:
            with store.transaction():
                for _ in range(records):
                    task = Task("abcdefghijk", "url", "submitted", task_id=str(uuid.uuid4()))
                    store.upsert(task)
                    store.save_job(task.task_id, {"owner_session_id": "history", "execution_state": "finished"})
            def old_lookup():
                return [task.task_id for task in store.list_all()
                        if (store.get_job(task.task_id) or {}).get("owner_session_id") == "current"
                        and (store.get_job(task.task_id) or {}).get("execution_state") in ("queued", "running", "waiting")]
            results = {}
            for name, lookup in (("previous", old_lookup), ("optimized", lambda: store.session_jobs("current"))):
                timings = []
                queries = []
                store._conn.set_trace_callback(queries.append)
                assert not lookup()
                store._conn.set_trace_callback(None)
                for _ in range(runs):
                    started = time.perf_counter()
                    assert not lookup()
                    timings.append((time.perf_counter() - started) * 1000)
                results[name] = {"median_ms": round(statistics.median(timings), 3), "queries": len(queries)}
            return {"records": records, **results}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--history", type=int, default=10000)
    args = parser.parse_args()
    if args.runs < 1 or args.history < 0:
        parser.error("runs must be positive and history must be nonnegative")
    exits = [worker_exit() for _ in range(args.runs)]
    cancels = [retry_cancel() for _ in range(args.runs)]
    print(json.dumps({"runs": args.runs,
                      "idle_worker_exit": {"median_ms": round(statistics.median(exits), 3), "max_ms": round(max(exits), 3)},
                      "cancel_60s_backoff": {"median_ms": round(statistics.median(cancels), 3), "max_ms": round(max(cancels), 3)},
                      "shutdown_status_lookup": history_status(args.history, args.runs)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
