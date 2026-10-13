import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from PIL import Image

from screator import events, media, pipeline, youtube
from screator.db import Task
from screator.exceptions import AppError


def image_bytes(size=(32, 18)):
    output = io.BytesIO()
    Image.new("RGB", size, "blue").save(output, format="JPEG")
    return output.getvalue()


class ThumbnailRecoveryTests(unittest.TestCase):
    def test_transient_errors_retry_with_two_second_intervals_and_replace_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "cover.jpg"
            dest.write_bytes(b"previous cover")
            with patch.object(youtube, "urlopen", side_effect=[OSError("reset"), OSError("timeout"), io.BytesIO(image_bytes())]) as request, \
                 patch.object(youtube.events, "wait") as wait:
                youtube.download_thumbnail("https://example.com/cover.jpg", dest)
            self.assertEqual(request.call_count, 3)
            self.assertEqual([call.args[0] for call in wait.call_args_list], [2, 2])
            self.assertEqual(dest.read_bytes(), image_bytes())
            self.assertEqual(list(Path(folder).glob("*.part")), [])

    def test_missing_variant_tries_five_times_and_keeps_existing_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "cover.jpg"
            dest.write_bytes(b"previous cover")
            error = HTTPError("https://example.com/maxres.jpg", 404, "Not Found", {}, None)
            with patch.object(youtube, "urlopen", side_effect=error) as request, \
                 patch.object(youtube.events, "wait") as wait:
                with self.assertRaises(AppError):
                    youtube.download_thumbnail(error.url, dest)
            self.assertEqual(request.call_count, 5)
            self.assertEqual([call.args[0] for call in wait.call_args_list], [2, 2, 2, 2])
            self.assertEqual(dest.read_bytes(), b"previous cover")

    def test_cancellation_during_retry_wait_stops_next_request(self):
        cancel = threading.Event()

        def failed_request(*_args, **_kwargs):
            cancel.set()
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(youtube, "urlopen", side_effect=failed_request) as request:
            with events.task_context(None, cancel, lambda *_: None):
                with self.assertRaises(events.Cancelled):
                    youtube.download_thumbnail("https://example.com/cover.jpg", Path(folder) / "cover.jpg")
        request.assert_called_once()

    def test_invalid_image_retries_before_using_next_metadata_candidate(self):
        info = {"id": "abcdefghijk", "thumbnails": [
            {"url": "https://example.com/low.jpg", "width": 1280, "height": 720},
            {"url": "https://example.com/high.jpg", "width": 1920, "height": 1080},
        ]}
        with patch.object(youtube, "_extract", return_value=info):
            meta = youtube.fetch_meta("https://youtu.be/abcdefghijk", None)
        self.assertEqual(meta.thumbnail_urls, ["https://example.com/high.jpg", "https://example.com/low.jpg"])

        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            task = Task(meta.video_id, meta.url, "pending")
            task.work_dir = str(work)
            task.video_path = str(work / "video.mp4")
            settings = SimpleNamespace(cover_width=480, cover_height=270)
            with patch.object(youtube, "urlopen", side_effect=[io.BytesIO(b"not an image"), io.BytesIO(b"still bad"), io.BytesIO(b"bad"), io.BytesIO(b"bad again"), io.BytesIO(b"last bad"), io.BytesIO(image_bytes((1280, 720)))]) as request, \
                 patch.object(youtube.events, "wait"), \
                 patch.object(media, "extract_frame_cover") as frame, \
                 patch("screator.translation.tasks.prepare"):
                pipeline._prepare_assets(settings, MagicMock(), task, meta, work, Path(task.video_path))
            self.assertEqual(request.call_count, 6)
            frame.assert_not_called()
            self.assertTrue((work / "cover.jpg").is_file())

    def test_old_saved_metadata_still_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "meta.json"
            path.write_text(json.dumps({
                "video_id": "abcdefghijk", "url": "url", "title": "title", "description": "",
                "uploader": "", "duration": 1, "thumbnail_url": "old.jpg",
                "language": None, "webpage_url": "url",
            }), encoding="utf-8")
            self.assertEqual(youtube.YoutubeMeta.load(path).thumbnail_urls, [])

    def test_unknown_size_uses_ytdlp_quality_preference(self):
        info = {"thumbnails": [
            {"url": "small.jpg", "width": 120, "height": 90, "preference": -12},
            {"url": "sd.jpg", "preference": -4},
            {"url": "maxres.jpg", "preference": 0},
        ]}
        self.assertEqual(youtube._thumbnail_candidates(info), ["maxres.jpg", "sd.jpg", "small.jpg"])

    def test_only_high_resolution_candidates_are_saved(self):
        info = {"id": "abcdefghijk", "thumbnails": [
            {"url": "https://example.com/small.jpg", "width": 640, "height": 480},
            {"url": "https://example.com/short.jpg", "width": 1280, "height": 600},
            {"url": "https://example.com/large.jpg", "width": 1280, "height": 720},
            {"url": "https://i.ytimg.com/vi/abcdefghijk/hq720.jpg"},
            {"url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"},
        ]}
        with patch.object(youtube, "_extract", return_value=info):
            meta = youtube.fetch_meta("url", None)
        self.assertEqual(meta.thumbnail_urls, [
            "https://example.com/large.jpg", "https://i.ytimg.com/vi/abcdefghijk/hq720.jpg",
        ])

    def test_no_high_resolution_thumbnail_falls_back_to_video_frame(self):
        info = {"id": "abcdefghijk", "thumbnails": [
            {"url": "https://example.com/small.jpg", "width": 640, "height": 480},
        ]}
        with patch.object(youtube, "_extract", return_value=info):
            meta = youtube.fetch_meta("url", None)
        self.assertEqual(meta.thumbnail_urls, [])

        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            task = Task(meta.video_id, meta.url, "pending")
            settings = SimpleNamespace(cover_width=1280, cover_height=720)

            def frame(_video, dest):
                dest.write_bytes(image_bytes((1280, 720)))

            with patch.object(youtube, "urlopen") as request, \
                 patch.object(media, "extract_frame_cover", side_effect=frame) as extract, \
                 patch("screator.translation.tasks.prepare"):
                pipeline._prepare_assets(settings, MagicMock(), task, meta, work, work / "video.mp4")
            request.assert_not_called()
            extract.assert_called_once()
            self.assertTrue((work / "cover.jpg").is_file())

    def test_downloaded_image_must_meet_minimum_size(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "cover.jpg"
            dest.write_bytes(b"previous cover")
            with patch.object(youtube, "urlopen", return_value=io.BytesIO(image_bytes((640, 360)))) as request:
                with self.assertRaisesRegex(AppError, "低于 1280×720"):
                    youtube.download_thumbnail("https://example.com/maxresdefault.jpg", dest,
                                               min_width=1280, min_height=720)
            request.assert_called_once()
            self.assertEqual(dest.read_bytes(), b"previous cover")

    def test_two_urls_at_same_resolution_share_five_attempts(self):
        meta = youtube.YoutubeMeta("id", "url", "title", "", "", None, "https://example.com/a.webp", None, "url",
                                   thumbnail_options=[
                                       {"url": "https://example.com/a.webp", "width": 1280, "height": 720},
                                       {"url": "https://example.com/b.jpg", "width": 1280, "height": 720},
                                   ])
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(youtube, "urlopen", side_effect=OSError("offline")) as request, \
             patch.object(youtube.events, "wait") as wait:
            with self.assertRaises(AppError):
                youtube.download_best_thumbnail(meta, Path(folder) / "cover.jpg")
        self.assertEqual(request.call_count, 5)
        self.assertEqual([call.args[0].full_url for call in request.call_args_list],
                         ["https://example.com/a.webp", "https://example.com/b.jpg", "https://example.com/a.webp",
                          "https://example.com/b.jpg", "https://example.com/a.webp"])
        self.assertEqual([call.args[0] for call in wait.call_args_list], [2, 2, 2, 2])


if __name__ == "__main__":
    unittest.main()
