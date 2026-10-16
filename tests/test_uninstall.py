import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from screator.exceptions import AppError
from screator.uninstall import profile_roots, remove_user_data


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.parent = self.home / "Library/Application Support/StarDazz"
        self.current = self.parent / "screator"
        self.legacy = self.parent / "yt2bili"
        self.roots_patch = patch("screator.uninstall.profile_roots", return_value=[self.current, self.legacy])
        self.roots_patch.start()
        self.addCleanup(self.roots_patch.stop)

    def populate(self):
        for root in (self.current, self.legacy):
            for rel in ("data/tasks.sqlite", "translation/models/weights", "translation/runtime/ollama", "work/video.mp4", "settings.json"):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture", encoding="utf-8")

    def test_removes_both_profiles_but_preserves_external_materials_and_other_apps(self):
        self.populate()
        outside = self.home / "custom-work"
        outside.mkdir()
        (outside / "video.mp4").write_text("keep", encoding="utf-8")
        other = self.parent / "another-app"
        other.mkdir()
        (self.current / "settings.json").write_text(json.dumps({"work_dir": str(outside)}))
        result = remove_user_data()
        self.assertEqual(result["removed"], [str(self.current), str(self.legacy)])
        self.assertFalse(self.current.exists())
        self.assertFalse(self.legacy.exists())
        self.assertEqual((outside / "video.mp4").read_text(), "keep")
        self.assertTrue(other.is_dir())

    def test_missing_profiles_are_not_created(self):
        self.assertEqual(remove_user_data(), {"removed": []})
        self.assertFalse(self.parent.exists())

    def test_busy_legacy_profile_prevents_any_profile_deletion(self):
        self.populate()
        # A child process also covers Windows process-scoped byte locks.
        lock = self.legacy / "execution.lock"
        script = "from pathlib import Path; import sys; from screator.locking import FileLock\nwith FileLock(Path(sys.argv[1])):\n print('ready', flush=True)\n sys.stdin.read(1)"
        child = subprocess.Popen([sys.executable, "-c", script, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(AppError):
                remove_user_data()
            for root in (self.current, self.legacy):
                self.assertTrue((root / "data/tasks.sqlite").is_file())
                self.assertTrue((root / "translation/models/weights").is_file())
        finally:
            child.communicate("x", timeout=10)

    @unittest.skipIf(os.name == "nt", "symlinks require Windows developer mode")
    def test_rejects_linked_profile_or_parent_before_deleting_anything(self):
        for link_parent in (False, True):
            with self.subTest(link_parent=link_parent), tempfile.TemporaryDirectory() as folder:
                base = Path(folder).resolve()
                outside = base / "outside"
                outside.mkdir()
                (outside / "keep").write_text("keep")
                if link_parent:
                    parent = base / "linked"
                    parent.symlink_to(outside, target_is_directory=True)
                    root = parent / "screator"
                else:
                    root = base / "screator"
                    root.symlink_to(outside, target_is_directory=True)
                with patch("screator.uninstall.profile_roots", return_value=[root]), self.assertRaises(AppError):
                    remove_user_data()
                self.assertEqual((outside / "keep").read_text(), "keep")

    @unittest.skipIf(os.name == "nt", "symlinks require Windows developer mode")
    def test_links_inside_profile_do_not_delete_external_targets(self):
        self.populate()
        outside = self.home / "external-models"
        outside.mkdir()
        (outside / "keep").write_text("keep")
        (self.current / "linked").symlink_to(outside, target_is_directory=True)
        remove_user_data()
        self.assertEqual((outside / "keep").read_text(), "keep")

    def test_concurrent_startup_after_unlock_is_preserved_and_reported(self):
        self.populate()
        original_rmdir = Path.rmdir
        sentinel = self.current / "data/new-task.sqlite"

        def concurrent_start(path):
            if path == self.current:
                sentinel.parent.mkdir()
                sentinel.write_text("new task")
            return original_rmdir(path)

        with patch.object(Path, "rmdir", concurrent_start), self.assertRaises(OSError):
            remove_user_data()
        self.assertEqual(sentinel.read_text(), "new task")

    def test_worker_entry_requires_confirmation_without_starting_service(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, str(root / "packaging/worker_entry.py"), "--uninstall-data"],
                                cwd=root, env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm", result.stderr)
        self.assertNotIn("worker.ready", result.stdout)

    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "native uninstall platforms")
    def test_worker_cleanup_command_uses_standard_roots_without_starting_service(self):
        project = Path(__file__).resolve().parent.parent
        env = {**os.environ, "HOME": str(self.home), "LOCALAPPDATA": str(self.home), "PYTHONPATH": str(project)}
        with patch("screator.uninstall.Path.home", return_value=self.home), patch.dict(os.environ, {"LOCALAPPDATA": str(self.home)}):
            roots = profile_roots()
        for root in roots:
            (root / "data").mkdir(parents=True)
            (root / "data/tasks.sqlite").write_text("fixture")
        result = subprocess.run([sys.executable, str(project / "packaging/worker_entry.py"), "--uninstall-data", "--confirm"],
                                cwd=project, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["removed"], [str(root) for root in roots])
        self.assertTrue(all(not root.exists() for root in roots))
        self.assertNotIn("worker.ready", result.stdout)


class ProfileLocationTests(unittest.TestCase):
    def test_platform_roots_include_legacy_without_creating_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            for platform, expected in (("win32", home / "StarDazz"),
                                       ("darwin", home / "Library/Application Support/StarDazz")):
                with patch("screator.uninstall.sys.platform", platform), patch("screator.uninstall.Path.home", return_value=home), patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                    self.assertEqual(profile_roots(), [expected / "screator", expected / "yt2bili"])
                    self.assertFalse(expected.exists())


if __name__ == "__main__":
    unittest.main()
