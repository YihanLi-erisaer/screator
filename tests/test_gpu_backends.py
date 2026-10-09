import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt2bili import media, events
from yt2bili.exceptions import InvalidMediaError, Yt2BiliError
from yt2bili.translation.config import DEFAULTS, from_env, snapshot, validate
from yt2bili.translation.runtime import local_session, runtime_environment, sha256
from yt2bili.translation.types import TranslationError


class InferenceBackendTests(unittest.TestCase):
    def test_auto_enables_vulkan_and_integrated_gpus_without_restricting_native_backends(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.platform", "win32"):
            env = runtime_environment(DEFAULTS, Path("components"))
        self.assertEqual(env["OLLAMA_VULKAN"], "1")
        self.assertEqual(env["OLLAMA_IGPU_ENABLE"], "1")
        self.assertNotIn("OLLAMA_LLM_LIBRARY", env)

    def test_auto_preserves_device_selection_and_explicit_opt_out(self):
        supplied = {"OLLAMA_VULKAN": "0", "OLLAMA_IGPU_ENABLE": "0", "GGML_VK_VISIBLE_DEVICES": "1"}
        with patch.dict(os.environ, supplied, clear=True), patch("sys.platform", "win32"):
            env = runtime_environment(DEFAULTS, Path("components"))
        self.assertTrue(all(env[key] == value for key, value in supplied.items()))

    def test_vulkan_override_and_cpu_disable_all_gpu_libraries(self):
        with patch.dict(os.environ, {"OLLAMA_LLM_LIBRARY": "cuda_v12"}, clear=True), patch("sys.platform", "win32"):
            env = runtime_environment({**DEFAULTS, "local_llm_backend": "vulkan"}, "components")
            self.assertEqual(env["OLLAMA_LLM_LIBRARY"], "vulkan")
            self.assertEqual(env["OLLAMA_IGPU_ENABLE"], "1")
            env = runtime_environment({**DEFAULTS, "local_llm_backend": "cpu"}, "components")
            self.assertEqual(env["OLLAMA_VULKAN"], "0")
            for key in ("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "GGML_VK_VISIBLE_DEVICES"):
                self.assertEqual(env[key], "-1")

    def test_metal_is_preserved_and_vulkan_rejected_on_macos(self):
        with patch.dict(os.environ, {"OLLAMA_LLM_LIBRARY": "vulkan"}, clear=True), patch("sys.platform", "darwin"):
            self.assertNotIn("OLLAMA_LLM_LIBRARY", runtime_environment(DEFAULTS, "components"))
            with self.assertRaises(TranslationError) as raised:
                runtime_environment({**DEFAULTS, "local_llm_backend": "vulkan"}, "components")
        self.assertEqual(raised.exception.code, "INPUT_INVALID")

    def test_backend_env_config_and_old_snapshot_defaults(self):
        with patch.dict(os.environ, {"LOCAL_LLM_BACKEND": "vulkan"}, clear=True):
            self.assertEqual(from_env()["local_llm_backend"], "vulkan")
        from types import SimpleNamespace
        self.assertEqual(snapshot(SimpleNamespace())["local_llm_backend"], "auto")
        for invalid in (None, "rocm", True, ["vulkan"]):
            with self.subTest(value=invalid), self.assertRaises(Yt2BiliError):
                validate({"local_llm_backend": invalid})
        with self.assertRaises(Yt2BiliError):
            validate({"local_llm_mode": "external", "local_llm_base_url": "http://127.0.0.1:11434",
                      "local_llm_backend": "vulkan"})

    def test_managed_launch_receives_gpu_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "ollama.exe"
            binary.write_bytes(b"fixture")
            (binary.parent / "installed.json").write_text(json.dumps({"binary_sha256": sha256(binary)}))
            process = MagicMock()
            process.poll.return_value = 0
            # First readiness probe refuses the port; next probe sees our runtime.
            process.poll.side_effect = [None, 0]
            with patch("sys.platform", "win32"), patch.dict(os.environ, {}, clear=True), \
                 patch("yt2bili.translation.runtime.runtime_spec", return_value={"version": "fixture"}), \
                 patch("yt2bili.translation.runtime.runtime_path", return_value=binary), \
                 patch("yt2bili.translation.runtime.request_json", side_effect=[TranslationError("LOCAL_UNAVAILABLE", "not running"), {"version": "fixture"}]), \
                 patch("yt2bili.translation.runtime.subprocess.Popen", return_value=process) as launch, \
                 patch("yt2bili.translation.runtime.ProcessTree"):
                with local_session(DEFAULTS, folder):
                    pass
            self.assertEqual(launch.call_args.kwargs["env"]["OLLAMA_IGPU_ENABLE"], "1")

    def test_external_session_never_launches_or_changes_server(self):
        config = {**DEFAULTS, "local_llm_mode": "external", "local_llm_base_url": "http://127.0.0.1:11434"}
        with patch("yt2bili.translation.runtime.subprocess.Popen") as launch:
            with local_session(config, "components") as address:
                self.assertEqual(address, config["local_llm_base_url"])
        launch.assert_not_called()


class ValidationBackendTests(unittest.TestCase):
    def setUp(self):
        self.info = {"vcodec": "av1", "pix_fmt": "yuv420p", "color_transfer": None,
                     "has_video": True, "has_audio": True, "duration": 3,
                     "video_duration": 3, "audio_duration": 3}
        self.env = patch.dict(os.environ, {"YT2BILI_HWACCEL": "auto", "YT2BILI_HWACCEL_DEVICE": "",
                                           "YT2BILI_VALIDATION_CACHE": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_windows_auto_prefers_cross_vendor_d3d11va(self):
        with patch.object(media.platform, "system", return_value="Windows"):
            candidates = media._validation_decode_candidates(self.info)
        self.assertEqual(candidates[0][1], "d3d11va")
        self.assertIn("d3d11", candidates[0])
        self.assertEqual(candidates[0][-2:], ["-c:v", "av1"])
        self.assertIn("cuda", candidates[1])

    def test_linux_tries_every_render_node_and_native_decoder(self):
        nodes = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]
        with patch.object(media.platform, "system", return_value="Linux"), patch.object(Path, "glob", return_value=nodes):
            candidates = media._validation_decode_candidates(self.info)
        self.assertEqual([args[-1] for args in candidates[1:]], [str(node) for node in nodes])
        self.assertTrue(all(args[args.index("-c:v") + 1] == "av1" for args in candidates[1:]))

    def test_manual_backend_and_device_are_used_without_other_gpu_backends(self):
        for system, backend, device in (("Windows", "d3d11va", "1"), ("Linux", "vaapi", "/dev/dri/renderD129"),
                                        ("Windows", "cuda", "1")):
            with self.subTest(backend=backend), patch.object(media.platform, "system", return_value=system), \
                 patch.dict(os.environ, {"YT2BILI_HWACCEL": backend, "YT2BILI_HWACCEL_DEVICE": device}):
                candidates = media._validation_decode_candidates(self.info)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0][1], backend)
                self.assertEqual(candidates[0][-2:], ["-hwaccel_device", device])

    def test_10bit_supported_but_hdr_and_unusual_formats_use_cpu(self):
        with patch.object(media.platform, "system", return_value="Windows"):
            self.assertTrue(media._validation_decode_candidates({**self.info, "pix_fmt": "yuv420p10le"}))
            for extra in ({"pix_fmt": "yuv444p"}, {"color_transfer": "smpte2084"}, {"vcodec": "mpeg4"}):
                self.assertEqual(media._validation_decode_candidates({**self.info, **extra}), [])

    def run_validation(self, effects):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            source.write_bytes(b"fixture")
            with patch.object(media.platform, "system", return_value="Windows"), \
                 patch.object(media, "probe_brief", return_value=self.info), \
                 patch.object(media, "_decode_track", side_effect=effects) as decode:
                result = media.validate_media(source, 3)
                return result, decode.call_args_list

    def test_cross_vendor_gpu_success_keeps_audio_on_cpu(self):
        result, calls = self.run_validation([3., 3.])
        self.assertTrue(result["has_video"])
        self.assertEqual(calls[0].args[3][1], "d3d11va")
        self.assertEqual(calls[1].args[1:], ("a:0", 3, []))

    def test_all_gpu_failures_restart_video_on_cpu_then_validate_audio(self):
        _, calls = self.run_validation([InvalidMediaError("no D3D"), InvalidMediaError("no CUDA"), 3., 3.])
        self.assertEqual(calls[2].args[1:], ("v:0", 3, []))
        self.assertEqual(calls[3].args[1:], ("a:0", 3, []))
        with self.assertRaises(InvalidMediaError):
            self.run_validation(InvalidMediaError("corrupt"))

    def test_cancellation_never_tries_another_backend(self):
        with self.assertRaises(events.Cancelled):
            self.run_validation(events.Cancelled())

    def test_hardware_frames_are_required_and_backend_reported(self):
        for method, pixel_format in (("d3d11va", "d3d11"), ("vaapi", "vaapi")):
            with self.subTest(method=method):
                process = MagicMock()
                process.stdout = io.StringIO("out_time_us=3000000\n")
                process.wait.return_value = 0
                process.poll.return_value = 0
                with patch.object(media.subprocess, "Popen", return_value=process) as launch, \
                     patch.object(media.events, "progress") as progress:
                    media._decode_track(Path("source.mp4"), "v:0", 3,
                                        ["-hwaccel", method, "-hwaccel_output_format", pixel_format])
                self.assertIn(f"format={pixel_format}", launch.call_args.args[0])
                self.assertIn(method.upper(), progress.call_args.kwargs["backend"])
