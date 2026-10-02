from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from yt2bili.translation.config import DEFAULTS
from yt2bili.translation.deployment import download_runtime, install, install_runtime, safe_extract_tar, uninstall_model
from yt2bili.translation.runtime import local_session, model_manifest_path, runtime_path, runtime_spec, sha256
from yt2bili.translation.jobs import TranslationJobs
from yt2bili.translation.types import TranslationError


class Response(io.BytesIO):
    def __init__(self, data, status, content_range=""):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Range": content_range}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "downloads").mkdir()
        self.payload = b"verified archive data"
        runtime = {"url": "https://github.com/ollama/ollama/releases/download/fixed/runtime.zip",
                   "sha256": hashlib.sha256(self.payload).hexdigest(), "size": len(self.payload), "archive": "zip"}
        self.spec = {"runtime": runtime, "runtime_macos_arm64": runtime}

    def test_download_resumes_only_when_content_range_matches(self):
        (self.root / "downloads/runtime.part").write_bytes(self.payload[:4])
        response = Response(self.payload[4:], 206, f"bytes 4-{len(self.payload)-1}/{len(self.payload)}")
        with patch("yt2bili.translation.deployment.manifest", return_value=self.spec), patch("yt2bili.translation.deployment.urlopen", return_value=response) as request:
            path = download_runtime(self.root)
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(request.call_args.args[0].headers["Range"], "bytes=4-")

    def test_managed_install_only_uses_the_current_pinned_model(self):
        self.assertEqual(model_manifest_path(self.root).relative_to(self.root).as_posix(),
                         "models/manifests/registry.ollama.ai/library/qwen3.5/4b")
        with self.assertRaises(TranslationError) as raised:
            install({**DEFAULTS, "local_llm_model": "qwen3:8b"}, self.root)
        self.assertEqual(raised.exception.code, "INPUT_INVALID")

    def test_ignored_range_restarts_download_without_appending(self):
        (self.root / "downloads/runtime.part").write_bytes(b"stale")
        with patch("yt2bili.translation.deployment.manifest", return_value=self.spec), patch("yt2bili.translation.deployment.urlopen", return_value=Response(self.payload,200)):
            self.assertEqual(download_runtime(self.root).read_bytes(), self.payload)

    def test_invalid_checksum_is_not_installed(self):
        with patch("yt2bili.translation.deployment.manifest", return_value=self.spec), patch("yt2bili.translation.deployment.urlopen", return_value=Response(b"x"*len(self.payload),200)):
            with self.assertRaises(TranslationError) as raised:
                download_runtime(self.root)
        self.assertEqual(raised.exception.code,"CHECKSUM_FAILED")
        self.assertFalse((self.root/'downloads/runtime.zip').exists())
        self.assertFalse((self.root/'downloads/runtime.part').exists())

    def test_busy_port_does_not_take_ownership_or_kill_service(self):
        with patch('yt2bili.translation.runtime.sys.platform','win32'), \
             patch('yt2bili.translation.runtime.platform.machine', return_value='AMD64'):
            binary=runtime_path(self.root)
        binary.parent.mkdir(parents=True);binary.write_bytes(b"fixture")
        (binary.parent/'installed.json').write_text(json.dumps({'binary_sha256':sha256(binary)}))
        with patch('yt2bili.translation.runtime.sys.platform','win32'), patch('yt2bili.translation.runtime.platform.machine',return_value='AMD64'), patch('yt2bili.translation.runtime.request_json',return_value={'version':'other'}), patch('yt2bili.translation.runtime.subprocess.Popen') as launch:
            with self.assertRaises(TranslationError) as raised:
                with local_session(DEFAULTS,self.root):pass
        self.assertEqual(raised.exception.code,'PORT_IN_USE')
        launch.assert_not_called()

    def test_slow_runtime_start_is_reported_separately_from_inference_timeout(self):
        with patch('yt2bili.translation.runtime.sys.platform','win32'), \
             patch('yt2bili.translation.runtime.platform.machine', return_value='AMD64'):
            binary = runtime_path(self.root)
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"fixture")
        (binary.parent / "installed.json").write_text(json.dumps({"binary_sha256": sha256(binary)}))
        process = Mock()
        process.poll.side_effect = [None, 1]
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 61]), sleep=Mock())
        with patch("yt2bili.translation.runtime.sys.platform", "win32"), \
             patch("yt2bili.translation.runtime.platform.machine", return_value="AMD64"), \
             patch("yt2bili.translation.runtime.request_json", side_effect=TranslationError("LOCAL_UNAVAILABLE", "not ready")), \
             patch("yt2bili.translation.runtime.subprocess.Popen", return_value=process), \
             patch("yt2bili.translation.runtime.ProcessTree"), \
             patch("yt2bili.translation.runtime.time", clock):
            with self.assertRaises(TranslationError) as raised:
                with local_session(DEFAULTS, self.root):
                    self.fail("runtime must not be ready")
        self.assertEqual(raised.exception.code, "STARTUP_TIMEOUT")
        self.assertIn("尚未开始大模型翻译", str(raised.exception))

    def test_macos_arm64_runtime_archive_is_verified_and_executable(self):
        archive = self.root / "runtime.tgz"
        with tarfile.open(archive, "w:gz") as bundle:
            item = tarfile.TarInfo("ollama")
            item.size = len(self.payload)
            item.mode = 0o755
            bundle.addfile(item, io.BytesIO(self.payload))
        spec = {"runtime_macos_arm64": {"version": "0.34.3", "archive": "tgz",
                                         "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}}
        with patch("yt2bili.translation.deployment.manifest", return_value=spec), \
             patch("yt2bili.translation.deployment.runtime_spec", return_value=spec["runtime_macos_arm64"]), \
             patch("yt2bili.translation.deployment.runtime_path", return_value=self.root / "runtime/0.34.3/ollama"):
            install_runtime(self.root, archive)
        binary = self.root / "runtime/0.34.3/ollama"
        self.assertEqual(binary.read_bytes(), self.payload)
        if os.name != "nt":
            self.assertTrue(binary.stat().st_mode & 0o111)

    def test_platform_runtime_selection(self):
        spec = {"runtime": {"version": "windows"}, "runtime_macos_arm64": {"version": "mac"}}
        with patch("yt2bili.translation.runtime.sys.platform", "darwin"), \
             patch("yt2bili.translation.runtime.platform.machine", return_value="arm64"):
            self.assertEqual(runtime_spec(spec)["version"], "mac")
        with patch("yt2bili.translation.runtime.sys.platform", "win32"), \
             patch("yt2bili.translation.runtime.platform.machine", return_value="AMD64"):
            self.assertEqual(runtime_spec(spec)["version"], "windows")

    def test_macos_runtime_archive_rejects_path_escape(self):
        archive = self.root / "unsafe.tgz"
        with tarfile.open(archive, "w:gz") as bundle:
            item = tarfile.TarInfo("../outside")
            item.size = len(self.payload)
            bundle.addfile(item, io.BytesIO(self.payload))
        with self.assertRaises(TranslationError) as raised:
            safe_extract_tar(archive, self.root / "staging")
        self.assertEqual(raised.exception.code, "INVALID_ARCHIVE")
        self.assertFalse((self.root / "outside").exists())

    def test_jobs_restore_as_interrupted_instead_of_success(self):
        (self.root/'jobs.json').write_text(json.dumps({'one':{'job_id':'one','operation_id':'operation','kind':'install','state':'running'}}))
        jobs=TranslationJobs(self.root,lambda *args:None)
        self.addCleanup(jobs.close)
        self.assertEqual(jobs.get('one')['state'],'interrupted')
        self.assertFalse(jobs.active())

    def test_uninstall_removes_managed_model_but_preserves_runtime_and_download(self):
        models = self.root / "models/blobs"
        models.mkdir(parents=True)
        (models / "sha256-fixture").write_bytes(b"model")
        (self.root / "deployment.json").write_text("{}")
        (self.root / "runtime").mkdir()
        (self.root / "runtime/ollama.exe").write_bytes(b"runtime")
        (self.root / "downloads/runtime.zip").write_bytes(b"archive")
        result = uninstall_model(DEFAULTS, self.root)
        self.assertTrue(result["uninstalled"])
        self.assertFalse((self.root / "models").exists())
        self.assertFalse((self.root / "deployment.json").exists())
        self.assertTrue((self.root / "runtime/ollama.exe").exists())
        self.assertTrue((self.root / "downloads/runtime.zip").exists())
        self.assertTrue(uninstall_model(DEFAULTS, self.root)["uninstalled"])

    def test_uninstall_rejects_external_mode_without_touching_model(self):
        models = self.root / "models"
        models.mkdir()
        (models / "keep").write_bytes(b"model")
        with self.assertRaises(TranslationError) as raised:
            uninstall_model({**DEFAULTS, "local_llm_mode": "external"}, self.root)
        self.assertEqual(raised.exception.code, "INPUT_INVALID")
        self.assertTrue((models / "keep").exists())

    def test_uninstall_rejects_link_outside_managed_directory(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_bytes(b"model")
        with tempfile.TemporaryDirectory() as other:
            link = Path(other) / "models"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Creating directory links is unavailable")
            with self.assertRaises(TranslationError) as raised:
                uninstall_model(DEFAULTS, Path(other))
            self.assertEqual(raised.exception.code, "UNINSTALL_FAILED")
            self.assertTrue((outside / "keep").exists())
