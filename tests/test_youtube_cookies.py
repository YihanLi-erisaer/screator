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


if __name__ == "__main__":
    unittest.main()
