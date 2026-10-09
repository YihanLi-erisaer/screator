import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from screator.desktop_settings import DesktopSettings
from screator.paths import AppPaths


class Vault:
    def get_password(self, service, name):
        return "existing-key" if service.startswith("StarDazz.yt2bili:") else None


class RenameCompatibilityTests(unittest.TestCase):
    def test_existing_desktop_profile_and_vault_remain_available(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = Path(folder) / "StarDazz" / "yt2bili"
            legacy.mkdir(parents=True)
            with patch("screator.paths.sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                paths = AppPaths.default()
            self.assertEqual(paths.root, legacy)
            self.assertEqual(DesktopSettings(paths, Vault()).key(), "existing-key")

    def test_new_desktop_profile_uses_lowercase_name(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("screator.paths.sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                paths = AppPaths.default()
            self.assertEqual(paths.root, Path(folder) / "StarDazz" / "screator")


if __name__ == "__main__":
    unittest.main()
