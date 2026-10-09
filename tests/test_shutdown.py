"""Shutdown regressions: cancellation, RPC congestion, receipts and recovery."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_desktop as desktop_tests
from screator import events, youtube, bili_upload, publications, pipeline, media
from screator.db import Task, TaskStore
from screator.desktop_service import DesktopService
from screator.desktop_worker import Protocol, WorkerRPC
from screator.scheduler import Scheduler


def request(identity, method):
    return {"protocol_version": 2, "request_id": identity, "method": method, "params": {}}


class CancellationTests(unittest.TestCase):
    def test_download_retry_wakes_on_cancel_and_keeps_partial(self):
        cancel, waiting = threading.Event(), threading.Event()
        outcome = []
        with tempfile.TemporaryDirectory() as folder:
            partial = Path(folder) / "source.mp4.part"
            partial.write_bytes(b"resumable")
            real_wait = events.wait
            def wait(seconds):
                waiting.set()
                real_wait(seconds)
            def download():
                try:
                    with events.task_context("abcdefghijk", cancel, lambda *_: None):
                        youtube.download_video("https://youtu.be/abcdefghijk", Path(folder), object(), validate=False)
                except Exception as exc:
                    outcome.append(exc)
            with patch.object(youtube, "_base_opts", return_value={}), \
                 patch.object(youtube, "_download_with_slot", side_effect=RuntimeError("SSL: unexpected_eof")) as extract, \
                 patch.object(events, "wait", side_effect=wait):
                thread = threading.Thread(target=download)
                thread.start()
                try:
                    self.assertTrue(waiting.wait(3))
                    started = time.monotonic()
                    cancel.set()
                    thread.join(1)
                    self.assertFalse(thread.is_alive(), "retry backoff ignored cancellation")
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertIsInstance(outcome[0], events.Cancelled)
                    self.assertEqual(extract.call_count, 1)
                    self.assertEqual(partial.read_bytes(), b"resumable")
                finally:
                    cancel.set()
                    thread.join(3)

    def test_thumbnail_cancel_does_not_write_more_chunks_or_wrap_cancel(self):
        cancel = threading.Event()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        def read(_):
            cancel.set()
            return b"unwanted bytes"
        response.read.side_effect = read
        with tempfile.TemporaryDirectory() as folder, patch.object(youtube, "urlopen", return_value=response):
            dest = Path(folder) / "cover.jpg"
            dest.write_bytes(b"previous cover")
            with events.task_context(None, cancel, lambda *_: None):
                with self.assertRaises(events.Cancelled):
                    youtube.download_thumbnail("https://example.com/cover", dest)
            self.assertEqual(dest.read_bytes(), b"previous cover")
            self.assertEqual(list(Path(folder).glob("*.part")), [])


class RPCTests(unittest.TestCase):
    def test_shutdown_bypasses_saturated_pool_and_cancels_queued_requests(self):
        release, occupied = threading.Event(), threading.Event()
        lock = threading.Lock()
        starts = []
        class Service:
            emit = staticmethod(lambda *_: None)
            ready = False
            closed = False
            def dispatch(self, method, params):
                if method == "slow":
                    with lock:
                        starts.append(method)
                        if len(starts) == 4: occupied.set()
                    if not release.wait(5): raise RuntimeError("test did not release RPCs")
                    return {}
                if method == "system.prepare_shutdown": return {"closing": True, "ready": False}
                return self.shutdown_status()
            def shutdown_status(self): return {"ready": self.ready}
            def close(self): self.closed = True
        service = Service()
        protocol = Protocol(io.StringIO())
        rpc = WorkerRPC(protocol, service)
        def reply(identity):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with protocol.lock:
                    replies = [json.loads(line) for line in protocol.stream.getvalue().splitlines()]
                for item in replies:
                    if item.get("request_id") == identity: return item
                time.sleep(.005)
            self.fail(f"No response for {identity}")
        try:
            for i in range(4): rpc.submit(request(str(i), "slow"))
            self.assertTrue(occupied.wait(2))
            for i in range(4, 16): rpc.submit(request(str(i), "slow"))
            rpc.submit(request("overflow", "slow"))
            self.assertIn("error", reply("overflow"))
            started = time.monotonic()
            rpc.submit(request("prepare", "system.prepare_shutdown"))
            self.assertTrue(reply("prepare")["result"]["closing"])
            self.assertLess(time.monotonic() - started, 1)
            for i in range(4, 16): self.assertIn("error", reply(str(i)))
            rpc.submit(request("status", "system.shutdown_status"))
            self.assertFalse(reply("status")["result"]["ready"])
            rpc.submit(request("too-early", "system.finish_shutdown"))
            self.assertIn("error", reply("too-early"))
            self.assertFalse(service.closed)
            rpc.submit(request("new", "slow"))
            self.assertIn("error", reply("new"))
            release.set()
            for i in range(4): reply(str(i))
            service.ready = True
            rpc.submit(request("finish", "system.finish_shutdown"))
            self.assertTrue(reply("finish")["result"]["ready"])
            self.assertTrue(service.closed)
            self.assertEqual(len(starts), 4)
        finally:
            release.set()
            rpc.close()

    def test_invalid_shutdown_message_does_not_cancel_business_requests(self):
        class Service:
            emit = staticmethod(lambda *_: None)
        protocol = Protocol(io.StringIO())
        rpc = WorkerRPC(protocol, Service())
        try:
            invalid = request("invalid", "system.prepare_shutdown")
            invalid["protocol_version"] = 1
            rpc.submit(invalid)
        finally:
            rpc.close()
        self.assertFalse(rpc.cancel.is_set())
        self.assertIn("error", json.loads(protocol.stream.getvalue()))

    def test_real_worker_finalization_ack_and_exit(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            process = subprocess.Popen([sys.executable, "-u", "-m", "screator.desktop_worker",
                                        "--data-dir", folder, "--resources", str(root)],
                                       cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                self.assertEqual(json.loads(process.stdout.readline())["event"], "worker.ready")
                started = time.monotonic()
                # Closing stdin wakes the intake thread; the control lane still
                # completes the handshake before main releases the worker lock.
                messages = "".join(json.dumps(request(str(i), method)) + "\n" for i, method in enumerate(
                    ("system.prepare_shutdown", "system.shutdown_status", "system.finish_shutdown")))
                out, err = process.communicate(messages, timeout=5)
                replies = {item["request_id"]: item for item in map(json.loads, out.splitlines()) if "request_id" in item}
                self.assertEqual(process.returncode, 0, err)
                self.assertTrue(replies["2"]["result"]["ready"])
                self.assertLess(time.monotonic() - started, 1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


class SessionQueryTests(unittest.TestCase):
    def test_status_uses_one_query_with_large_history_and_platform_inflight(self):
        # Windows requires the database to close before its directory is removed.
        with tempfile.TemporaryDirectory() as folder, \
             closing(TaskStore(Path(folder) / "tasks.sqlite")) as store:
            with store.transaction():
                for i in range(1000):
                    task = Task("abcdefghijk", "url", "submitted", task_id=str(uuid.uuid4()), bv_id="BV1234567890")
                    store.upsert(task)
                    store.save_job(task.task_id, {"owner_session_id": "old", "execution_state": "finished"})
                task = Task("12345678901", "url", "queued_upload", task_id=str(uuid.uuid4()))
                store.upsert(task)
                store.save_job(task.task_id, {"owner_session_id": "current", "execution_state": "waiting"})
                publications.ensure_bili(store, task)
                pub = publications.for_platform(store, task.task_id, "bilibili")
                publications.change(store, pub["publication_id"], status="uploading_media")
            scheduler = Scheduler.__new__(Scheduler)
            scheduler.store, scheduler.session_id = store, "current"
            scheduler.guard, scheduler.active, scheduler.closing = threading.RLock(), {}, True
            queries = []
            store._conn.set_trace_callback(queries.append)
            state = scheduler.shutdown_status()
            store._conn.set_trace_callback(None)
            self.assertFalse(state["ready"])
            self.assertEqual(state["pending_task_ids"], [task.task_id])
            self.assertEqual(state["inflight_task_ids"], [task.task_id])
            self.assertEqual(len(queries), 1)


class ShutdownServiceTests(unittest.TestCase):
    setUp = desktop_tests.DesktopTests.setUp
    wait_until = desktop_tests.DesktopTests.wait_until
    wait_idle = desktop_tests.DesktopTests.wait_idle
    mocks = desktop_tests.DesktopTests.mocks
    meta = desktop_tests.DesktopTests.meta
    download = desktop_tests.DesktopTests.download
    prepare = desktop_tests.DesktopTests.prepare
    upload = desktop_tests.DesktopTests.upload
    create = desktop_tests.DesktopTests.create

    def test_cancelled_thumbnail_does_not_start_fallback_extraction(self):
        with self.mocks():
            identity = self.create()["task_id"]
            self.wait_idle()
        task = self.service.task(identity)
        Path(task.cover_path).unlink()
        with patch.object(youtube, "download_thumbnail", side_effect=events.Cancelled("cancelled")), \
             patch.object(media, "extract_frame_cover") as extract:
            with self.assertRaises(events.Cancelled):
                pipeline._prepare_assets(self.service.config.build(), self.service.store, task,
                                         self.meta(task.url, None), Path(task.work_dir), Path(task.video_path))
            extract.assert_not_called()

    def test_shutdown_keeps_success_receipt_and_resumes_deferred_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        def upload(*args, **kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError("test did not release upload")
            return self.upload(*args, **kwargs)
        try:
            with self.mocks(), patch.object(bili_upload, "upload", side_effect=upload):
                identity = self.create(mode="auto")["task_id"]
                self.assertTrue(entered.wait(3))
                video = Path(self.service.task(identity).video_path)
                state = self.service.prepare_shutdown()
                self.assertFalse(state["ready"])
                self.assertEqual(state["inflight_task_ids"], [identity])
                release.set()
                self.wait_until(lambda: self.service.shutdown_status()["ready"], "shutdown readiness")
                task = self.service.task(identity)
                self.assertEqual(task.status, "submitted")
                self.assertEqual(task.bv_id, "BV1234567890")
                self.assertEqual(task.cleanup_state, "pending")
                self.assertTrue(video.is_file())
                self.assertTrue(any(event == "system.shutdown_status" and state["ready"]
                                    for event, state in self.notifications))
                self.service.close()
                reopened = DesktopService(self.paths, lambda *_: None, desktop_tests.MemoryVault())
                self.addCleanup(reopened.close)
                self.wait_until(lambda: reopened.task(identity).cleanup_state == "done", "deferred cleanup")
                self.assertFalse(video.exists())
                self.assertEqual(reopened.task(identity).bv_id, "BV1234567890")
        finally:
            release.set()

    def test_slow_cleanup_does_not_hold_shutdown_guard(self):
        entered, release, replied = threading.Event(), threading.Event(), threading.Event()
        cleanup = self.service.scheduler.uploader.cleanup
        def slow_cleanup(task_id):
            entered.set()
            if not release.wait(5): raise RuntimeError("test did not release cleanup")
            return cleanup(task_id)
        try:
            with self.mocks(), patch.object(self.service.scheduler.uploader, "cleanup", side_effect=slow_cleanup):
                self.create(mode="auto")
                self.assertTrue(entered.wait(3))
                thread = threading.Thread(target=lambda: (self.service.prepare_shutdown(), replied.set()))
                thread.start()
                try:
                    self.assertTrue(replied.wait(1), "cleanup blocked the shutdown guard")
                finally:
                    release.set()
                    thread.join(3)
                self.wait_idle()
        finally:
            release.set()
