"""Real yt-dlp subprocess regressions using only local HTTP fixtures."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from screator import events, youtube, youtube_worker


@contextmanager
def endpoint(serve):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                serve(self)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/video.mp4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


class YoutubeCancellationTests(unittest.TestCase):
    def start_task(self, fn):
        cancel, stopped = threading.Event(), threading.Event()
        errors, progress = [], []

        def run():
            try:
                with events.task_context("abcdefghijk", cancel, lambda *args: progress.append(args)):
                    fn()
            except Exception as exc:
                errors.append(exc)
            finally:
                stopped.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(cancel.set)
        return cancel, stopped, errors, progress

    def assert_cancelled(self, cancel, stopped, errors):
        started = time.monotonic()
        cancel.set()
        self.assertTrue(stopped.wait(2), "cancellation waited for the network timeout")
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], events.Cancelled)

    def test_metadata_cancel_interrupts_http_wait_without_retry(self):
        entered, release = threading.Event(), threading.Event()
        calls, processes = [], []
        popen = subprocess.Popen

        def serve(handler):
            calls.append(handler.path)
            entered.set()
            release.wait(10)
            handler.send_response(503)
            handler.end_headers()

        def spawn(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        try:
            with endpoint(serve) as url, \
                 patch.object(youtube, "_base_opts", return_value={"socket_timeout": 30, "extractor_retries": 5, "proxy": ""}), \
                 patch.object(youtube_worker.subprocess, "Popen", side_effect=spawn):
                cancel, stopped, errors, _ = self.start_task(lambda: youtube._extract(url, None, False))
                self.assertTrue(entered.wait(15), "yt-dlp did not reach the HTTP fixture")
                self.assert_cancelled(cancel, stopped, errors)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
        finally:
            release.set()

    def test_download_cancel_keeps_partial_and_stops_process(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def serve(handler):
            calls.append(handler.path)
            handler.send_response(200)
            handler.send_header("Content-Type", "video/mp4")
            handler.send_header("Content-Length", str(1024 * 1024))
            handler.end_headers()
            if len(calls) > 1:
                handler.wfile.write(b"v" * 16384)
                handler.wfile.flush()
                entered.set()
                release.wait(10)

        try:
            with tempfile.TemporaryDirectory() as folder, endpoint(serve) as url, \
                 patch.object(youtube, "_base_opts", return_value={"socket_timeout": 30, "proxy": ""}):
                root = Path(folder)
                retained = root / "previous.mp4"
                retained.write_bytes(b"keep completed assets")
                cancel, stopped, errors, progress = self.start_task(
                    lambda: youtube.download_video(url, root, None, validate=False))
                self.assertTrue(entered.wait(15), "yt-dlp did not start the media transfer")
                deadline = time.monotonic() + 3
                while not progress and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(progress, "download progress did not cross the subprocess pipe")
                self.assert_cancelled(cancel, stopped, errors)
                self.assertEqual(len(calls), 2, "cancelled download was retried")
                partial = root / "source.mp4.part"
                self.assertTrue(partial.is_file())
                self.assertGreater(partial.stat().st_size, 0)
                self.assertEqual(retained.read_bytes(), b"keep completed assets")
        finally:
            release.set()

    def test_successful_extract_and_download_keep_results_logs_and_progress(self):
        body = b"local media fixture" * 1024

        def serve(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "video/mp4")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        with tempfile.TemporaryDirectory() as folder, endpoint(serve) as url:
            cancel, progress = threading.Event(), []
            opts = {"outtmpl": str(Path(folder) / "source.%(ext)s"), "socket_timeout": 3, "proxy": ""}
            with events.task_context("abcdefghijk", cancel, lambda *args: progress.append(args)), \
                 self.assertLogs("screator.youtube", level="INFO") as logs:
                meta = youtube._extract_info(opts, url, False)
                self.assertEqual(meta["id"], "video")
                self.assertEqual(meta["ext"], "mp4")
                result = youtube._download_with_slot(opts, url)
            self.assertEqual(result["id"], "video")
            self.assertEqual((Path(folder) / "source.mp4").read_bytes(), body)
            self.assertTrue(progress)
            self.assertTrue(any("Downloading" in line for line in logs.output))

    def test_cancel_while_waiting_for_download_gate_or_windows_lock(self):
        for windows_lock in (False, True):
            with self.subTest(windows_lock=windows_lock):
                gate = threading.Lock()
                gate.acquire()
                started = threading.Event()

                def run():
                    with youtube.download_slots(None if windows_lock else gate):
                        started.set()
                        youtube._download_with_slot({}, "url")

                with patch.object(youtube, "_download_lock", gate if windows_lock else None), \
                     patch.object(youtube, "_extract_info") as extract:
                    cancel, stopped, errors, _ = self.start_task(run)
                    self.assertTrue(started.wait(1))
                    self.assert_cancelled(cancel, stopped, errors)
                    extract.assert_not_called()
                    self.assertTrue(gate.locked(), "cancel released someone else's lock")
                gate.release()

    @unittest.skipIf(os.name == "nt", "POSIX process-group regression; Windows uses a job object")
    def test_cancel_also_kills_ffmpeg_or_js_descendants_holding_pipes(self):
        real_popen = subprocess.Popen
        processes = []
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "child.pid"
            code = (
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
            )

            def spawn(command, **kwargs):
                process = real_popen([sys.executable, "-c", code, str(marker)], **kwargs)
                processes.append(process)
                return process

            with patch.object(youtube_worker.subprocess, "Popen", side_effect=spawn):
                cancel, stopped, errors, _ = self.start_task(
                    lambda: youtube_worker.isolated_extract({}, "url", False))
                deadline = time.monotonic() + 5
                while not marker.is_file() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.is_file())
                self.assert_cancelled(cancel, stopped, errors)
            self.assertIsNotNone(processes[0].poll())
            import psutil
            descendant = int(marker.read_text())
            deadline = time.monotonic() + 2
            while psutil.pid_exists(descendant) and time.monotonic() < deadline:
                if psutil.Process(descendant).status() == psutil.STATUS_ZOMBIE:
                    break
                time.sleep(.02)
            self.assertTrue(not psutil.pid_exists(descendant) or
                            psutil.Process(descendant).status() == psutil.STATUS_ZOMBIE)


if __name__ == "__main__":
    unittest.main()
