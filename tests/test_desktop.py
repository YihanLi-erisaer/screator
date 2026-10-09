from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from screator import events, media, pipeline, publications, youtube, bili_upload, desktop_auth
from screator.db import Task, TaskStore
from screator.desktop_auth import LoginSession, validate_login
from screator.desktop_service import DesktopService, parse_urls
from screator.desktop_settings import DesktopSettings
from screator.desktop_worker import DesktopLogHandler, Protocol, redact
from screator.exceptions import InvalidMediaError, AppError
from screator.locking import FileLock, work_lock
from screator.paths import AppPaths


# Windows CI flushes many small SQLite transactions more slowly than a local SSD.
# These tests assert ordering/state, not a five-second throughput guarantee.
ASYNC_TIMEOUT = 30


class MemoryVault:
    def __init__(self): self.values = {}
    def get_password(self, service, name): return self.values.get((service, name))
    def set_password(self, service, name, value): self.values[service, name] = value
    def delete_password(self, service, name): self.values.pop((service, name), None)


def login_fixture():
    return {"cookie_info": {"cookies": [{"name": name, "value": "123" if name == "DedeUserID" else "fixture"} for name in ("SESSDATA", "bili_jct", "DedeUserID")]},
            "sso": [], "token_info": {"access_token": "fixture", "refresh_token": "fixture", "expires_in": 3600, "mid": 123}}


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.environment = patch.dict(os.environ, {"SCREATOR_COORDINATION_DIR": str(Path(self.tmp.name) / "coordination")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.paths = AppPaths.default(self.tmp.name, self.tmp.name)
        self.notifications = []
        self.service = DesktopService(self.paths, lambda event, payload: self.notifications.append((event, payload)), MemoryVault())
        self.addCleanup(self.service.close)
        self.service.config.set_key("test-key")
        self.uploads = []
        self.account = self.service.accounts.bind(login_fixture(), verify=False)
        self.service.config.values["upload_gap_seconds"] = 0

    def wait_until(self, predicate, description):
        deadline = time.monotonic() + ASYNC_TIMEOUT
        while not predicate():
            if time.monotonic() >= deadline:
                tasks = [(t.task_id, t.status, t.wait_reason, t.error)
                         for t in self.service.store.list_all()]
                self.fail(f"Timed out waiting for {description}; tasks={tasks}; "
                          f"queue={self.service.scheduler.snapshot()}")
            time.sleep(.05)

    def wait_idle(self):
        self.wait_until(lambda: not self.service.scheduler.snapshot()["active"], "idle scheduler")

    def meta(self, url, settings):
        video_id = parse_urls(url)[0][0]
        return youtube.YoutubeMeta(video_id, url, "Original title", "description", "author", 10, "thumb", None, url)

    def download(self, url, folder, settings, **kwargs):
        file = folder / "source.mp4"
        file.write_bytes(b"complete video")
        return file

    def prepare(self, settings, store, task, meta, work, video):
        task.title_zh = task.title_zh or "翻译标题"
        task.desc_zh = "简介"
        cover = work / "cover.jpg"
        cover.write_bytes(b"cover")
        task.cover_path = str(cover)
        store.upsert(task)

    def upload(self, settings, video, cover, title, desc, url, on_started=None):
        if on_started: on_started(99999999)
        self.uploads.append((video.parent.name, title, desc))
        return "BV1234567890"

    def mocks(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(media, "require_ffmpeg"))
        stack.enter_context(patch.object(youtube, "fetch_meta", side_effect=self.meta))
        stack.enter_context(patch.object(youtube, "download_video", side_effect=self.download))
        stack.enter_context(patch.object(media, "prepare_upload_video", side_effect=lambda source, *a, **kw: source))
        stack.enter_context(patch.object(pipeline, "_prepare_assets", side_effect=self.prepare))
        stack.enter_context(patch.object(bili_upload, "upload", side_effect=self.upload))
        stack.enter_context(patch.object(bili_upload, "renew"))
        stack.enter_context(patch.object(desktop_auth, "verify_credentials", side_effect=lambda info: {"uid": desktop_auth.credential_identity(info), "nickname": "Test"}))
        return stack

    def create(self, video_id="abcdefghijk", **kwargs):
        return self.service.create("https://youtu.be/" + video_id, "create-" + video_id, account_id=self.account["account_id"], **kwargs)

    def test_preview_never_submits_and_edit_is_used_on_submit(self):
        with self.mocks():
            self.create()
            self.wait_idle()
            self.assertFalse(self.uploads)
            task = self.service.task("abcdefghijk")
            self.assertEqual(task.status, "ready")
            self.service.update_metadata(task.video_id, "编辑后的标题", "编辑后的简介")
            self.service.config.build().bili_cookies.write_text("{}")
            self.service.submit(task.video_id, "submit-operation")
            self.service.submit(task.video_id, "submit-operation")
            self.wait_idle()
            self.assertEqual(len(self.uploads), 1)
            self.assertEqual(self.uploads[0][1], "编辑后的标题")
            self.assertIn("原链接：", self.uploads[0][2])
            self.assertEqual(self.service.task(task.video_id).status, "submitted")
            self.assertFalse(Path(task.work_dir).exists())

    def test_duplicate_operation_and_url_only_enqueues_once(self):
        with self.mocks():
            first = self.create()
            second = self.create()
            self.wait_idle()
            self.assertEqual(first, second)
            self.assertEqual(len(self.service.store.list_all()), 1)
            result = self.service.create("https://youtube.com/watch?v=abcdefghijk&t=20", "another-operation", account_id=self.account["account_id"])
            self.assertFalse(result["created"])

    def test_external_work_lock_prevents_retry(self):
        with self.mocks():
            self.create()
            self.wait_idle()
            task = self.service.task("abcdefghijk")
            self.service.store.update(task.task_id, status="failed")
            with work_lock(Path(task.work_dir)):
                self.service.retry(task.task_id, "locked-retry")
                self.wait_idle()
            self.assertEqual(self.service.task(task.task_id).status, "failed")

    def test_export_includes_rotated_logs_and_redacts_credentials(self):
        (self.paths.root / "logs/desktop.log.1").write_text("old task complete\nSESSDATA=secret", encoding="utf-8")
        (self.paths.root / "logs/desktop.log").write_text("new task complete", encoding="utf-8")
        target = self.paths.root / "export.txt"
        self.service.log_export(str(target))
        value = target.read_text(encoding="utf-8")
        self.assertIn("old task complete", value)
        self.assertIn("new task complete", value)
        self.assertNotIn("secret", value)

    def test_unknown_submission_cannot_retry_or_resubmit(self):
        with self.mocks():
            self.create()
            self.wait_idle()
            self.service.config.build().bili_cookies.write_text("{}")
            with patch.object(bili_upload, "upload", side_effect=lambda *a, **k: (k["on_started"](99999999), "")[1]):
                self.service.submit("abcdefghijk", "submit-unknown")
                self.wait_idle()
            task = self.service.task("abcdefghijk")
            self.assertEqual(task.status, "submission_unknown")
            self.assertTrue(Path(task.video_path).exists())
            with self.assertRaises(AppError): self.service.retry(task.video_id, "retry-unknown")
            with self.assertRaises(AppError): self.service.submit(task.video_id, "submit-again")
            self.service.resolve(task.video_id, bv_id="BV1234567890")
            self.assertTrue(Path(task.video_path).exists())

    def test_upload_error_becomes_unknown_not_automatic_retry(self):
        def fail_upload(*args, **kwargs):
            kwargs["on_started"](99999999)
            raise AppError("connection lost")
        with self.mocks():
            self.create()
            self.wait_idle()
            self.service.config.build().bili_cookies.write_text("{}")
            with patch.object(bili_upload, "upload", side_effect=fail_upload):
                self.service.submit("abcdefghijk", "submit-lost")
                self.wait_idle()
            self.assertEqual(self.service.task("abcdefghijk").status, "submission_unknown")

    def test_four_tasks_continue_downloading_while_validation_waits(self):
        validating = threading.Event()
        release = threading.Event()
        fourth = threading.Event()
        original = self.download
        def download(url, *args, **kwargs):
            if "44444444444" in url: fourth.set()
            return original(url, *args, **kwargs)
        def validate(source, *args, **kwargs):
            if not validating.is_set():
                validating.set(); release.wait(4)
            return source
        with self.mocks(), patch.object(youtube, "download_video", side_effect=download), patch.object(media, "prepare_upload_video", side_effect=validate):
            try:
                for i in range(1, 5): self.create(str(i) * 11)
                self.assertTrue(validating.wait(2))
                self.assertTrue(fourth.wait(2))
            finally:
                release.set()
            self.wait_idle()
            self.assertTrue(all(t.status == "ready" for t in self.service.store.list_all()))

    def test_cancel_download_is_recoverable(self):
        started = threading.Event()
        def download(*args, **kwargs):
            started.set()
            while True:
                events.check_cancelled()
                time.sleep(.01)
        with self.mocks(), patch.object(youtube, "download_video", side_effect=download):
            self.create()
            self.assertTrue(started.wait(2))
            self.service.cancel("abcdefghijk")
            self.wait_idle()
        self.assertEqual(self.service.task("abcdefghijk").status, "cancelled")
        with self.mocks():
            self.service.retry("abcdefghijk", "retry-cancelled")
            self.wait_idle()
        self.assertEqual(self.service.task("abcdefghijk").status, "ready")

    def test_invalid_media_retries_five_times_and_never_uploads(self):
        with self.mocks(), patch.object(media, "prepare_upload_video", side_effect=InvalidMediaError("broken")) as check:
            self.create()
            self.wait_idle()
            self.assertEqual(check.call_count, 5)
            self.assertEqual(self.service.task("abcdefghijk").status, "failed")
            self.assertFalse(self.uploads)

    def test_repair_preserves_bv_without_posting(self):
        task = Task("abcdefghijk", "https://youtu.be/abcdefghijk", "submitted", bv_id="BV1234567890")
        task.account_id = self.account["account_id"]
        task.account_uid_snapshot = self.account["uid"]
        self.service.store.upsert(task)
        with self.mocks():
            self.service.repair(task.video_id, "repair-operation")
            self.wait_idle()
        task = self.service.task(task.video_id)
        self.assertEqual((task.status, task.bv_id), ("submitted", "BV1234567890"))
        self.assertTrue(Path(task.video_path).is_file())
        self.assertFalse(self.uploads)

    def test_settings_and_key_never_leak_secret_to_disk_or_response(self):
        result = self.service.config.public()
        self.assertNotIn("test-key", json.dumps(result))
        self.service.update_settings({"theme": "dark"})
        self.assertNotIn("test-key", (self.paths.root / "settings.json").read_text(encoding="utf-8"))
        with self.assertRaises(AppError): self.service.update_settings({"upload_gap_seconds": -1})
        with self.assertRaises(AppError): self.service.update_settings({"deepl_auth_key": "bad"})

    def test_youtube_resolution_setting_persists_and_old_snapshots_use_best(self):
        self.assertEqual(self.service.config.build().youtube_max_height, 0)
        old_snapshot = self.service.config.snapshot()
        old_snapshot.pop("youtube_max_height")
        result = self.service.update_settings({"youtube_max_height": 720})
        self.assertEqual(result["youtube_max_height"], 720)
        self.assertEqual(self.service.config.build().youtube_max_height, 720)
        self.assertEqual(self.service.config.build(old_snapshot).youtube_max_height, 0)
        saved = json.loads((self.paths.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["youtube_max_height"], 720)
        reloaded = DesktopSettings(self.paths, MemoryVault())
        self.assertEqual(reloaded.build().youtube_max_height, 720)
        for invalid in (True, 999, "720"):
            with self.subTest(invalid=invalid), self.assertRaises(AppError):
                self.service.update_settings({"youtube_max_height": invalid})

    def test_ui_language_persists_and_rejects_invalid_values(self):
        self.assertEqual(self.service.config.public()["ui_language"], "zh-CN")
        self.assertEqual(self.service.update_settings({"ui_language": "en"})["ui_language"], "en")
        self.assertEqual(DesktopSettings(self.paths, MemoryVault()).values["ui_language"], "en")
        self.assertEqual(self.service.update_settings({"ui_language": "zh-HK"})["ui_language"], "zh-HK")
        for invalid in (None, True, "fr", ["en"]):
            with self.subTest(invalid=invalid), self.assertRaises(AppError):
                self.service.update_settings({"ui_language": invalid})

    def test_youtube_audio_language_persists_and_old_snapshots_use_auto(self):
        old_snapshot = self.service.config.snapshot()
        old_snapshot.pop("youtube_audio_language")
        result = self.service.update_settings({"youtube_audio_language": "zh"})
        self.assertEqual(result["youtube_audio_language"], "zh")
        self.assertEqual(self.service.config.build().youtube_audio_language, "zh")
        self.assertEqual(self.service.config.build(old_snapshot).youtube_audio_language, "auto")
        saved = json.loads((self.paths.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["youtube_audio_language"], "zh")
        self.assertEqual(DesktopSettings(self.paths, MemoryVault()).build().youtube_audio_language, "zh")
        for invalid in (None, True, "xx", ["zh"]):
            with self.subTest(invalid=invalid), self.assertRaises(AppError):
                self.service.update_settings({"youtube_audio_language": invalid})

    def test_old_acfun_enable_setting_is_ignored_and_removed_on_next_save(self):
        saved = self.service.config.snapshot()
        saved["acfun_experimental_enabled"] = False
        (self.paths.root / "settings.json").write_text(json.dumps(saved), encoding="utf-8")
        reloaded = DesktopSettings(self.paths, MemoryVault())
        self.assertNotIn("acfun_experimental_enabled", reloaded.public())
        reloaded.update({"theme": "dark"})
        self.assertNotIn("acfun_experimental_enabled", json.loads((self.paths.root / "settings.json").read_text(encoding="utf-8")))

    def test_gpu_backend_settings_are_persisted_and_snapshotted(self):
        previous = self.service.config.snapshot()
        previous.pop("local_llm_backend")
        result = self.service.update_settings({"local_llm_backend": "vulkan", "hwaccel": "d3d11va"})
        self.assertEqual(result["local_llm_backend"], "vulkan")
        reloaded = DesktopSettings(self.paths, MemoryVault())
        self.assertEqual(reloaded.build().local_llm_backend, "vulkan")
        self.assertEqual(reloaded.build(previous).local_llm_backend, "auto")
        self.assertEqual(reloaded.values["hwaccel"], "d3d11va")
        with self.assertRaises(AppError):
            self.service.update_settings({"local_llm_backend": "unsupported"})

    def test_translation_timeouts_are_persisted_and_public(self):
        result = self.service.update_settings({
            "local_llm_timeout_seconds": 240,
            "translation_total_timeout_seconds": 360,
        })
        self.assertEqual(result["local_llm_timeout_seconds"], 240)
        self.assertEqual(result["translation_total_timeout_seconds"], 360)
        saved = json.loads((self.paths.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["local_llm_timeout_seconds"], 240)
        self.assertEqual(saved["translation_total_timeout_seconds"], 360)
        reloaded = DesktopSettings(self.paths, MemoryVault())
        self.assertEqual(reloaded.values["local_llm_timeout_seconds"], 240)
        self.assertEqual(reloaded.values["translation_total_timeout_seconds"], 360)
        with self.assertRaises(AppError):
            self.service.update_settings({
                "local_llm_timeout_seconds": 400,
                "translation_total_timeout_seconds": 300,
            })

    def test_saved_qwen3_settings_upgrade_to_qwen35_without_changing_task_snapshots(self):
        saved = self.service.config.snapshot()
        saved["local_llm_model"] = "qwen3:8b"
        (self.paths.root / "settings.json").write_text(json.dumps(saved), encoding="utf-8")
        reloaded = DesktopSettings(self.paths, MemoryVault())
        self.assertEqual(reloaded.values["local_llm_model"], "qwen3.5:4b")
        from screator.translation.config import legacy_snapshot
        self.assertEqual(legacy_snapshot(saved)["local_llm_model"], "qwen3:8b")

    def test_local_task_creation_does_not_require_deepl_vault(self):
        self.service.config.set_key("")
        with self.mocks(), patch.object(self.service.config, "key", side_effect=AssertionError("must not read key")):
            self.create()
            self.wait_idle()
        self.assertEqual(self.service.task("abcdefghijk").status, "ready")

    def test_retranslation_clears_old_text_before_queue_and_never_uploads(self):
        from screator.translation.types import TranslationResult
        with self.mocks():
            self.create(); self.wait_idle()
        self.service.update_metadata("abcdefghijk", "用户编辑", "用户正文")
        with self.assertRaises(AppError):
            self.service.retranslate("abcdefghijk", "retranslate-no-confirm")
        started, release = threading.Event(), threading.Event()
        def slow_translation(*args, **kwargs):
            started.set()
            release.wait(ASYNC_TIMEOUT)
            return TranslationResult("新翻译", "新正文", "local_llm")
        with patch("screator.translation.tasks.translate_group", side_effect=slow_translation):
            self.service.retranslate("abcdefghijk", "retranslate-success", replace_edited=True)
            try:
                self.assertTrue(started.wait(ASYNC_TIMEOUT))
                queued = self.service.get_task("abcdefghijk")
                self.assertEqual((queued["title_zh"], queued["desc_zh"]), ("", ""))
                self.assertEqual(queued["translation"]["state"], "queued")
                self.assertEqual(queued["snapshot"]["mode"], "retranslate")
                self.assertEqual((Path(queued["work_dir"]) / "title.txt").read_text(encoding="utf-8"), "")
                self.assertEqual((Path(queued["work_dir"]) / "desc.txt").read_text(encoding="utf-8"), "")
                self.assertEqual(publications.for_platform(self.service.store, queued["task_id"], "bilibili")["text"], "")
            finally:
                release.set()
            self.wait_idle()
        self.assertEqual(self.service.task("abcdefghijk").title_zh, "新翻译")
        self.assertEqual(self.service.task("abcdefghijk").status, "ready")
        self.assertEqual(self.service.store.translation("abcdefghijk")["state"], "complete")
        self.assertEqual(publications.for_platform(self.service.store, queued["task_id"], "bilibili")["text"], "新翻译")
        self.assertFalse(self.uploads)

    def test_cancel_waiting_download_finishes_immediately_and_keeps_record(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def download(url, *args, **kwargs):
            calls.append(url)
            if "abcdefghijk" in url:
                started.set()
                release.wait(5)
            return self.download(url, *args, **kwargs)
        with self.mocks(), patch.object(youtube, "download_video", side_effect=download):
            try:
                self.create()
                self.assertTrue(started.wait(2))
                second = self.create("12345678901")["task_id"]
                def dispatched_but_not_started():
                    with self.service.scheduler.guard:
                        item = self.service.scheduler.active.get(second)
                        return bool(item and item.stage == "download" and not item.running)
                self.wait_until(dispatched_but_not_started, "waiting download dispatch")
                self.service.cancel(second)
                self.assertEqual(self.service.task(second).status, "cancelled")
                self.assertIsNotNone(self.service.store.get(second))
                self.assertEqual(self.service.store.get_job(second)["execution_state"], "finished")
                self.assertNotIn(second, self.service.scheduler.active)
                self.assertNotIn(second, [item["task_id"] for item in self.service.scheduler.snapshot()["active"]])
                self.service.retry(second, "retry-waiting-cancel")
            finally:
                release.set()
            self.wait_idle()
        self.assertEqual(sum("12345678901" in url for url in calls), 1)
        self.assertEqual(self.service.task(second).status, "ready")

    def test_cancel_download_before_dispatch_keeps_record(self):
        with self.service.scheduler.guard:
            task_id = self.create()["task_id"]
            self.service.cancel(task_id)
            self.assertEqual(self.service.task(task_id).status, "cancelled")
            self.assertEqual(self.service.store.get_job(task_id)["execution_state"], "finished")
        self.assertIsNotNone(self.service.store.get(task_id))

    def test_failed_retranslation_keeps_text_cleared_and_task_retryable(self):
        from screator.translation.types import TranslationError
        with self.mocks():
            self.create(); self.wait_idle()
        self.service.update_metadata("abcdefghijk", "用户编辑", "用户正文")
        with patch("screator.translation.tasks.translate_group", side_effect=TranslationError("FAIL", "模拟翻译失败")):
            self.service.retranslate("abcdefghijk", "retranslate-fail", replace_edited=True)
            self.wait_idle()
        task = self.service.get_task("abcdefghijk")
        self.assertEqual((task["title_zh"], task["desc_zh"]), ("", ""))
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["translation"]["state"], "failed")
        self.assertFalse(self.uploads)

    def test_translation_job_returns_immediately_and_is_idempotent(self):
        from screator.translation.types import TranslationResult
        started, release=threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            started.set(); release.wait(3)
            return TranslationResult("试译", "正文", "local_llm")
        with patch("screator.translation.service.translate", side_effect=slow):
            first=self.service.translation_test("local_llm", "same-operation")
            self.assertTrue(started.wait(1))
            second=self.service.translation_test("local_llm", "same-operation")
            self.assertEqual(first,second)
            self.service.translation_jobs.cancel(first["job_id"])
            release.set()
            until=time.monotonic()+3
            while self.service.translation_jobs.active() and time.monotonic()<until:time.sleep(.01)
            self.assertEqual(self.service.translation_jobs.get(first["job_id"])["state"],"cancelled")

    def test_translation_uninstall_uses_background_job_and_updates_status(self):
        root = self.paths.root / "translation"
        models = root / "models"
        models.mkdir(parents=True)
        (models / "fixture").write_bytes(b"model")
        (root / "deployment.json").write_text("{}")
        job = self.service.dispatch("translation.uninstall", {"operation_id": "uninstall-model-1"})
        self.wait_until(lambda: not self.service.translation_jobs.active(), "model uninstall")
        self.assertEqual(self.service.translation_jobs.get(job["job_id"])["state"], "complete")
        self.assertEqual(self.service.translation_status()["local"]["state"], "missing")
        self.assertFalse(models.exists())
        self.assertEqual(job, self.service.translation_uninstall("uninstall-model-1"))
        self.service.config.values["local_llm_mode"] = "external"
        with self.assertRaises(AppError):
            self.service.translation_uninstall("uninstall-model-2")

    def test_configuration_and_edit_are_blocked_for_active_task(self):
        started, release = threading.Event(), threading.Event()
        def download(*args, **kwargs):
            started.set(); release.wait(3); return self.download(*args, **kwargs)
        with self.mocks(), patch.object(youtube, "download_video", side_effect=download):
            try:
                self.create(); self.assertTrue(started.wait(2))
                with self.assertRaises(AppError): self.service.update_settings({"bili_tid": 12})
                with self.assertRaises(AppError): self.service.update_metadata("abcdefghijk", "new", "desc")
            finally:
                release.set()
            self.wait_idle()

    def test_unknown_method_and_bad_parameters_fail_closed(self):
        with self.assertRaises(AppError): self.service.dispatch("shell.execute", {})
        with self.assertRaises(AppError): self.service.dispatch("tasks.list", [])
        with self.assertRaises(AppError): self.service.list_tasks(limit=21)
        with self.assertRaises(AppError): self.service.list_tasks(limit=100000)

    def test_task_and_history_pages_advance_by_twenty(self):
        for index in range(45):
            video_id = f"page{index:07d}"
            self.service.store.upsert(Task(video_id, f"https://youtu.be/{video_id}", "submitted"))
        for history in (False, True):
            pages = [self.service.list_tasks(offset=offset, history=history) for offset in (0, 20, 40)]
            self.assertEqual([len(page["items"]) for page in pages], [20, 20, 5])
            self.assertEqual([page["total"] for page in pages], [45, 45, 45])
            ids = [item["task_id"] for page in pages for item in page["items"]]
            self.assertEqual(len(set(ids)), 45)


class ContractTests(unittest.TestCase):
    def test_worker_keeps_file_log_when_desktop_ipc_fails(self):
        saved = []
        class BrokenService:
            def add_log(self, entry): raise BrokenPipeError("desktop disconnected")
        class FileHandler:
            def emit(self, record): saved.append(record.getMessage())
        handler = DesktopLogHandler(BrokenService(), FileHandler())
        record = logging.LogRecord("worker", logging.ERROR, "", 0, "last diagnostic", (), None)
        with patch.object(handler, "handleError"):
            handler.emit(record)
        self.assertEqual(saved, ["last diagnostic"])

    def test_parse_urls_validates_host_and_deduplicates_video_id(self):
        items = parse_urls("https://www.youtube.com/watch?v=-abcdefghij&t=10")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "-abcdefghij")
        for invalid in ("file:///tmp/abcdefghijk", "https://youtube.com.evil.test/watch?v=abcdefghijk", "https://youtube.com/playlist?list=123"):
            with self.assertRaises(AppError): parse_urls(invalid)

    def test_protocol_success_error_and_invalid_version(self):
        class Service:
            def dispatch(self, method, params):
                if method == "fail": raise AppError("safe message")
                return {"ok": True}
        stream = io.StringIO()
        protocol = Protocol(stream)
        for method in ("ok", "fail"):
            protocol.handle(Service(), {"protocol_version": 2, "request_id": method, "method": method})
        protocol.handle(Service(), {"protocol_version": 1, "request_id": "old"})
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertTrue(lines[0]["result"]["ok"])
        self.assertEqual(lines[1]["error"]["message"], "safe message")
        self.assertIn("error", lines[2])

    def test_protocol_concurrent_writes_remain_json_lines(self):
        from concurrent.futures import ThreadPoolExecutor
        stream = io.StringIO(); protocol = Protocol(stream)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: protocol.emit("progress", {"n": n, "text": "中文\n分行"}), range(100)))
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({r["event_id"] for r in rows}), 100)

    def test_migration_backs_up_legacy_and_rejects_future(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE original (value TEXT)"); conn.commit()
            store = TaskStore(path); store.close()
            self.assertTrue(Path(str(path) + ".pre-desktop.bak").is_file())
            with closing(sqlite3.connect(path)) as conn: conn.execute("PRAGMA user_version=99")
            with self.assertRaises(AppError): TaskStore(path)

    def test_startup_recovers_states_without_restarting_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = AppPaths.default(folder, folder)
            store = TaskStore(paths.root / "data/tasks.sqlite")
            store.upsert(Task("abcdefghijk", "url", "uploading"))
            store.upsert(Task("12345678901", "url", "downloading"))
            store.upsert(Task("cancelled01", "url", "cancel_requested"))
            for video_id, state in (("abcdefghijk", "uploading_media"), ("cancelled01", "queued")):
                task = store.require(video_id)
                publications.ensure_bili(store, task)
                pub = publications.for_platform(store, task.task_id, "bilibili")
                publications.change(store, pub["publication_id"], status=state)
            store.update("cancelled01", cancel_requested=1)
            store.close()
            service = DesktopService(paths, lambda *args: None, MemoryVault())
            try:
                self.assertEqual(service.task("abcdefghijk").status, "submission_unknown")
                self.assertEqual(service.task("12345678901").status, "interrupted")
                self.assertEqual(service.task("cancelled01").status, "cancelled")
                self.assertEqual(publications.for_platform(service.store, service.task("abcdefghijk").task_id, "bilibili")["status"], "submission_unknown")
                self.assertEqual(publications.for_platform(service.store, service.task("cancelled01").task_id, "bilibili")["status"], "cancelled")
                self.assertFalse(service.scheduler.snapshot()["active"])
            finally: service.close()

    def test_restart_preserves_completed_multi_target_order(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = AppPaths.default(folder, folder)
            store = TaskStore(paths.root / "data/tasks.sqlite")
            cases = (
                ("abcdefghijk", "failed", "partial_success", "2026-01-01T00:00:00.000+00:00"),
                ("lmnopqrstuv", "abandoned", "completed_with_abandon", "2026-01-02T00:00:00.000+00:00"),
            )
            for video_id, target_status, parent_status, _ in cases:
                store.upsert(Task(video_id, "https://youtu.be/" + video_id, "ready"))
                task = store.require(video_id)
                publications.ensure_bili(store, task)
                bili = publications.for_platform(store, task.task_id, "bilibili")
                publications.change(store, bili["publication_id"], status="submitted")
                with store.transaction() as db:
                    db.execute("""INSERT INTO task_publications
                        (publication_id,task_id,platform,source_video_id,status,error)
                        VALUES(?,?,'douyin',?,?,?)""",
                        ("douyin-" + video_id, task.task_id, video_id, target_status, "旧错误"))
                publications.project(store, task.task_id)
                self.assertEqual(store.require(task.task_id).status, parent_status)
            store.upsert(Task("wxyz1234567", "https://youtu.be/wxyz1234567", "submitted"))
            timestamps = {video_id: updated_at for video_id, _, _, updated_at in cases}
            timestamps["wxyz1234567"] = "2026-01-03T00:00:00.000+00:00"
            with store.transaction() as db:
                for video_id, updated_at in timestamps.items():
                    db.execute("UPDATE tasks SET updated_at=? WHERE video_id=?", (updated_at, video_id))
            revisions = {task.video_id: task.revision for task in store.list_all()}
            store.close()

            for _ in range(2):
                service = DesktopService(paths, lambda *args: None, MemoryVault())
                try:
                    listed = service.list_tasks()["items"]
                    self.assertEqual([task["video_id"] for task in listed],
                                     ["wxyz1234567", "lmnopqrstuv", "abcdefghijk"])
                    for video_id, _, parent_status, _ in cases:
                        task = service.task(video_id)
                        self.assertEqual(task.status, parent_status)
                        self.assertEqual(task.updated_at, timestamps[video_id])
                        self.assertEqual(task.revision, revisions[video_id])
                finally:
                    service.close()

    def test_file_lock_rejects_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "task.lock"
            with FileLock(path):
                with self.assertRaises(AppError):
                    with FileLock(path): pass
            with FileLock(path): pass

    def test_cancelled_qr_response_cannot_replace_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "cookie.json"
            session = LoginSession(destination, lambda *args: None)
            session.cancel()
            session.call = lambda *args: {"code": 0, "data": {"url": "https://example.com", "auth_code": "fixture"}}
            session._run()
            self.assertFalse(destination.exists())

    def test_qr_success_stores_biliup_schema_without_emitting_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "cookie.json"
            emitted = []
            session = LoginSession(destination, lambda *args: emitted.append(args))
            responses = [{"code": 0, "data": {"url": "https://example.com", "auth_code": "private-code"}},
                         {"code": 0, "data": login_fixture()}]
            with patch.object(session, "call", side_effect=responses), patch.object(session.cancelled, "wait", return_value=False):
                session._run()
            info = validate_login(json.loads(destination.read_text(encoding="utf-8")))
            self.assertEqual(info["platform"], "BiliTV")
            self.assertEqual(emitted[-1][1]["status"], "success")
            self.assertNotIn("private-code", json.dumps(emitted))
            self.assertNotIn("access_token", json.dumps(emitted))

    def test_expired_qr_does_not_store_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "cookie.json"
            emitted = []
            session = LoginSession(destination, lambda *args: emitted.append(args))
            responses = [{"code": 0, "data": {"url": "https://example.com", "auth_code": "fixture"}}, {"code": 86038}]
            with patch.object(session, "call", side_effect=responses), patch.object(session.cancelled, "wait", return_value=False):
                session._run()
            self.assertFalse(destination.exists())
            self.assertEqual(emitted[-1][1]["status"], "expired")

    def test_credentials_validation_and_log_redaction(self):
        self.assertEqual(validate_login(login_fixture())["token_info"]["mid"], 123)
        with self.assertRaises(AppError): validate_login({"cookie_info": {}})
        self.assertNotIn("secret", redact('SESSDATA=secret'))
        self.assertNotIn("secret", redact('{"access_token":"secret"}'))


if __name__ == "__main__": unittest.main()
