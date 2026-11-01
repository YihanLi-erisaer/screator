from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from screator import youtube
from screator.desktop_settings import DesktopSettings
from screator.exceptions import AppError
from screator.paths import AppPaths


class YoutubeCookieExportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.settings = DesktopSettings(
            AppPaths.default(folder.name, folder.name), vault=object(),
        ).build()
        for mock in (
            patch.object(youtube, "_js_runtimes", return_value={}),
            patch.object(youtube.media, "ffmpeg_tool", return_value="ffmpeg"),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def export(self, settings, browser):
        destination = self.root / "secrets/youtube_cookies.txt"
        downloader = MagicMock()

        def write_cookies(*args, **kwargs):
            destination.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        downloader.YoutubeDL.return_value.__enter__.return_value.extract_info.side_effect = write_cookies
        with patch.object(youtube, "__getattr__", return_value=downloader):
            result = youtube.export_browser_cookies(settings, browser)
        self.assertEqual(result, destination)
        self.assertTrue(destination.is_file())
        options = downloader.YoutubeDL.call_args.args[0]
        self.assertEqual(options["cookiefile"], str(destination))
        self.assertEqual(options["cookiesfrombrowser"][0], browser)

    def test_first_desktop_export_creates_missing_destination(self):
        settings = replace(self.settings, youtube_cookies=self.root / "secrets/youtube_cookies.txt")
        self.assertFalse(settings.youtube_cookies.exists())
        self.export(settings, "chrome")

    def test_export_uses_default_destination_without_cookie_configuration(self):
        self.export(self.settings, "firefox")

    def test_download_still_rejects_missing_input_cookie_file(self):
        settings = replace(self.settings, youtube_cookies=self.root / "missing.txt")
        with self.assertRaisesRegex(AppError, "YouTube cookies 文件不存在"):
            youtube._base_opts(settings)

    def test_metadata_keeps_cookies_and_allows_downloader_to_choose_clients(self):
        cookie_file = self.root / "secrets/youtube_cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        settings = replace(self.settings, youtube_cookies=cookie_file)
        with patch.object(youtube, "_extract_info", return_value={"id": "UKhwU_Q-FgU"}) as extract:
            meta = youtube.fetch_meta("https://youtu.be/UKhwU_Q-FgU", settings)
        opts, _, download = extract.call_args.args
        self.assertEqual(meta.video_id, "UKhwU_Q-FgU")
        self.assertEqual(opts["cookiefile"], str(cookie_file))
        self.assertFalse(download)
        self.assertNotIn("player_client", opts.get("extractor_args", {}).get("youtube", {}))

    def test_unavailable_with_configured_cookies_does_not_ask_to_configure_env(self):
        cookie_file = self.root / "secrets/youtube_cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        settings = replace(self.settings, youtube_cookies=cookie_file)
        with patch.object(youtube, "_extract_info", side_effect=RuntimeError("Video unavailable")):
            with self.assertRaises(AppError) as failure:
                youtube.fetch_meta("https://youtu.be/UKhwU_Q-FgU", settings)
        message = str(failure.exception)
        self.assertIn("已配置 YouTube cookies", message)
        self.assertIn("Video unavailable", message)
        self.assertNotIn("YOUTUBE_COOKIES", message)

    def test_bot_block_with_browser_cookies_reports_existing_configuration(self):
        message = youtube._format_ytdlp_error("解析 YouTube 链接失败",
            RuntimeError("Sign in to confirm you're not a bot"), cookies_configured=True)
        self.assertIn("已配置 YouTube cookies", message)
        self.assertNotIn("当前 IP 已无法匿名解析", message)

    def test_connection_failures_report_network_instead_of_cookie_import(self):
        for detail in (
            "Unable to download API page: Connection to www.youtube.com timed out. (connect timeout=30.0)",
            "[Errno 61] Connection refused",
            "[Errno 8] nodename nor servname provided, or not known",
        ):
            for configured in (False, True):
                with self.subTest(detail=detail, cookies_configured=configured):
                    message = youtube._format_ytdlp_error("解析 YouTube 链接失败",
                        RuntimeError(detail), cookies_configured=configured)
                    self.assertIn("网络连接失败", message)
                    self.assertIn("代理", message)
                    self.assertIn(detail, message)
                    self.assertNotIn("cookies", message)
                    self.assertNotIn("YOUTUBE_COOKIES", message)

    def test_unrelated_failure_does_not_suggest_importing_cookies(self):
        message = youtube._format_ytdlp_error("解析 YouTube 链接失败",
            RuntimeError("[Errno 1] Operation not permitted"), cookies_configured=True)
        self.assertEqual(message, "解析 YouTube 链接失败：[Errno 1] Operation not permitted")


if __name__ == "__main__":
    unittest.main()
