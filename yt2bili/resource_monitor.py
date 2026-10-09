"""Read-only resource snapshots of the desktop process tree.

CPU uses user + system time (never children_* time, which would double count).
RSS is summed, so shared pages may be counted more than once. Only descendants
of the native owner are discovered; previously observed descendants remain
owned after reparenting, identified by PID and creation time rather than name.
"""
from __future__ import annotations

import os
import threading
import time

from yt2bili.exceptions import Yt2BiliError


class ResourceMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.root = None
        self.owned = {}
        self.previous = {}
        self.last_time = None
        self.cached = None
        self.application_scope = False

    def _initialize(self, psutil):
        current = psutil.Process()
        owner = os.environ.get("YT2BILI_APP_PID")
        if owner:
            try:
                pid = int(owner)
                # Never trust an arbitrary PID or scan the development launcher.
                ancestor = current.parent()
                while ancestor is not None and ancestor.pid != pid:
                    ancestor = ancestor.parent()
                if ancestor is None:
                    raise ValueError("owner is not an ancestor")
                self.root = ancestor
            except (ValueError, psutil.Error, OSError) as exc:
                raise Yt2BiliError("无法确认应用主进程，请重新启动桌面应用。") from exc
            self.application_scope = True
        else:
            # Standalone worker / protocol smoke tests have no native owner.
            self.root = current
        self.root.create_time()

    def snapshot(self):
        try:
            import psutil
        except ImportError as exc:
            raise Yt2BiliError("资源监控组件缺失，请安装桌面依赖或更新安装包。") from exc
        with self.lock:
            try:
                if self.root is None:
                    self._initialize(psutil)
                if not self.root.is_running():
                    raise Yt2BiliError("应用主进程已退出，资源监控已停止。")
                now = time.monotonic()
                if self.cached and self.last_time is not None and now - self.last_time < 0.5:
                    return self.cached
                elapsed = now - self.last_time if self.last_time is not None else None
                # Reopening the panel must warm up instead of showing a long-term
                # average from the last time the user visited settings.
                baseline = self.previous if elapsed is not None and elapsed <= 10 else {}
                cpus = psutil.cpu_count() or 1
                system_memory = psutil.virtual_memory().total
                discovered = [self.root, *self.root.children(recursive=True)]
            except (psutil.Error, OSError) as exc:
                raise Yt2BiliError("暂时无法读取应用进程，请稍后重试。") from exc

            inaccessible = 0
            for process in discovered:
                try:
                    self.owned[(process.pid, process.create_time())] = process
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, PermissionError):
                    inaccessible += 1

            rows, previous, owned = [], {}, {}
            for identity, process in self.owned.items():
                try:
                    if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                        continue
                    name = process.name()
                    parent_pid = process.ppid()
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, PermissionError):
                    name, parent_pid = "—", None
                cpu, memory, cpu_time = None, None, None
                try:
                    times = process.cpu_times()
                    cpu_time = times.user + times.system
                except (psutil.Error, OSError):
                    pass
                try:
                    memory = process.memory_info().rss
                except (psutil.Error, OSError):
                    pass
                # is_running also detects PID reuse. Do not attribute a reused
                # PID to this application even if its name matches a known tool.
                if not process.is_running():
                    continue
                owned[identity] = process
                if cpu_time is not None:
                    previous[identity] = cpu_time
                    if identity in baseline and elapsed:
                        cpu = round(min(100.0, max(0.0, cpu_time - baseline[identity]) / elapsed / cpus * 100), 2)
                rows.append({"pid": process.pid, "parent_pid": parent_pid, "name": name,
                             "cpu_percent": cpu, "memory_bytes": memory})

            self.owned, self.previous, self.last_time = owned, previous, now
            cpu_values = [r["cpu_percent"] for r in rows if r["cpu_percent"] is not None]
            memory_values = [r["memory_bytes"] for r in rows if r["memory_bytes"] is not None]
            rows.sort(key=lambda r: (-(r["cpu_percent"] or 0), -(r["memory_bytes"] or 0), r["pid"]))
            self.cached = {
                "scope": "application" if self.application_scope else "worker",
                "root_pid": self.root.pid, "sampled_at": time.time(),
                "interval_seconds": round(elapsed, 3) if baseline else None,
                "logical_cpu_count": cpus, "system_memory_bytes": system_memory,
                "cpu_percent": round(min(100.0, sum(cpu_values)), 2) if cpu_values else None,
                "memory_bytes": sum(memory_values) if memory_values else None,
                "process_count": len(rows), "cpu_pending_processes": len(rows) - len(cpu_values),
                "memory_unavailable_processes": len(rows) - len(memory_values),
                "inaccessible_processes": inaccessible, "processes": rows,
            }
            return self.cached
