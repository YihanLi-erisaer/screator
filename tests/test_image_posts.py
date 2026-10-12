from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

import test_desktop as fixtures
from screator import bili_upload, desktop_auth, events, history_transfer, image_posts, media, publications
from screator.db import SCHEMA_VERSION, Task, TaskStore
from screator.desktop_service import DesktopService
from screator.exceptions import AppError


class ImagePostTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    wait_until = fixtures.DesktopTests.wait_until
    wait_idle = fixtures.DesktopTests.wait_idle

    def images(self, count=2):
        result = []
        for index in range(count):
            path = Path(self.tmp.name) / f"photo-{index}.png"
            Image.new("RGB", (64 + index, 48), "red").save(path)
            result.append(str(path))
        return result

    def create(self, paths=None, **extra):
        return self.service.dispatch("image_posts.create", {
            "paths": paths or self.images(), "text": "我的图文\n保留换行和表情 🌸", "account_id": self.account["account_id"],
            "operation_id": "image-post-create", **extra})

    def verification(self):
        return patch.object(desktop_auth, "verify_credentials", side_effect=lambda info: {
            "uid": desktop_auth.credential_identity(info), "nickname": "Test"})

    def session(self, create_response=None, upload_failure=None):
        session = MagicMock()
        session.__enter__.return_value = session
        def post(url, **kwargs):
            if "upload_bfs" in url:
                if upload_failure: raise upload_failure
                # Verify multipart content and credentials without contacting Bilibili.
                self.assertEqual(kwargs["data"]["csrf"], "fixture")
                self.assertEqual(kwargs["files"]["file_up"][2], "image/png")
                self.assertTrue(kwargs["files"]["file_up"][1].read().startswith(b"\x89PNG"))
                return self.response({"code": 0, "data": {"image_url": "https://i0.hdslb.com/bfs/new_dyn/test.png", "image_width": 64, "image_height": 48}})
            if isinstance(create_response, Exception): raise create_response
            return self.response(create_response or {"code": 0, "data": {"dyn_id_str": "1234567890123456789"}})
        session.post.side_effect = post
        return session

    def response(self, body):
        response = MagicMock()
        response.json.return_value = body
        return response

    def test_preview_prepares_in_order_without_video_tools_then_submit_uses_edited_body(self):
        paths = self.images()
        with patch.object(bili_upload, "renew") as renew, patch.object(media, "require_ffmpeg") as ffmpeg:
            created = self.create(paths=list(reversed(paths)))
            self.wait_idle()
            task = self.service.task(created["task_id"])
            self.assertEqual(task.status, "ready", task.error)
            outputs = image_posts.prepared_paths(task)
            self.assertEqual(outputs[0].read_bytes(), Path(paths[1]).read_bytes())
            self.assertEqual(outputs[1].read_bytes(), Path(paths[0]).read_bytes())
            self.assertEqual(len(self.service.dispatch("image_posts.images", {"task_id": task.task_id})["items"]), 2)
            renew.assert_not_called()
            ffmpeg.assert_not_called()
            self.service.update_metadata(task.task_id, "修改后的正文", "修改后的正文\n🌸")
            session = self.session()
            with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
                self.service.submit(task.task_id, "submit-image-post")
                self.wait_idle()
            task = self.service.task(task.task_id)
            self.assertEqual((task.status, task.bv_id), ("submitted", "1234567890123456789"), task.error)
            self.assertTrue(Path(task.work_dir).is_dir())
            self.assertTrue(all(Path(path).is_file() for path in paths))
            payload = session.post.call_args.kwargs["json"]["dyn_req"]
            self.assertEqual(payload["content"]["contents"][0]["raw_text"], "修改后的正文\n🌸")
            self.assertEqual((payload["scene"], len(payload["pics"])), (2, 2))
            pub = publications.for_platform(self.service.store, task.task_id, "bilibili")
            self.assertEqual((pub["status"], pub["remote_id"]), ("submitted", task.bv_id))
            renew.assert_not_called()

    def test_auto_publish_and_operation_idempotence(self):
        paths = self.images()
        session = self.session()
        with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
            first = self.create(paths, mode="auto")
            self.assertEqual(self.create(paths, mode="auto"), first)
            self.wait_idle()
        self.assertEqual(self.service.task(first["task_id"]).status, "submitted")
        self.assertEqual(sum("create/dyn" in call.args[0] for call in session.post.call_args_list), 1)
        self.assertEqual(len(self.service.list_tasks(history=True)["items"]), 1)

    def test_invalid_inputs_are_rejected_before_creating_tasks(self):
        paths = self.images()
        invalid = [[], paths * 5, [paths[0], paths[0]], ["relative.png"], [str(Path(self.tmp.name) / "missing.png")], [None]]
        for selected in invalid:
            with self.subTest(selected=selected), self.assertRaises(AppError):
                self.service.create_image_post(selected, "正文", self.account["account_id"], "invalid-image-create")
        for text in ("", " \n ", "x" * 2001, None):
            with self.subTest(text=str(text)[:10]), self.assertRaises(AppError):
                self.service.create_image_post(paths, text, self.account["account_id"], "invalid-image-create")
        corrupt = Path(self.tmp.name) / "corrupt.png"
        corrupt.write_bytes(b"not an image")
        with self.assertRaises(AppError): self.service.inspect_images([str(corrupt)])
        Image.new("RGB", (10, 10)).save(corrupt.with_suffix(".webp"))
        with self.assertRaises(AppError): self.service.inspect_images([str(corrupt.with_suffix(".webp"))])
        corrupt.write_bytes(b"x" * (image_posts.MAX_BYTES + 1))
        with self.assertRaises(AppError): self.service.inspect_images([str(corrupt)])
        self.assertEqual(self.service.store.list_all(), [])

    def test_upload_failure_can_retry_but_create_timeout_requires_resolution(self):
        session = self.session(upload_failure=requests.Timeout())
        with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
            created = self.create(mode="auto")
            self.wait_idle()
        task = self.service.task(created["task_id"])
        self.assertEqual(task.status, "failed", task.error)
        self.assertFalse(any("create/dyn" in call.args[0] for call in session.post.call_args_list))
        self.service.retry(task.task_id, "retry-image-post")
        self.wait_idle()
        session = self.session(create_response=requests.Timeout())
        with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
            self.service.submit(task.task_id, "timeout-image-post")
            self.wait_idle()
        task = self.service.task(task.task_id)
        self.assertEqual(task.status, "submission_unknown", task.error)
        with self.assertRaises(AppError): self.service.retry(task.task_id, "unsafe-image-retry")
        self.service.resolve(task.task_id, bv_id="1234567890123456789")
        self.assertEqual(self.service.task(task.task_id).status, "submitted")
        self.assertTrue(Path(task.work_dir).exists())

    def test_explicit_rejection_is_failed_and_missing_receipt_is_unknown(self):
        for index, response in enumerate(({"code": -101, "message": "secret=fixture"}, {"code": 0, "data": {}})):
            session = self.session(create_response=response)
            with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
                created = self.create(mode="auto", operation_id=f"image-outcome-{index}")
                self.wait_idle()
            task = self.service.task(created["task_id"])
            self.assertEqual(task.status, "failed" if index == 0 else "submission_unknown", task.error)
            self.assertNotIn("fixture", task.error)

    def test_cancel_before_create_does_not_publish(self):
        created = self.create()
        self.wait_idle()
        task = self.service.task(created["task_id"])
        cancel = threading.Event()
        session = self.session()
        post = session.post.side_effect
        def cancel_upload(*args, **kwargs):
            result = post(*args, **kwargs)
            cancel.set()
            return result
        session.post.side_effect = cancel_upload
        started = MagicMock()
        with patch.object(image_posts.requests, "Session", return_value=session), events.task_context(None, cancel, lambda *_: None):
            with self.assertRaises(events.Cancelled): image_posts.upload(task, fixtures.login_fixture(), started)
        started.assert_not_called()
        self.assertEqual(session.post.call_count, 1)

    def test_managed_images_cannot_escape_task_directory(self):
        created = self.create()
        self.wait_idle()
        task = self.service.task(created["task_id"])
        task.image_paths = json.dumps(self.images())
        with self.assertRaisesRegex(AppError, "本任务目录"): image_posts.prepared_paths(task)

    def test_ready_post_survives_restart_and_source_removal(self):
        paths = self.images()
        created = self.create(paths)
        self.wait_idle()
        self.service.close()
        self.service = DesktopService(self.paths, lambda *_: None, fixtures.MemoryVault())
        self.addCleanup(self.service.close)
        for path in paths: Path(path).unlink()
        task = self.service.task(created["task_id"])
        self.assertEqual(task.status, "ready")
        session = self.session()
        with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
            self.service.submit(task.task_id, "restart-image-post")
            self.wait_idle()
        task = self.service.task(task.task_id)
        self.assertEqual(task.status, "submitted", task.error)
        self.assertTrue(image_posts.prepared_paths(task))

    def test_cancel_after_create_is_sent_keeps_successful_receipt(self):
        created = self.create()
        self.wait_idle()
        task = self.service.task(created["task_id"])
        cancel = threading.Event()
        session = self.session()
        post = session.post.side_effect
        def cancel_create(url, **kwargs):
            response = post(url, **kwargs)
            if "create/dyn" in url: cancel.set()
            return response
        session.post.side_effect = cancel_create
        with patch.object(image_posts.requests, "Session", return_value=session), events.task_context(None, cancel, lambda *_: None):
            self.assertEqual(image_posts.upload(task, fixtures.login_fixture()), "1234567890123456789")

    def test_image_history_export_import_and_schema_seven_migration(self):
        session = self.session()
        with self.verification(), patch.object(image_posts.requests, "Session", return_value=session):
            created = self.create(mode="auto")
            self.wait_idle()
        history = Path(self.tmp.name) / "image-history.json"
        history_transfer.export_file(self.service.store, history)
        new_path = Path(self.tmp.name) / "new.sqlite"
        store = TaskStore(new_path)
        self.addCleanup(store.close)
        history_transfer.initialize(store)
        self.assertEqual(history_transfer.import_file(store, history)["imported"], 1)
        imported = history_transfer.list_imported(store)[0]
        self.assertEqual((imported["source_kind"], imported["bv_id"]), ("image_post", "1234567890123456789"))
        original = self.service.task(created["task_id"])
        migration_path = Path(self.tmp.name) / "v7.sqlite"
        migration = TaskStore(migration_path)
        legacy = Task("local-existing", "", "ready", source_kind="local", local_cover_path="/user/cover.png")
        migration.upsert(legacy)
        migration._conn.execute("ALTER TABLE tasks DROP COLUMN source_image_paths")
        migration._conn.execute("ALTER TABLE tasks DROP COLUMN image_paths")
        migration._conn.execute("PRAGMA user_version=7")
        migration.close()
        migration = TaskStore(migration_path)
        self.addCleanup(migration.close)
        self.assertEqual(migration.require(legacy.task_id).local_cover_path, "/user/cover.png")
        self.assertEqual(migration.require(legacy.task_id).image_paths, "[]")
        self.assertEqual(migration._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertTrue(Path(str(migration_path) + ".pre-v8.bak").is_file())
        self.assertTrue(original.work_dir)


if __name__ == "__main__":
    unittest.main()
