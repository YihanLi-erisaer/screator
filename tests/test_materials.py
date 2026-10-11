from __future__ import annotations

import functools
import http.server
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_desktop as fixtures
from screator import events, materials, media, pipeline, publications, youtube
from screator.exceptions import AppError, InvalidMediaError


class MaterialTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    wait_until = fixtures.DesktopTests.wait_until
    wait_idle = fixtures.DesktopTests.wait_idle
    meta = fixtures.DesktopTests.meta
    download = fixtures.DesktopTests.download
    prepare = fixtures.DesktopTests.prepare
    upload = fixtures.DesktopTests.upload
    mocks = fixtures.DesktopTests.mocks

    def library(self):
        return self.service.dispatch("materials.create_library", {"name": "创作素材"})["id"]

    def source_server(self):
        root = Path(self.tmp.name) / "http"
        root.mkdir(exist_ok=True)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return root, f"http://127.0.0.1:{server.server_port}"

    def test_material_dispatch_respects_parameter_and_shutdown_guards(self):
        with self.assertRaisesRegex(AppError, "请求参数必须为对象"):
            self.service.dispatch("materials.libraries", None)
        self.service.scheduler.closing = True
        try:
            with self.assertRaisesRegex(AppError, "应用正在退出"):
                self.service.dispatch("materials.create_library", {"name": "退出期间"})
        finally:
            self.service.scheduler.closing = False
        self.assertEqual(self.service.dispatch("materials.libraries", {})["items"], [])

    def test_real_http_download_repeat_record_and_management(self):
        library = self.library()
        root, base = self.source_server()
        from PIL import Image
        Image.new("RGB", (20, 20), "red").save(root / "sample.png")
        original = (root / "sample.png").read_bytes()
        ids = []
        for index in range(2):
            params = dict(url=base + "/sample.png", library_id=library, asset_type="image", operation_id=f"download-{index}")
            created = self.service.dispatch("materials.download", params)
            self.assertEqual(self.service.dispatch("materials.download", params), created)
            ids.append(created["task_id"])
        self.wait_idle()
        self.assertNotEqual(*ids)
        rows = self.service.dispatch("materials.list", {"library_id": library})["items"]
        self.assertEqual(len(rows), 2)
        for task_id in ids:
            task = self.service.task(task_id)
            self.assertEqual(task.status, "downloaded", task.error)
            self.assertFalse(publications.items(self.service.store, task_id))
        self.assertEqual(self.service.list_tasks()["total"], 2)
        self.assertEqual(self.service.list_tasks(history=True)["total"], 0)
        self.service.materials.rename(library, rows[0]["name"], "参考图.png")
        self.assertEqual(self.service.materials.file(library, "参考图.png").read_bytes(), original)
        with patch.object(materials, "open_native") as opened:
            self.service.materials.open(library, "参考图.png")
            opened.assert_called_once()
        with self.assertRaises(AppError): self.service.materials.delete(library, "参考图.png")
        self.service.materials.delete(library, "参考图.png", confirmed=True)
        self.assertEqual(len(self.service.materials.list(library)["items"]), 1)
        self.assertEqual(self.service.list_tasks()["total"], 2)

    def test_library_traversal_and_extension_changes_rejected(self):
        for name in ("..", "../outside", "C:\\outside", "CON", "test.", "bad/name"):
            with self.subTest(name=name), self.assertRaises(AppError): self.service.materials.create(name)
        library = self.library()
        with self.assertRaises(AppError): self.service.materials.create("创作素材")
        path = self.service.materials.folder(library) / "a.png"
        path.write_bytes(b"image")
        with self.assertRaises(AppError): self.service.materials.rename(library, "a.png", "a.exe")
        with self.assertRaises(AppError): self.service.materials.delete(library, "../a.png", True)
        self.assertTrue(path.exists())

    def test_queued_download_cancelled_immediately_and_failed_download_retries(self):
        library = self.library()
        started, release = threading.Event(), threading.Event()
        def blocked(*args):
            started.set(); release.wait(5); events.check_cancelled()
            raise AppError("fixture failure")
        with patch.object(materials, "download", side_effect=blocked):
            first = self.service.create_download("https://example.com/one", library, "video", "download-one")
            self.assertTrue(started.wait(5))
            second = self.service.create_download("https://example.com/two", library, "video", "download-two")
            self.service.cancel(second["task_id"])
            self.assertEqual(self.service.task(second["task_id"]).status, "cancelled")
            release.set(); self.wait_idle()
        task = self.service.task(first["task_id"])
        self.assertEqual(task.status, "failed")
        with patch.object(materials, "download", side_effect=AppError("retry reached downloader")) as download:
            self.service.retry(task.task_id, "retry-download")
            self.wait_idle()
            download.assert_called_once()
        self.assertEqual(len(self.service.store.list_all()), 2)

    def test_local_auto_publish_preserves_source_and_workdir(self):
        source = Path(self.tmp.name) / "user-owned.mp4"
        source.write_bytes(b"untouched original")
        with self.mocks(), patch.object(youtube, "download_video") as download, patch.object(youtube, "fetch_meta") as meta:
            result = self.service.create("", "local-auto-submit", account_id=self.account["account_id"], mode="auto",
                                         local_path=str(source), title="自己的标题", description="自己的简介")
            self.wait_idle()
            task = self.service.task(result["task_id"])
            self.assertEqual(task.status, "submitted", task.error)
            self.assertTrue(Path(task.work_dir).is_dir())
            self.assertTrue(Path(task.video_path).is_file())
            self.assertNotEqual(Path(task.video_path), source)
            self.assertEqual(source.read_bytes(), b"untouched original")
            download.assert_not_called(); meta.assert_not_called()
            self.assertFalse(publications.can_cleanup(self.service.store, task.task_id))
            self.service.scheduler.uploader.cleanup(task.task_id)
            self.assertTrue(Path(task.work_dir).exists())

    def test_local_prepare_never_translates_or_downloads_thumbnail(self):
        from screator.db import Task
        folder = Path(self.tmp.name) / "prepare"
        folder.mkdir()
        task = Task("local-test", "", "pending", source_kind="local", title_zh="用户标题", desc_zh="用户简介")
        self.service.store.upsert(task)
        meta = youtube.YoutubeMeta(task.video_id, "", task.title_zh, task.desc_zh, "", None, "", None, "")
        with patch.object(media, "extract_frame_cover"), patch.object(media, "process_cover"), patch.object(youtube, "download_thumbnail") as thumb, patch("screator.translation.tasks.prepare") as translate:
            pipeline._prepare_assets(self.service.config.build(), self.service.store, task, meta, folder, folder / "video.mp4")
            translate.assert_not_called(); thumb.assert_not_called()
        self.assertEqual(task.title_zh, "用户标题")
        self.assertEqual(task.desc_zh, "用户简介")

    def test_local_invalid_media_does_not_redownload_or_quarantine_source(self):
        source = Path(self.tmp.name) / "invalid.mp4"
        source.write_bytes(b"original invalid media")
        with self.mocks(), patch.object(media, "prepare_upload_video", side_effect=InvalidMediaError("invalid")), patch.object(youtube, "download_video") as download:
            params = dict(url="", operation_id="local-invalid", account_id=self.account["account_id"], local_path=str(source), title="Test")
            result = self.service.create(**params)
            self.assertEqual(self.service.create(**params), result)
            self.wait_idle()
            task = self.service.task(result["task_id"])
            self.assertEqual(task.status, "failed")
            download.assert_not_called()
        self.assertEqual(source.read_bytes(), b"original invalid media")

    def test_local_metadata_edit_keeps_exact_description(self):
        source = Path(self.tmp.name) / "video.mp4"
        source.write_bytes(b"video")
        with self.mocks():
            result = self.service.create("", "local-edit-test", account_id=self.account["account_id"], local_path=str(source), title="Title")
            self.wait_idle()
        self.service.update_metadata(result["task_id"], "Custom title", "My exact description")
        self.assertEqual(self.service.task(result["task_id"]).desc_zh, "My exact description")
        with self.assertRaises(AppError): self.service.retranslate(result["task_id"], "no-translate")

    def test_audio_adapter_requests_extraction(self):
        from screator.db import Task
        folder = Path(self.tmp.name) / "audio"
        folder.mkdir()
        task = Task("audio", "https://soundcloud.com/user/song", "pending", source_kind="material", asset_type="audio")
        captured = {}
        def download(opts, url):
            captured.update(opts)
            (folder / "materials" / "song.mp3").write_bytes(b"audio")
        with patch.object(youtube, "_base_opts", return_value={}), patch.object(youtube, "_download_with_slot", side_effect=download):
            self.assertEqual(len(materials.download(task, self.service.config.build(), folder)), 1)
        self.assertEqual(captured["postprocessors"][0]["key"], "FFmpegExtractAudio")
        self.assertEqual(captured["playlistend"], 1)

    def test_gallery_adapter_downloads_images_with_real_http(self):
        import re
        from gallery_dl.extractor.common import Extractor, Message
        from gallery_dl import extractor
        root, base = self.source_server()
        from PIL import Image
        Image.new("RGB", (12, 12), "blue").save(root / "image.png")
        class Fixture(Extractor):
            category = "fixture"
            subcategory = "post"
            pattern = r"https://fixture.test/(.+)"
            def items(self):
                data = {"id": "post", "num": 1, "filename": "image", "extension": "png"}
                yield Message.Directory, "", data
                yield Message.Url, base + "/image.png", data
        folder = Path(self.tmp.name) / "gallery"
        folder.mkdir()
        fixture = Fixture(re.match(Fixture.pattern, "https://fixture.test/post"))
        with patch.object(extractor, "find", return_value=fixture):
            materials.download_gallery("https://fixture.test/post", folder)
        files = list(folder.rglob("*.png"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), (root / "image.png").read_bytes())

    def test_schema_five_migration_preserves_youtube_tasks_and_backs_up(self):
        from screator.db import Task, TaskStore
        path = Path(self.tmp.name) / "migration.sqlite"
        store = TaskStore(path)
        task = Task("abcdefghijk", "https://youtu.be/abcdefghijk", "ready")
        store.upsert(task)
        for name in ("source_kind", "local_path", "library_id", "asset_type"):
            store._conn.execute(f"ALTER TABLE tasks DROP COLUMN {name}")
        store._conn.execute("PRAGMA user_version=5")
        store.close()
        store = TaskStore(path)
        self.addCleanup(store.close)
        self.assertEqual(store.require(task.task_id).source_kind, "youtube")
        self.assertEqual(store.require(task.task_id).status, "ready")
        self.assertTrue(Path(str(path) + ".pre-v6.bak").is_file())
        self.assertEqual(store._conn.execute("PRAGMA user_version").fetchone()[0], 6)

    def test_real_local_video_validation_cover_and_submission(self):
        import subprocess
        source = Path(self.tmp.name) / "real-local.mp4"
        subprocess.run([media.ffmpeg_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
                        "-c:v", "libx264", "-c:a", "aac", str(source)], check=True, capture_output=True)
        original = source.read_bytes()
        real_validate, real_prepare = media.prepare_upload_video, pipeline._prepare_assets
        with self.mocks(), patch.object(media, "prepare_upload_video", side_effect=real_validate), patch.object(pipeline, "_prepare_assets", side_effect=real_prepare), patch("screator.translation.tasks.prepare", side_effect=AssertionError("Translation must not run")):
            result = self.service.create("", "real-local-submit", account_id=self.account["account_id"], mode="auto",
                                         local_path=str(source), title="Exact title", description="Exact description")
            self.wait_idle()
        task = self.service.task(result["task_id"])
        self.assertEqual(task.status, "submitted", task.error)
        self.assertEqual(self.uploads[0][1:], ("Exact title", "Exact description"))
        self.assertTrue(Path(task.cover_path).stat().st_size > 0)
        self.assertTrue(Path(task.video_path).stat().st_size > 0)
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__": unittest.main()
