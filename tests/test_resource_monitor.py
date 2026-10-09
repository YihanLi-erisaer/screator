import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psutil

from yt2bili.exceptions import Yt2BiliError
from yt2bili.resource_monitor import ResourceMonitor


class Process:
    def __init__(self, pid, created=1, memory=1024, cpu=10):
        self.pid, self.created, self.memory, self.cpu = pid, created, memory, cpu
        self.running, self.denied = True, False
        self.descendants = []

    def create_time(self): return self.created
    def is_running(self): return self.running
    def status(self): return psutil.STATUS_RUNNING
    def name(self): return "ffmpeg.exe" if self.pid != 1 else "Screator.exe"
    def ppid(self): return 1
    def children(self, recursive=False):
        assert recursive
        return self.descendants
    def cpu_times(self):
        if self.denied: raise psutil.AccessDenied(self.pid)
        # Huge children time must never enter the application's CPU sum.
        return SimpleNamespace(user=self.cpu, system=0, children_user=10000, children_system=10000)
    def memory_info(self):
        if self.denied: raise psutil.AccessDenied(self.pid)
        return SimpleNamespace(rss=self.memory)


class ResourceMonitorTests(unittest.TestCase):
    def setUp(self):
        self.root = Process(1, memory=100)
        self.tool = Process(2, memory=200)
        self.root.descendants = [self.tool]
        self.monitor = ResourceMonitor()
        self.monitor.root = self.root
        self.monitor.application_scope = True
        self.clock = 100.0
        self.addCleanup(patch.stopall)
        patch("yt2bili.resource_monitor.time.monotonic", side_effect=lambda: self.clock).start()
        patch.object(psutil, "cpu_count", return_value=4).start()
        patch.object(psutil, "virtual_memory", return_value=SimpleNamespace(total=4096)).start()

    def next(self):
        self.clock += 2
        return self.monitor.snapshot()

    def test_normalized_cpu_totals_exclude_child_time_and_memory_sum(self):
        first = self.monitor.snapshot()
        self.assertIsNone(first["cpu_percent"])
        self.assertEqual(first["cpu_pending_processes"], 2)
        self.root.cpu += 1
        self.tool.cpu += 2
        second = self.next()
        self.assertEqual(second["cpu_percent"], 37.5)
        self.assertEqual(second["memory_bytes"], 300)
        self.assertEqual(second["process_count"], 2)
        self.assertEqual(second["interval_seconds"], 2)
        self.assertEqual(sum(p["cpu_percent"] for p in second["processes"]), 37.5)
        json.dumps(second, allow_nan=False)

    def test_exited_process_removed_and_new_process_warms_up(self):
        self.monitor.snapshot()
        self.tool.running = False
        replacement = Process(3, memory=400, cpu=999)
        self.root.descendants = [replacement]
        result = self.next()
        self.assertEqual({p["pid"] for p in result["processes"]}, {1, 3})
        self.assertEqual(result["memory_bytes"], 500)
        self.assertEqual(result["cpu_percent"], 0)
        self.assertEqual(result["cpu_pending_processes"], 1)
        self.assertIsNone(next(p for p in result["processes"] if p["pid"] == 3)["cpu_percent"])

    def test_reparented_descendant_remains_owned(self):
        self.monitor.snapshot()
        self.root.descendants = []
        self.assertEqual(self.next()["process_count"], 2)

    def test_reused_pid_does_not_inherit_cpu_baseline(self):
        self.monitor.snapshot()
        self.tool.running = False
        replacement = Process(2, created=2, cpu=999)
        self.root.descendants = [replacement]
        result = self.next()
        self.assertEqual(result["process_count"], 2)
        self.assertIsNone(next(p for p in result["processes"] if p["pid"] == 2)["cpu_percent"])

    def test_denied_metrics_are_unknown_instead_of_zero(self):
        self.monitor.snapshot()
        self.tool.denied = True
        result = self.next()
        self.assertEqual(result["memory_bytes"], 100)
        self.assertEqual(result["memory_unavailable_processes"], 1)
        denied = next(p for p in result["processes"] if p["pid"] == 2)
        self.assertIsNone(denied["memory_bytes"])
        self.assertIsNone(denied["cpu_percent"])

    def test_cpu_counter_reset_and_long_pause(self):
        self.monitor.snapshot()
        self.tool.cpu = 0
        self.assertEqual(self.next()["cpu_percent"], 0)
        self.clock += 20
        self.assertIsNone(self.monitor.snapshot()["cpu_percent"])

    def test_repeated_reads_are_cached_and_dead_owner_stops_monitoring(self):
        first = self.monitor.snapshot()
        self.assertIs(self.monitor.snapshot(), first)
        self.root.running = False
        with self.assertRaises(Yt2BiliError): self.monitor.snapshot()

    def test_arbitrary_owner_pid_is_rejected(self):
        current = SimpleNamespace(parent=lambda: None)
        with patch.dict(os.environ, {"YT2BILI_APP_PID": "987654"}), patch.object(psutil, "Process", return_value=current):
            with self.assertRaises(Yt2BiliError): ResourceMonitor().snapshot()

    def test_missing_component_has_actionable_error(self):
        with patch.dict(sys.modules, {"psutil": None}):
            with self.assertRaisesRegex(Yt2BiliError, "组件缺失"):
                ResourceMonitor().snapshot()

    def test_system_process_enumeration_denial_is_actionable(self):
        with patch.object(self.root, "children", side_effect=PermissionError("sysctl denied")):
            with self.assertRaisesRegex(Yt2BiliError, "暂时无法读取"):
                self.monitor.snapshot()


class LiveResourceTests(unittest.TestCase):
    def test_native_owner_and_real_worker_protocol_include_siblings_and_descendants(self):
        # A separate owner simulates the native shell's environment contract.
        # A CPU/memory process is a sibling of the real worker (like WebView2).
        # An identically named external process belongs to the test runner and
        # must not enter the owner tree. The worker itself uses production RPC.
        owner_code = r'''
import json, os, subprocess, sys, tempfile, time
root = os.getcwd()
load = subprocess.Popen([sys.executable, "-u", "-c", "import time; data=bytearray(32*1024**2); print('ready',flush=True); end=time.monotonic()+30;\nwhile time.monotonic()<end: sum(range(10000))"], stdout=subprocess.PIPE, text=True)
worker = None
try:
    assert load.stdout.readline().strip() == "ready"
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ, "YT2BILI_APP_PID": str(os.getpid()), "YT2BILI_COORDINATION_DIR": folder}
        command = [os.environ["YT2BILI_TEST_FROZEN_WORKER"]] if os.environ.get("YT2BILI_TEST_FROZEN_WORKER") else [sys.executable, "-u", "-m", "yt2bili.desktop_worker"]
        worker = subprocess.Popen(command + ["--data-dir", folder, "--resources", root], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def sample(ident):
            worker.stdin.write(json.dumps({"protocol_version":2,"request_id":ident,"method":"system.resources","params":{}})+"\n")
            worker.stdin.flush()
            while True:
                line = worker.stdout.readline()
                if not line: raise RuntimeError(worker.stderr.read())
                value = json.loads(line)
                if value.get("request_id") == ident:
                    assert "error" not in value, value
                    return value["result"]
        sample("first")
        time.sleep(0.8)
        snapshot = sample("second")
        print(json.dumps({"snapshot":snapshot,"owner_pid":os.getpid(),"worker_pid":worker.pid,"load_pid":load.pid}),flush=True)
        worker.stdin.close()
        worker.wait(timeout=15)
finally:
    load.kill(); load.wait()
    if worker is not None and worker.poll() is None: worker.kill(); worker.wait()
'''
        external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (external.kill(), external.wait()) if external.poll() is None else None)
        environment = dict(os.environ)
        environment.pop("YT2BILI_APP_PID", None)
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, "-c", owner_code], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        snapshot = report["snapshot"]
        pids = {p["pid"] for p in snapshot["processes"]}
        self.assertTrue({report["owner_pid"], report["worker_pid"], report["load_pid"]}.issubset(pids))
        self.assertNotIn(external.pid, pids)
        self.assertNotIn(os.getpid(), pids)
        self.assertEqual(snapshot["scope"], "application")
        self.assertGreater(snapshot["cpu_percent"], 0)
        self.assertGreater(snapshot["memory_bytes"], 32 * 1024 ** 2)
        self.assertAlmostEqual(snapshot["memory_bytes"], sum(p["memory_bytes"] for p in snapshot["processes"]))
        self.assertTrue(all(set(p) == {"pid", "parent_pid", "name", "cpu_percent", "memory_bytes"} for p in snapshot["processes"]))


if __name__ == "__main__":
    unittest.main()
