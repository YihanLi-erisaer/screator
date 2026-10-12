from __future__ import annotations

import json
import sqlite3
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import test_desktop as fixtures
from screator import image_posts, publications, history_transfer
from screator.db import Task, TaskStore, SCHEMA_VERSION
from screator.desktop_service import DesktopService
from screator.exceptions import AppError
from screator.xiaohongshu import AuthRequired, RateLimited, Rejected, UnknownResult, validate_metadata
from screator.xiaohongshu_browser import receipt

USER = "0123456789abcdef01234567"
NOTE = "abcdef0123456789abcdef01"
COOKIES = [{"name": "web_session", "value": "secret-xhs-session", "domain": ".xiaohongshu.com", "path": "/"}]


class FakeBrowser:
    def __init__(self):
        self.calls = []
        self.mode = "success"
        self.user = USER
        self.auth_error = None
        self.started = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def identity(self, cookies):
        if self.auth_error: raise self.auth_error
        if not cookies: raise AuthRequired("未登录")
        return self.user, "小红书测试账号"

    def start(self): self.started = True
    def poll(self): return {"status": "done", "cookies": COOKIES}
    def cancel(self): self.started = False
    def close(self): self.cancel()

    def publish(self, cookies, paths, snapshot, creating, cancel, progress):
        self.calls.append({"paths": list(paths), "snapshot": dict(snapshot)})
        if self.mode == "upload_error": raise AppError("图片上传失败")
        if self.mode == "auth_error": raise AuthRequired("登录失效")
        if self.mode == "rate_error": raise RateLimited("平台限流")
        creating()
        if self.mode == "timeout": raise UnknownResult("发布响应超时")
        if self.mode == "reject": raise Rejected("平台明确拒绝")
        if self.mode == "cancel_after_click": cancel.set()
        if self.mode == "block":
            self.entered.set()
            self.release.wait(10)
        return NOTE


class XiaohongshuTests(unittest.TestCase):
    wait_until = fixtures.DesktopTests.wait_until
    wait_idle = fixtures.DesktopTests.wait_idle

    def setUp(self):
        fixtures.DesktopTests.setUp(self)
        self.browser = FakeBrowser()
        self.service.xiaohongshu.client = self.browser
        self.service.dispatch("xiaohongshu.auth.start", {})
        self.assertEqual(self.service.dispatch("xiaohongshu.auth.poll", {}), {"status": "done"})
        self.xhs = self.service.xiaohongshu.account()
        path = Path(self.tmp.name) / "image.png"
        Image.new("RGB", (80, 64), "blue").save(path)
        self.images = [str(path)]

    def create(self, sync=True, **extra):
        params = {"paths": self.images, "text": "正文第一行\n第二行", "account_id": self.account["account_id"], "operation_id": "create-xhs-image"}
        if sync: params.update(sync_xiaohongshu=True, xiaohongshu_account_id=self.xhs["account_id"], xiaohongshu_binding_revision=self.xhs["binding_revision"], xiaohongshu_title="小红书标题")
        params.update(extra)
        result = self.service.dispatch("image_posts.create", params)
        self.wait_idle()
        return result["task_id"]

    def pub(self, task_id): return publications.for_platform(self.service.store, task_id, "xiaohongshu")

    def submit(self, task_id, targets=None, op="submit-xhs-image"):
        self.service.submit(task_id, op, targets=targets)
        self.wait_idle()

    def bili(self):
        return patch.object(image_posts, "upload", return_value="1234567890123456789")

    def verify_bili(self):
        return patch("screator.desktop_auth.verify_credentials", return_value={"uid": "123", "nickname": "Test"})

    def test_opt_out_keeps_original_bilibili_only_flow(self):
        self.browser.auth_error = AppError("离线")
        task_id = self.create(sync=False, text="长正文" * 400)
        self.assertEqual([p["platform"] for p in publications.items(self.service.store, task_id)], ["bilibili"])
        self.assertEqual(self.browser.calls, [])

    def test_preview_edits_and_confirmation_publish_shared_images_with_independent_title(self):
        task_id = self.create()
        p = self.pub(task_id)
        self.assertEqual(p["status"], "ready")
        self.assertEqual(self.browser.calls, [])
        self.service.update_publication(p["publication_id"], revision=p["revision"], title="修改后的小红书标题")
        self.service.update_metadata(task_id, "B站图文标题", "编辑后的共用正文\n第二行 🌸")
        with self.verify_bili(), self.bili() as bili:
            self.submit(task_id)
        self.assertEqual(bili.call_count, 1)
        self.assertEqual(self.service.task(task_id).status, "submitted")
        call = self.browser.calls[0]
        self.assertEqual(call["snapshot"]["title"], "修改后的小红书标题")
        self.assertEqual(call["snapshot"]["description"], "编辑后的共用正文\n第二行 🌸")
        self.assertEqual(call["paths"], image_posts.prepared_paths(self.service.task(task_id)))
        self.assertEqual(self.pub(task_id)["remote_id"], NOTE)
        self.assertTrue(call["paths"][0].is_file())

    def test_auto_mode_publishes_both_and_operation_is_idempotent(self):
        with self.verify_bili(), self.bili() as bili:
            task_id = self.create(mode="auto")
            duplicate = self.create(mode="auto")
        self.assertEqual(task_id, duplicate)
        self.assertEqual(bili.call_count, 1)
        self.assertEqual(len(self.browser.calls), 1)
        with self.assertRaises(AppError): self.create(mode="auto", xiaohongshu_title="不同标题")

    def test_uncertain_bilibili_dynamic_accepts_numeric_receipt_in_multi_platform_task(self):
        task_id = self.create()
        def uncertain(task, info, on_started=None):
            on_started(None)
            raise AppError("动态创建响应超时")
        with self.verify_bili(), patch.object(image_posts, "upload", side_effect=uncertain): self.submit(task_id)
        bili = publications.for_platform(self.service.store, task_id, "bilibili")
        self.assertEqual(bili["status"], "submission_unknown")
        self.assertEqual(self.pub(task_id)["status"], "submitted")
        self.service.resolve_publication(bili["publication_id"], "1234567890123456789")
        self.assertEqual(self.service.task(task_id).bv_id, "1234567890123456789")
        self.assertEqual(self.service.task(task_id).status, "submitted")

    def test_one_platform_failure_retries_only_that_platform(self):
        task_id = self.create()
        self.browser.mode = "upload_error"
        with self.verify_bili(), self.bili() as bili:
            self.submit(task_id)
            self.assertEqual(self.service.task(task_id).status, "partial_success")
            p = self.pub(task_id)
            self.assertEqual(p["status"], "failed")
            self.service.retry_publication(p["publication_id"], "retry-xhs-image")
            self.browser.mode = "success"
            self.submit(task_id, targets=[p["publication_id"]], op="submit-xhs-retry")
        self.assertEqual(bili.call_count, 1)
        self.assertEqual(len(self.browser.calls), 2)
        self.assertEqual(self.service.task(task_id).status, "submitted")

    def test_uncertain_submission_requires_resolution_before_retry(self):
        task_id = self.create()
        self.browser.mode = "timeout"
        with self.verify_bili(), self.bili() as bili:
            self.submit(task_id)
            p = self.pub(task_id)
            self.assertEqual(p["status"], "submission_unknown")
            with self.assertRaises(AppError): self.service.retry_publication(p["publication_id"], "unsafe-xhs-retry")
            with self.assertRaises(AppError): self.service.abandon_publication(p["publication_id"])
            with self.assertRaises(AppError): self.service.resolve_publication(p["publication_id"], "invalid-id")
            self.service.resolve_publication(p["publication_id"], not_submitted=True)
            self.service.retry_publication(p["publication_id"], "verified-xhs-retry")
            self.browser.mode = "success"
            self.submit(task_id, targets=[p["publication_id"]], op="verified-xhs-submit")
        self.assertEqual(bili.call_count, 1)
        self.assertEqual(self.pub(task_id)["remote_id"], NOTE)

    def test_auth_and_rate_limits_pause_only_xiaohongshu_until_explicit_recovery(self):
        for mode, reason in (("auth_error", "auth_required"), ("rate_error", "rate_limited")):
            with self.subTest(mode=mode):
                self.xhs = self.service.xiaohongshu.account()
                task_id = self.create(operation_id="create-xhs-" + mode)
                self.browser.mode = mode
                with self.verify_bili(), self.bili() as bili:
                    self.service.submit(task_id, "submit-xhs-" + mode)
                    self.wait_until(lambda: self.pub(task_id)["status"] == "waiting" and
                                    publications.for_platform(self.service.store, task_id, "bilibili")["status"] == "submitted", "independent XHS wait")
                    self.assertEqual(self.service.scheduler.snapshot()["xiaohongshu"]["wait_reason"], reason)
                    self.browser.mode = "success"
                    if reason == "auth_required":
                        self.service.xiaohongshu.start()
                        self.service.xiaohongshu.poll()
                    else:
                        self.service.xiaohongshu.resume()
                    self.wait_idle()
                self.assertEqual(bili.call_count, 1)
                self.assertEqual(self.pub(task_id)["status"], "submitted")

    def test_explicit_rejection_can_retry_and_cancel_after_click_keeps_receipt(self):
        task_id = self.create()
        self.browser.mode = "reject"
        with self.verify_bili(), self.bili(): self.submit(task_id)
        p = self.pub(task_id)
        self.assertEqual(p["status"], "failed")
        self.service.retry_publication(p["publication_id"], "rejected-xhs-retry")
        self.browser.mode = "cancel_after_click"
        self.submit(task_id, targets=[p["publication_id"]], op="cancel-xhs-submit")
        self.assertEqual(self.pub(task_id)["status"], "submitted")

    def test_account_and_format_validation_rejects_before_task_creation(self):
        for params in ({"sync_xiaohongshu": "true"}, {"xiaohongshu_title": ""}, {"xiaohongshu_title": "🌸" * 11},
                       {"text": "正文" * 501}, {"xiaohongshu_account_id": "another-account"}, {"xiaohongshu_binding_revision": True}):
            with self.subTest(params=params), self.assertRaises(AppError): self.create(**params)
        gif = Path(self.tmp.name) / "animated.gif"
        Image.new("RGB", (40, 40), "red").save(gif)
        with self.assertRaises(AppError): self.create(paths=[str(gif)])
        self.assertEqual(self.service.store.list_all(), [])

    def test_credentials_never_enter_status_task_history_or_notifications(self):
        task_id = self.create()
        with self.verify_bili(), self.bili(): self.submit(task_id)
        public = json.dumps({"status": self.service.dispatch("xiaohongshu.auth.status", {}),
                             "task": self.service.get_task(task_id), "history": history_transfer.all_records(self.service.store),
                             "events": self.notifications}, ensure_ascii=False)
        self.assertNotIn("secret-xhs-session", public)
        self.assertNotIn("web_session", public)
        target = Path(self.tmp.name) / "history.json"
        history_transfer.export_file(self.service.store, target)
        self.assertEqual(history_transfer._validated_record(json.loads(target.read_text())["records"][0])["publications"][1]["platform"], "xiaohongshu")
        rows = self.service.data_center.list(platform="xiaohongshu")["items"]
        self.assertEqual(rows[0]["account_name"], "小红书测试账号")
        self.service.delete_history(task_id, self.service.task(task_id).revision, confirmed=True)
        self.assertIsNone(self.service.store._conn.execute("SELECT 1 FROM xiaohongshu_attempts").fetchone())

    def test_logout_blocked_while_publishing_and_archive_preserves_unresolved_targets(self):
        task_id = self.create()
        with self.assertRaises(AppError): self.service.xiaohongshu.archive()
        self.browser.mode = "block"
        with self.verify_bili(), self.bili():
            self.service.submit(task_id, "blocking-xhs-submit")
            self.assertTrue(self.browser.entered.wait(5))
            try:
                with self.assertRaises(AppError): self.service.xiaohongshu.clear()
            finally: self.browser.release.set()
            self.wait_idle()
        self.service.xiaohongshu.archive()
        self.assertIsNone(self.service.xiaohongshu.account())
        self.assertEqual(self.pub(task_id)["remote_id"], NOTE)

    def test_restart_recovers_creation_intent_without_resending(self):
        task_id = self.create()
        p = self.pub(task_id)
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO xiaohongshu_attempts(publication_id,phase,updated_at) VALUES(?,'creating','fixture')", (p["publication_id"],))
            publications.change(self.service.store, p["publication_id"], status="uploading_media")
            self.service.store.update(task_id, status="uploading")
        vault = self.service.config.vault
        self.service.close()
        self.service = DesktopService(self.paths, lambda *_: None, vault)
        self.addCleanup(self.service.close)
        self.assertEqual(self.pub(task_id)["status"], "submission_unknown")
        self.assertEqual(self.service.scheduler.snapshot()["active"], [])
        self.service.resolve_publication(p["publication_id"], NOTE)
        self.assertEqual(self.pub(task_id)["status"], "submitted")

    def test_v10_upgrade_preserves_other_platform_receipts_attempts_and_identity(self):
        path = Path(self.tmp.name) / "legacy-v10.sqlite"
        old = TaskStore(path)
        task = Task("legacyvideo", "", "ready", task_id=str(uuid.uuid4()))
        old.upsert(task)
        publications.ensure_bili(old, task)
        for platform in ("acfun", "youtube", "twitter"):
            old._conn.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status,remote_id) VALUES(?,?,?,'legacyvideo','submitted','receipt')", (platform, task.task_id, platform))
        old._conn.execute("INSERT INTO acfun_attempts(attempt_id,publication_id,phase,started_at) VALUES('ac-attempt','acfun','submitted','fixture')")
        for table, platform in (("youtube_attempts", "youtube"), ("social_upload_attempts", "twitter")):
            old._conn.execute(f"INSERT INTO {table}(publication_id,phase,media_sha256,request_sha256,updated_at) VALUES(?,'submitted','media','request','fixture')", (platform,))
        # Model the previous release's constraint and absence of XHS tables.
        old._conn.execute("PRAGMA foreign_keys=OFF")
        schema = old._conn.execute("SELECT sql FROM sqlite_master WHERE name='task_publications'").fetchone()[0]
        objects = list(old._conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='task_publications' AND type IN ('index','trigger') AND sql IS NOT NULL AND name!='uq_xiaohongshu_source'"))
        old._conn.execute(schema.replace('"task_publications"', 'legacy_publications').replace(",'xiaohongshu'", ""))
        old._conn.execute("INSERT INTO legacy_publications SELECT * FROM task_publications")
        old._conn.execute("DROP TABLE task_publications")
        old._conn.execute("ALTER TABLE legacy_publications RENAME TO task_publications")
        for (sql,) in objects: old._conn.execute(sql)
        old._conn.execute("DROP TABLE xiaohongshu_attempts")
        old._conn.execute("DROP TABLE xiaohongshu_accounts")
        old._conn.execute("PRAGMA user_version=10")
        old.close()
        migrated = TaskStore(path)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertTrue(Path(str(path) + ".pre-v11.bak").exists())
        self.assertIsNone(migrated._conn.execute("PRAGMA foreign_key_check").fetchone())
        for table in ("acfun_attempts", "youtube_attempts", "social_upload_attempts"):
            self.assertEqual(migrated._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 1)
        self.assertEqual({p["platform"]: p["remote_id"] for p in publications.items(migrated, task.task_id) if p["remote_id"]}, {"acfun": "receipt", "youtube": "receipt", "twitter": "receipt"})
        with self.assertRaises(sqlite3.IntegrityError):
            migrated._conn.execute("UPDATE task_publications SET platform='xiaohongshu' WHERE publication_id='acfun'")


class XiaohongshuReceiptTests(unittest.TestCase):
    def test_only_explicit_success_with_valid_id_counts_as_submitted(self):
        self.assertEqual(receipt({"success": True, "data": {"noteId": NOTE}}), NOTE)
        for body in ({"success": True}, {"data": {"id": NOTE}}, {"code": False, "data": {"id": NOTE}}, {"success": True, "data": {"id": "invalid"}}, []):
            with self.subTest(body=body), self.assertRaises(UnknownResult): receipt(body)
        with self.assertRaises(Rejected): receipt({"success": False, "msg": "secret-token"})

    def test_utf16_limits_match_desktop_counters(self):
        validate_metadata({"title": "🌸" * 10, "description": "🌸" * 500})
        for snapshot in ({"title": "🌸" * 11, "description": "正文"}, {"title": "标题", "description": "🌸" * 501}):
            with self.assertRaises(AppError): validate_metadata(snapshot)
