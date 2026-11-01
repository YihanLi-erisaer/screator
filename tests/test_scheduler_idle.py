"""Idle scheduling must stay quiet while preserving task and countdown updates."""
import threading
import time
import unittest
from unittest.mock import patch

import test_desktop as desktop_tests
from screator import youtube


class SchedulerIdleTests(unittest.TestCase):
    setUp = desktop_tests.DesktopTests.setUp
    mocks = desktop_tests.DesktopTests.mocks
    meta = desktop_tests.DesktopTests.meta
    download = desktop_tests.DesktopTests.download
    prepare = desktop_tests.DesktopTests.prepare
    upload = desktop_tests.DesktopTests.upload
    wait_idle = desktop_tests.DesktopTests.wait_idle
    wait_until = desktop_tests.DesktopTests.wait_until
    create = desktop_tests.DesktopTests.create

    def test_idle_queue_does_not_emit_or_scan_and_new_work_wakes_it(self):
        queue_update = threading.Event()
        running_update = threading.Event()
        finished_update = threading.Event()
        download_started = threading.Event()
        release_download = threading.Event()

        def output(event, payload):
            self.notifications.append((event, payload))
            if event == "queue.changed":
                queue_update.set()
                if payload["download"]["running_task_id"]:
                    running_update.set()
                if download_started.is_set() and not payload["active"]:
                    finished_update.set()

        self.service.output = output
        # Synchronize with the dispatcher after initialization, without relying
        # on how quickly its thread starts on a loaded CI machine.
        self.service.scheduler.signal.set()
        self.assertTrue(queue_update.wait(5))
        queue_update.clear()
        queries = []
        with self.service.store._lock:
            self.service.store._conn.set_trace_callback(queries.append)
        try:
            self.assertFalse(queue_update.wait(.7), "idle queue keeps sending UI updates")
            self.assertEqual(queries, [], "idle queue keeps querying SQLite")
        finally:
            with self.service.store._lock:
                self.service.store._conn.set_trace_callback(None)

        def blocked_download(*args, **kwargs):
            download_started.set()
            if not release_download.wait(10):
                raise RuntimeError("test did not release download")
            return self.download(*args, **kwargs)

        with self.mocks(), patch.object(youtube, "download_video", side_effect=blocked_download):
            try:
                self.create()
                self.assertTrue(download_started.wait(5), "new task did not wake idle dispatcher")
                self.assertTrue(running_update.wait(5), "running stage was not reported")
            finally:
                release_download.set()
            self.assertTrue(finished_update.wait(5), "finished queue state was not reported")
            self.wait_idle()
        self.assertEqual(self.service.task("abcdefghijk").status, "ready")
        queue_update.clear()
        self.assertFalse(queue_update.wait(.7), "finished queue did not return to idle")

    def test_waiting_upload_continues_to_report_countdown(self):
        samples = []
        countdown_updates = threading.Event()

        def output(event, payload):
            if event == "queue.changed":
                for lane in payload["uploads"]:
                    if lane["wait_reason"] == "cooldown":
                        samples.append(lane["remaining_seconds"])
                        if len(samples) >= 2:
                            countdown_updates.set()

        with self.mocks():
            self.create()
            self.wait_idle()
            self.service.output = output
            with patch.object(self.service.scheduler.uploader, "try_submit",
                              return_value=("cooldown", time.monotonic() + 60)):
                try:
                    self.service.submit("abcdefghijk", "idle-countdown-submit")
                    self.assertTrue(countdown_updates.wait(5), "upload countdown stopped refreshing")
                    self.assertGreater(samples[0], samples[1])
                    self.assertGreater(samples[1], 0)
                finally:
                    self.service.scheduler.cancel("abcdefghijk")
                    self.wait_idle()


if __name__ == "__main__":
    unittest.main()
