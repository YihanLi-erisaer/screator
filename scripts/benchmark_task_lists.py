"""Offline task/history polling timings with synthetic data and no real accounts.

Run the same command before and after a change; timings are machine-specific.
Only temporary profiles are created. No downloads or publishing are started.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screator.desktop_service import DesktopService
from screator.paths import AppPaths


class EmptyVault:
    def get_password(self, *_):
        return None


def measure(service, samples, **filters):
    elapsed = []
    # Exclude dispatcher queries from the trace and keep the fixture stable.
    with service.scheduler.guard, service.store._lock:
        service.list_tasks(**filters)
        statements = []
        service.store._conn.set_trace_callback(statements.append)
        try:
            result = service.list_tasks(**filters)
        finally:
            service.store._conn.set_trace_callback(None)
        for _ in range(samples):
            started = time.perf_counter()
            service.list_tasks(**filters)
            elapsed.append((time.perf_counter() - started) * 1000)
    return {"median_ms": round(statistics.median(elapsed), 3),
            "sql_statements": len(statements), "total": result["total"]}


def benchmark(count, samples):
    with tempfile.TemporaryDirectory(prefix="screator-list-benchmark-") as folder:
        service = DesktopService(AppPaths.default(folder, str(ROOT)), lambda *_: None, EmptyVault())
        try:
            stamp = "2026-01-01T00:00:00+00:00"
            with service.store.transaction() as db:
                db.executemany("""INSERT INTO tasks
                    (task_id,video_id,url,status,bv_id,updated_at,title_zh,account_name_snapshot)
                    VALUES(?,?,?,'submitted','BV1234567890',?,'Benchmark title','Benchmark')""",
                    ((f"task-{i:08d}", f"{i:011d}", f"https://youtu.be/{i:011d}", stamp)
                     for i in range(count)))
                db.executemany("""INSERT INTO task_publications
                    (publication_id,task_id,platform,source_video_id,status,remote_id)
                    VALUES(?,?,'bilibili',?,'submitted','BV1234567890')""",
                    ((f"pub-{i}", f"task-{i:08d}", f"{i:011d}") for i in range(count)))
                db.executemany("INSERT INTO imported_publishing_history VALUES(?,?,?)",
                    ((f"import-{i:08d}", json.dumps({"task_id": f"import-{i:08d}",
                        "video_id": f"{i:011d}", "updated_at": stamp, "status": "submitted",
                        "title_zh": "Imported title", "title_orig": "", "publications": []}), stamp)
                     for i in range(count)))
            return {"local_tasks": count, "imported_history": count,
                    "tasks": measure(service, samples),
                    "history": measure(service, samples, history=True),
                    "history_search": measure(service, samples, history=True, search="title"),
                    "platform": measure(service, samples, platform="bilibili")}
        finally:
            service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--tasks", type=int, nargs="+", default=[1000, 10000])
    args = parser.parse_args()
    if args.samples < 1 or any(count < 1 for count in args.tasks):
        parser.error("samples and task counts must be positive")
    print(json.dumps([benchmark(count, args.samples) for count in args.tasks], indent=2))


if __name__ == "__main__":
    main()
