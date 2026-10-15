"""Video synchronization through the real scheduler with isolated publishers."""
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_desktop as fixtures
import test_xiaohongshu as xhs_fixtures
from test_xiaohongshu import NOTE
from screator import media, pipeline, publications
from screator.exceptions import AppError


class VideoSyncTests(unittest.TestCase):
    setUp = xhs_fixtures.XiaohongshuTests.setUp
    wait_until = fixtures.DesktopTests.wait_until
    wait_idle = fixtures.DesktopTests.wait_idle
    meta = fixtures.DesktopTests.meta
    download = fixtures.DesktopTests.download
    prepare = fixtures.DesktopTests.prepare
    upload = fixtures.DesktopTests.upload
    mocks = fixtures.DesktopTests.mocks
    pub = xhs_fixtures.XiaohongshuTests.pub

    def create(self, local=True, **extra):
        params = dict(url="https://www.youtube.com/watch?v=abcdefghijk", operation_id=uuid.uuid4().hex,
                      account_id=self.account["account_id"], sync_xiaohongshu=True,
                      xiaohongshu_account_id=self.xhs["account_id"],
                      xiaohongshu_binding_revision=self.xhs["binding_revision"], xiaohongshu_title="独立视频标题")
        if local:
            source = Path(self.tmp.name) / "original.mp4"
            source.write_bytes(b"video fixture")
            params.update(url="", local_path=str(source), title="B站视频标题", description="共用简介")
        params.update(extra)
        result = self.service.dispatch("tasks.create", params)
        self.wait_idle()
        return result

    def video_mocks(self):
        stack = self.mocks()
        stack.enter_context(patch.object(media, "probe_brief", return_value={"has_video": True, "duration": 10}))
        return stack

    def test_local_and_youtube_preview_share_prepared_video_and_editable_title(self):
        for local in (True, False):
            with self.subTest(local=local), self.video_mocks():
                result = self.create(local)
                task_id = result["task_id"]
                p = self.pub(task_id)
                self.assertEqual(p["status"], "ready")
                self.assertEqual(json.loads(p["snapshot"])["media_type"], "video")
                self.assertEqual(len(self.browser.calls), 0 if local else 1)
                self.service.update_publication(p["publication_id"], title="编辑的视频标题", revision=p["revision"])
                self.service.update_metadata(task_id, "B站标题", "修改后的简介 🌸")
                self.service.submit(task_id, uuid.uuid4().hex)
                self.wait_idle()
                task = self.service.task(task_id)
                self.assertEqual(task.status, "submitted")
                self.assertEqual(self.pub(task_id)["remote_id"], NOTE)
                self.assertEqual(self.browser.calls[-1]["paths"], [Path(task.video_path)])
                self.assertEqual(self.browser.calls[-1]["snapshot"]["title"], "编辑的视频标题")
                self.assertIn("修改后的简介", self.browser.calls[-1]["snapshot"]["description"])
                self.assertTrue(Path(task.video_path).is_file())

    def test_auto_mode_and_idempotent_create_do_not_repeat_publication(self):
        with self.video_mocks():
            first = self.create(mode="auto", operation_id="video-auto")
            second = self.create(mode="auto", operation_id="video-auto")
        self.assertEqual(first, second)
        self.assertEqual(len(self.browser.calls), 1)
        self.assertEqual(len(self.uploads), 1)
        self.assertTrue((Path(self.tmp.name) / "original.mp4").is_file())

    def test_uncertain_receipt_keeps_bilibili_success_and_prevents_retry(self):
        self.browser.mode = "timeout"
        with self.video_mocks():
            task_id = self.create(mode="auto")["task_id"]
        self.assertEqual(publications.for_platform(self.service.store, task_id, "bilibili")["status"], "submitted")
        p = self.pub(task_id)
        self.assertEqual(p["status"], "submission_unknown")
        with self.assertRaises(AppError): self.service.retry_publication(p["publication_id"], "retry-unknown")
        self.assertTrue(Path(self.service.task(task_id).video_path).is_file())

    def test_clear_rejection_can_retry_only_xiaohongshu(self):
        self.browser.mode = "reject"
        with self.video_mocks():
            task_id = self.create(mode="auto")["task_id"]
            p = self.pub(task_id)
            self.assertEqual(p["status"], "failed")
            self.browser.mode = "success"
            self.service.retry_publication(p["publication_id"], "retry-rejected")
            self.service.submit(task_id, "submit-retry", targets=[p["publication_id"]])
            self.wait_idle()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(self.pub(task_id)["status"], "submitted")

    def test_invalid_selection_is_rejected_before_creating_a_task(self):
        for invalid in ({"sync_xiaohongshu": "true"}, {"xiaohongshu_title": "🌸" * 11},
                        {"description": "🌸" * 501}, {"xiaohongshu_binding_revision": True},
                        {"xiaohongshu_account_id": "other"}):
            with self.subTest(invalid=invalid), self.video_mocks(), self.assertRaises(AppError):
                self.create(**invalid)
        self.assertEqual(self.service.store.list_all(), [])

    def test_opt_out_and_duplicate_source_cannot_add_a_target(self):
        with self.video_mocks():
            result = self.create(False, sync_xiaohongshu=False)
            self.assertEqual(len(publications.items(self.service.store, result["task_id"])), 1)
            with self.assertRaises(AppError): self.create(False)
        self.assertEqual(self.browser.calls, [])

    def test_invalid_video_blocks_only_xiaohongshu_and_emoji_edit_is_rejected(self):
        with self.video_mocks():
            task_id = self.create()["task_id"]
            with self.assertRaises(AppError):
                self.service.update_metadata(task_id, "标题", "🌸" * 501)
            self.assertEqual(self.service.task(task_id).desc_zh, "简介")
            with patch.object(media, "probe_brief", return_value={"has_video": False, "duration": 10}):
                with self.assertRaises(AppError): self.service.submit(task_id, "invalid-video")
        self.assertEqual(self.pub(task_id)["status"], "ready")
        self.assertEqual(self.browser.calls, [])

    def test_real_video_validation_cover_and_shared_upload(self):
        import subprocess
        source = Path(self.tmp.name) / "real.mp4"
        subprocess.run([media.ffmpeg_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
                        "-c:v", "libx264", "-c:a", "aac", str(source)], check=True, capture_output=True)
        original = source.read_bytes()
        real_validate, real_prepare = media.prepare_upload_video, pipeline._prepare_assets
        with self.mocks(), patch.object(media, "prepare_upload_video", side_effect=real_validate), \
                patch.object(pipeline, "_prepare_assets", side_effect=real_prepare):
            task_id = self.create(local_path=str(source), mode="auto", description="")["task_id"]
        task = self.service.task(task_id)
        self.assertEqual(task.status, "submitted", task.error)
        self.assertEqual(self.pub(task_id)["status"], "submitted")
        self.assertEqual(self.browser.calls[0]["paths"], [Path(task.video_path)])
        self.assertEqual(self.browser.calls[0]["snapshot"]["description"], "")
        self.assertTrue(Path(task.cover_path).stat().st_size > 0)
        self.assertEqual(source.read_bytes(), original)

    def test_receipt_wins_if_cancel_arrives_after_the_publish_click(self):
        self.browser.mode = "cancel_after_click"
        with self.video_mocks():
            task_id = self.create(mode="auto")["task_id"]
        self.assertEqual(self.pub(task_id)["status"], "submitted")
        self.assertEqual(self.pub(task_id)["remote_id"], NOTE)
