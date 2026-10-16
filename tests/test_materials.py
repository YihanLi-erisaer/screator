from __future__ import annotations

import functools
import http.server
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_desktop as fixtures
from screator import bili_upload, events, materials, media, pipeline, publications, youtube
from screator.db import SCHEMA_VERSION
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
        folder = self.service.materials.root / "创作素材"
        folder.mkdir()
        return self.service.dispatch("materials.create_library", {"directory": str(folder)})["id"]

    def test_custom_library_location_persists_and_receives_materials(self):
        parent = Path(self.tmp.name) / "用户选择的素材目录"
        parent.mkdir()
        folder = parent / "旅行素材"
        folder.mkdir()
        (folder / "existing.png").write_bytes(b"existing material")
        result = self.service.dispatch("materials.create_library", {"directory": str(folder)})
        folder = folder.resolve()
        self.assertEqual(result["name"], "旅行素材")
        self.assertEqual(result["path"], str(folder))
        self.assertTrue(folder.is_dir())
        self.assertFalse((self.service.materials.root / "旅行素材").exists())
        restored = materials.MaterialLibrary(self.service.materials.root, self.service.store)
        self.assertEqual(restored.folder(result["id"]), folder)
        source = Path(self.tmp.name) / "sample.png"
        source.write_bytes(b"custom location material")
        restored.commit(result["id"], "download-task", [source])
        listing = restored.list(result["id"])
        self.assertEqual(listing["path"], str(folder))
        self.assertEqual(len(listing["items"]), 2)
        downloaded = next(item for item in listing["items"] if item["name"] != "existing.png")
        self.assertEqual(restored.file(result["id"], downloaded["name"]).read_bytes(), b"custom location material")
        self.assertEqual((folder / "existing.png").read_bytes(), b"existing material")
        self.assertFalse((folder / "旅行素材").exists())
        self.assertEqual(restored.create(str(folder)), result)
        self.assertEqual(len(restored.libraries()["items"]), 1)
        with patch.object(materials, "open_native") as opened:
            restored.open(result["id"])
            opened.assert_called_once_with(folder)

    def test_existing_libraries_keep_their_location_after_schema_upgrade(self):
        folder = self.service.materials.root / "旧素材库"
        folder.mkdir()
        (folder / "original.png").write_bytes(b"original material")
        with self.service.store.transaction() as db:
            db.execute("DROP TABLE material_libraries")
            db.execute("CREATE TABLE material_libraries(id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE)")
            db.execute("INSERT INTO material_libraries VALUES(?,?)", ("legacy-library", "旧素材库"))
        restored = materials.MaterialLibrary(self.service.materials.root, self.service.store)
        self.assertEqual(restored.folder("legacy-library"), folder)
        self.assertEqual(restored.list("legacy-library")["items"][0]["name"], "original.png")
        self.assertEqual(restored.file("legacy-library", "original.png").read_bytes(), b"original material")
        new_folder = self.service.materials.root / "新素材库"
        new_folder.mkdir()
        result = restored.create(str(new_folder))
        self.assertTrue(restored.folder(result["id"]).is_dir())

    def test_invalid_library_folders_do_not_create_records(self):
        parent = Path(self.tmp.name) / "素材目录"
        parent.mkdir()
        existing = parent / "已存在"
        existing.mkdir()
        original = existing / "original.png"
        original.write_bytes(b"preserved")
        invalid = ("", "relative/path", str(parent / "missing"), str(original), 123, "bad\x00path", None, str(Path(parent.anchor)))
        for directory in invalid:
            with self.subTest(directory=directory), self.assertRaises(AppError):
                self.service.materials.create(directory)
        self.assertFalse((parent / "missing").exists())
        self.assertEqual(original.read_bytes(), b"preserved")
        self.assertEqual(self.service.materials.libraries()["items"], [])

    def test_different_folders_with_the_same_name_do_not_replace_library(self):
        first = Path(self.tmp.name) / "first" / "素材"
        second = Path(self.tmp.name) / "second" / "素材"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        result = self.service.materials.create(str(first))
        with self.assertRaisesRegex(AppError, "同名素材库"):
            self.service.materials.create(str(second))
        self.assertEqual(self.service.materials.folder(result["id"]), first.resolve())
        self.assertEqual(len(self.service.materials.libraries()["items"]), 1)

    def source_server(self):
        root = Path(self.tmp.name) / "http"
        root.mkdir(exist_ok=True)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return root, f"http://127.0.0.1:{server.server_port}"

    def test_local_material_import_preserves_sources_and_existing_files(self):
        library = self.library()
        folder = self.service.materials.folder(library)
        (folder / "photo.PNG").write_bytes(b"existing image")
        sources = []
        for name, content in (("photo.PNG", b"new image"), ("music.mp3", b"new audio"), ("video.mp4", b"new video")):
            source = Path(self.tmp.name) / name
            source.write_bytes(content)
            sources.append(str(source))
        result = self.service.dispatch("materials.import", {"library_id": library, "paths": sources})
        self.assertEqual(result, {"added": 3, "skipped": 0})
        self.assertEqual((folder / "photo.PNG").read_bytes(), b"existing image")
        self.assertEqual((folder / "photo (1).PNG").read_bytes(), b"new image")
        for value in sources:
            self.assertTrue(Path(value).is_file())
        self.assertEqual((folder / "music.mp3").read_bytes(), Path(sources[1]).read_bytes())
        self.assertEqual((folder / "video.mp4").read_bytes(), Path(sources[2]).read_bytes())
        self.assertEqual({item["kind"] for item in self.service.materials.list(library)["items"]}, {"image", "audio", "video"})
        result = self.service.materials.import_files(library, [str(folder / "photo.PNG"), sources[0], sources[0]])
        self.assertEqual(result, {"added": 1, "skipped": 2})
        self.assertEqual((folder / "photo (2).PNG").read_bytes(), b"new image")

    def test_local_material_import_validates_the_entire_selection_before_copying(self):
        library = self.library()
        image = Path(self.tmp.name) / "valid.png"
        image.write_bytes(b"image")
        unsupported = Path(self.tmp.name) / "document.pdf"
        unsupported.write_bytes(b"document")
        for paths in (None, "file.png", [], [123], ["bad\x00path"], ["relative.png"], [str(image), str(unsupported)], [str(image), str(image.parent)], [str(image), str(image.parent / "missing.mp4")]):
            with self.subTest(paths=paths), self.assertRaises(AppError):
                self.service.materials.import_files(library, paths)
        self.assertEqual(self.service.materials.list(library)["items"], [])
        self.assertEqual(image.read_bytes(), b"image")

    def test_local_material_import_cleans_up_copies_if_a_copy_fails(self):
        library = self.library()
        sources = []
        for name in ("first.png", "second.mp4"):
            source = Path(self.tmp.name) / name
            source.write_bytes(b"original")
            sources.append(str(source))
        copy = materials.shutil.copyfileobj
        attempts = 0
        def fail_second(reader, writer, **kwargs):
            nonlocal attempts
            attempts += 1
            writer.write(b"partial")
            if attempts == 2:
                raise OSError("disk full")
            copy(reader, writer, **kwargs)
        with patch.object(materials.shutil, "copyfileobj", side_effect=fail_second):
            with self.assertRaisesRegex(AppError, "添加本地素材失败"):
                self.service.materials.import_files(library, sources)
        self.assertEqual(self.service.materials.list(library)["items"], [])
        for source in sources:
            self.assertEqual(Path(source).read_bytes(), b"original")

    def test_material_dispatch_respects_parameter_and_shutdown_guards(self):
        with self.assertRaisesRegex(AppError, "请求参数必须为对象"):
            self.service.dispatch("materials.libraries", None)
        self.service.scheduler.closing = True
        try:
            with self.assertRaisesRegex(AppError, "应用正在退出"):
                self.service.dispatch("materials.create_library", {"directory": str(self.service.materials.root)})
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
        library = self.library()
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

    def test_local_custom_cover_used_for_preview_and_auto_publish(self):
        from PIL import Image
        source = Path(self.tmp.name) / "user-video.mp4"
        source.write_bytes(b"original video")
        cover = Path(self.tmp.name) / "user-cover.png"
        Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(cover)
        original_cover = cover.read_bytes()
        real_prepare = pipeline._prepare_assets
        for mode in ("preview", "auto"):
            with self.subTest(mode=mode), self.mocks(), patch.object(pipeline, "_prepare_assets", side_effect=real_prepare), patch.object(media, "extract_frame_cover") as frame, patch.object(youtube, "download_thumbnail") as thumb, patch("screator.translation.tasks.prepare") as translate, patch.object(bili_upload, "upload", side_effect=self.upload) as upload:
                params = dict(url="", operation_id="custom-cover-" + mode, account_id=self.account["account_id"],
                              mode=mode, local_path=str(source), cover_path=str(cover), title="Custom title")
                result = self.service.dispatch("tasks.create", params)
                self.assertEqual(self.service.dispatch("tasks.create", params), result)
                self.wait_idle()
                task = self.service.task(result["task_id"])
                self.assertEqual(task.status, "ready" if mode == "preview" else "submitted", task.error)
                self.assertEqual(task.local_cover_path, str(cover.resolve()))
                self.assertEqual(Path(task.cover_path), Path(task.work_dir) / "cover.jpg")
                with Image.open(task.cover_path) as prepared:
                    settings = self.service.config.build()
                    self.assertEqual(prepared.size, (settings.cover_width, settings.cover_height))
                    self.assertEqual(prepared.format, "JPEG")
                    self.assertGreater(prepared.getpixel((0, 0))[0], 250)
                self.assertIsNotNone(self.service.cover(task.task_id)["image"])
                frame.assert_not_called(); thumb.assert_not_called(); translate.assert_not_called()
                if mode == "auto":
                    self.assertEqual(upload.call_args.args[2], Path(task.cover_path))
                else:
                    upload.assert_not_called()
        self.assertEqual(cover.read_bytes(), original_cover)
        self.assertEqual(source.read_bytes(), b"original video")

    def test_invalid_custom_covers_rejected_before_queueing(self):
        from PIL import Image
        source = Path(self.tmp.name) / "user-video.mp4"
        source.write_bytes(b"video")
        folder = Path(self.tmp.name)
        (folder / "corrupt.png").write_bytes(b"not an image")
        Image.new("RGB", (20, 20)).save(folder / "unsupported.gif")
        invalid_covers = [str(folder / "missing.png"), str(folder), "relative.png",
                          str(folder / "corrupt.png"), str(folder / "unsupported.gif"), 123]
        for cover in invalid_covers:
            with self.subTest(cover=cover), self.assertRaises(AppError):
                self.service.create("", "invalid-cover-test", account_id=self.account["account_id"],
                                    local_path=str(source), cover_path=cover, title="Title")
        self.assertFalse(self.service.store.list_all())
        with self.assertRaisesRegex(AppError, "仅支持本地视频"):
            self.service.create("https://youtu.be/abcdefghijk", "youtube-cover-test", account_id=self.account["account_id"],
                                cover_path=str(folder / "corrupt.png"))

    def test_missing_custom_cover_fails_and_retry_uses_restored_image(self):
        from PIL import Image
        source = Path(self.tmp.name) / "user-video.mp4"
        source.write_bytes(b"video")
        cover = Path(self.tmp.name) / "cover.jpg"
        Image.new("RGB", (200, 100), "blue").save(cover)
        real_prepare = pipeline._prepare_assets
        def remove_cover(*args):
            cover.unlink()
            return real_prepare(*args)
        with self.mocks(), patch.object(pipeline, "_prepare_assets", side_effect=remove_cover), patch.object(media, "extract_frame_cover") as frame:
            result = self.service.create("", "missing-cover-test", account_id=self.account["account_id"],
                                         local_path=str(source), cover_path=str(cover), title="Title", mode="auto")
            self.wait_idle()
            task = self.service.task(result["task_id"])
            self.assertEqual(task.status, "failed")
            self.assertIn("封面图片已移动或删除", task.error)
            frame.assert_not_called()
            self.assertFalse(self.uploads)
        Image.new("RGB", (200, 100), "blue").save(cover)
        with self.mocks(), patch.object(pipeline, "_prepare_assets", side_effect=real_prepare):
            self.service.retry(task.task_id, "restored-cover-retry")
            self.wait_idle()
            self.assertEqual(self.service.task(task.task_id).status, "ready")
            self.service.submit(task.task_id, "restored-cover-submit")
            self.wait_idle()
        self.assertEqual(self.service.task(task.task_id).status, "submitted")

    def test_custom_cover_participates_in_operation_identity(self):
        from PIL import Image
        source = Path(self.tmp.name) / "user-video.mp4"
        source.write_bytes(b"video")
        cover = Path(self.tmp.name) / "cover.png"
        Image.new("RGB", (20, 20)).save(cover)
        params = dict(url="", operation_id="cover-identity-test", account_id=self.account["account_id"],
                      local_path=str(source), title="Title")
        with self.mocks():
            self.service.create(**params)
            self.wait_idle()
            with self.assertRaises(AppError):
                self.service.create(**params, cover_path=str(cover))

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

    def test_instagram_cookie_import_is_scoped_and_can_be_cleared(self):
        source = Path(self.tmp.name) / "browser-cookies.txt"
        source.write_text("# Netscape HTTP Cookie File\n"
                          "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\tinstagram-secret\n"
                          ".example.com\tTRUE\t/\tTRUE\t2147483647\ttoken\tunrelated-secret\n",
                          encoding="utf-8")
        self.service.dispatch("auth.import", {"path": str(source), "kind": "instagram"})
        target = self.paths.root / "secrets/instagram_cookies.txt"
        self.assertTrue(self.service.config.public(False)["instagram_cookies"])
        self.assertIn("instagram-secret", target.read_text(encoding="utf-8"))
        self.assertNotIn("unrelated-secret", target.read_text(encoding="utf-8"))
        self.service.dispatch("auth.clear", {"kind": "instagram"})
        self.assertFalse(target.exists())
        self.assertFalse(self.service.config.public(False)["instagram_cookies"])
        source.write_text("# Netscape HTTP Cookie File\n"
                          ".instagram.com\tTRUE\t/\tTRUE\t2147483647\tcsrftoken\tno-login\n",
                          encoding="utf-8")
        with self.assertRaisesRegex(AppError, "sessionid"):
            self.service.dispatch("auth.import", {"path": str(source), "kind": "instagram"})
        self.assertFalse(target.exists())

    def test_instagram_login_redirect_uses_cookie_and_reports_login_state(self):
        import re
        from gallery_dl import exception, extractor
        from gallery_dl.extractor.common import Extractor

        seen = []
        class Fixture(Extractor):
            category = "instagram"
            subcategory = "post"
            pattern = r"https://www\.instagram\.com/p/([^/]+)"

            def items(self):
                seen.append(self.config("cookies"))
                raise exception.AbortExtraction("HTTP redirect to login page (https://www.instagram.com/accounts/login/)")
                yield

        url = "https://www.instagram.com/p/example/"
        folder = Path(self.tmp.name) / "gallery"
        folder.mkdir()
        cookie_file = Path(self.tmp.name) / "instagram-cookies.txt"
        cookie_file.write_text("fixture", encoding="utf-8")
        with patch.object(extractor, "find", side_effect=lambda _: Fixture(re.match(Fixture.pattern, url))):
            with self.assertRaisesRegex(AppError, "先在.*导入 Instagram Cookie"):
                materials.download_gallery(url, folder)
            with self.assertRaisesRegex(AppError, "登录状态已失效"):
                materials.download_gallery(url, folder, cookie_file)
        self.assertEqual(seen, [None, str(cookie_file)])

    def test_material_image_download_passes_imported_instagram_cookie(self):
        from screator.db import Task

        cookie_file = self.paths.root / "secrets/instagram_cookies.txt"
        cookie_file.write_text("fixture", encoding="utf-8")
        task = Task("instagram-post", "https://www.instagram.com/p/example/", "pending",
                    source_kind="material", asset_type="image")
        folder = Path(self.tmp.name) / "instagram-work"
        folder.mkdir()

        def fake_gallery(url, destination, cookies):
            self.assertEqual(url, task.url)
            self.assertEqual(cookies, cookie_file)
            (destination / "image.jpg").write_bytes(b"image")

        with patch.object(materials, "download_gallery", side_effect=fake_gallery):
            self.assertEqual(materials.download(task, self.service.config.build(), folder),
                             [folder / "materials/image.jpg"])

    def test_schema_five_migration_preserves_youtube_tasks_and_backs_up(self):
        from screator.db import Task, TaskStore
        path = Path(self.tmp.name) / "migration.sqlite"
        store = TaskStore(path)
        task = Task("abcdefghijk", "https://youtu.be/abcdefghijk", "ready")
        store.upsert(task)
        for name in ("source_kind", "local_path", "local_cover_path", "library_id", "asset_type"):
            store._conn.execute(f"ALTER TABLE tasks DROP COLUMN {name}")
        store._conn.execute("PRAGMA user_version=5")
        store.close()
        store = TaskStore(path)
        self.addCleanup(store.close)
        self.assertEqual(store.require(task.task_id).source_kind, "youtube")
        self.assertEqual(store.require(task.task_id).status, "ready")
        self.assertTrue(Path(str(path) + ".pre-v6.bak").is_file())
        self.assertEqual(store._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_schema_six_migration_preserves_local_tasks_and_cover_paths(self):
        from screator.db import Task, TaskStore
        path = Path(self.tmp.name) / "migration-v6.sqlite"
        store = TaskStore(path)
        task = Task("local-existing", "", "ready", source_kind="local", local_path="/user/video.mp4",
                    cover_path="/work/cover.jpg", title_zh="Existing title")
        store.upsert(task)
        store._conn.execute("ALTER TABLE tasks DROP COLUMN local_cover_path")
        store._conn.execute("PRAGMA user_version=6")
        store.close()
        store = TaskStore(path)
        restored = store.require(task.task_id)
        self.assertEqual((restored.source_kind, restored.local_path, restored.cover_path, restored.title_zh),
                         (task.source_kind, task.local_path, task.cover_path, task.title_zh))
        self.assertEqual(restored.local_cover_path, "")
        restored.local_cover_path = "/user/custom.png"
        store.upsert(restored)
        store.close()
        store = TaskStore(path)
        self.addCleanup(store.close)
        self.assertEqual(store.require(task.task_id).local_cover_path, "/user/custom.png")
        self.assertTrue(Path(str(path) + ".pre-v7.bak").is_file())
        self.assertEqual(store._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

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
