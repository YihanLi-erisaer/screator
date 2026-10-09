from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from yt2bili import events
from yt2bili.process_manager import creation_options
from .http import request_json
from .types import TranslationError
from .process_tree import ProcessTree


def manifest():
    return json.loads(files("yt2bili.translation").joinpath("manifest.json").read_text(encoding="utf-8"))


def runtime_spec(data=None):
    data = data or manifest()
    if sys.platform == "win32" and platform.machine().upper() in ("AMD64", "X86_64"):
        return data["runtime"]
    if sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        return data["runtime_macos_arm64"]
    raise TranslationError("UNSUPPORTED_PLATFORM", "应用管理模式支持 Windows x64 和 macOS Apple Silicon；其他平台请使用外部 Ollama。")


def model_manifest_path(root):
    return Path(root) / "models/manifests/registry.ollama.ai/library" / manifest()["model"]["name"].replace(":", "/")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            events.check_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def runtime_path(root):
    spec = runtime_spec()
    return Path(root) / "runtime" / spec["version"] / ("ollama.exe" if sys.platform == "win32" else "ollama")


def model_status(address, model):
    spec = manifest()
    expected = (spec["model"]["digest"] if model == spec["model"]["name"] else
                spec.get("compatible_models", {}).get(model))
    if expected is None:
        raise TranslationError("MODEL_UNSUPPORTED", "当前不支持此本地模型。")
    version = request_json(address, "/api/version").get("version", "")
    models = request_json(address, "/api/tags", limit=1048576).get("models", [])
    for item in models:
        if item.get("name") == model or item.get("model") == model:
            if item.get("remote_model") or item.get("remote_host"):
                return {"state": "error", "code": "MODEL_NOT_LOCAL", "message": "此服务指向云模型，请安装本机模型。"}
            digest = str(item.get("digest", "")).removeprefix("sha256:")
            if digest != expected:
                return {"state": "error", "code": "MODEL_DIGEST_MISMATCH", "message": "模型摘要与支持清单不一致，请安装固定版本。"}
            return {"state": "ready", "digest": digest, "runtime_version": version, "message": "模型已安装，可进行试译。"}
    return {"state": "missing", "code": "MODEL_MISSING", "message": "本地模型未安装，请先安装翻译组件。"}


def status(config, root):
    if config["local_llm_mode"] == "external":
        try:
            return model_status(config["local_llm_base_url"], config["local_llm_model"])
        except TranslationError as exc:
            return {"state": "unavailable", "code": exc.code, "message": str(exc)}
    root = Path(root)
    marker = root / "deployment.json"
    try:
        installed = json.loads(marker.read_text(encoding="utf-8"))
        spec = runtime_spec()
        if (not runtime_path(root).is_file() or installed["model_digest"] != manifest()["model"]["digest"]
                or installed["runtime_version"] != spec["version"]):
            raise ValueError()
        model_file = model_manifest_path(root)
        if sha256(model_file) != installed["model_digest"]:
            raise ValueError()
        return {"state": "ready", "message": "本地组件已安装，将按需启动；可试译检测。", **installed}
    except (OSError, KeyError, ValueError, TranslationError):
        return {"state": "missing", "message": "尚未安装本地翻译组件。", "code": "MODEL_MISSING"}


def runtime_environment(config, root):
    env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(Path(root) / "models"),
           "OLLAMA_NO_CLOUD": "1", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1"}
    backend = config.get("local_llm_backend", "auto")
    if backend == "vulkan" and sys.platform not in {"win32", "linux"}:
        raise TranslationError("INPUT_INVALID", "Vulkan 推理支持 Windows 和 Linux；Apple Silicon 请选择自动推理。")
    if sys.platform == "darwin":
        # Ollama discovers its bundled Metal runner beside this binary.
        env.pop("OLLAMA_LLM_LIBRARY", None)
    elif backend == "cpu":
        env.update(OLLAMA_LLM_LIBRARY="cpu", OLLAMA_VULKAN="0", OLLAMA_IGPU_ENABLE="0",
                   CUDA_VISIBLE_DEVICES="-1", HIP_VISIBLE_DEVICES="-1", ROCR_VISIBLE_DEVICES="-1",
                   GGML_VK_VISIBLE_DEVICES="-1")
    elif backend == "vulkan":
        env.update(OLLAMA_LLM_LIBRARY="vulkan", OLLAMA_VULKAN="1", OLLAMA_IGPU_ENABLE="1")
    else:
        # v0.34.3 filters most integrated GPUs out unless explicitly enabled.
        # Preserve advanced device selection and explicit opt-outs from the environment.
        env.setdefault("OLLAMA_VULKAN", "1")
        env.setdefault("OLLAMA_IGPU_ENABLE", "1")
    # Do not log runtime output: model runners can include prompts in debug logs.
    env.pop("OLLAMA_DEBUG", None)
    return env


@contextmanager
def local_session(config, root, *, installing=False):
    """Own only processes created here. A busy port never grants ownership."""
    address = config["local_llm_base_url"]
    if config["local_llm_mode"] == "external":
        yield address
        return
    spec = runtime_spec()
    root = Path(root)
    binary = runtime_path(root)
    if not binary.is_file():
        raise TranslationError("LOCAL_UNAVAILABLE", "本地运行时未安装。")
    try:
        marker = json.loads((binary.parent / "installed.json").read_text(encoding="utf-8"))
        if sha256(binary) != marker["binary_sha256"]:
            raise ValueError()
    except (OSError, KeyError, ValueError):
        raise TranslationError("LOCAL_UNAVAILABLE", "本地运行时校验失败，请重新安装。") from None
    try:
        request_json(address, "/api/version", timeout=.3)
    except TranslationError:
        pass
    else:
        raise TranslationError("PORT_IN_USE", "11435 端口已被占用；请关闭占用服务或使用外部模式。")
    env = runtime_environment(config, root)
    process = subprocess.Popen([str(binary), "serve"], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **creation_options())
    tree = ProcessTree(process)
    try:
        # Startup is separate from the model inference timeout. Under load the
        # managed runtime may take longer to become ready between queued jobs.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            events.check_cancelled()
            if process.poll() is not None:
                raise TranslationError("LOCAL_UNAVAILABLE", "本地运行时启动失败，可能是端口或驱动问题。")
            try:
                version = request_json(address, "/api/version", timeout=.5).get("version")
                if version != spec["version"]:
                    raise TranslationError("VERSION_MISMATCH", "本地运行时版本不匹配。")
                break
            except TranslationError as exc:
                if exc.code == "VERSION_MISMATCH":
                    raise
                time.sleep(.1)
        else:
            raise TranslationError("STARTUP_TIMEOUT", "本地运行时启动超过 60 秒，尚未开始大模型翻译，请检查资源占用后重试。")
        yield address
    finally:
        tree.close()
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **creation_options())
                if process.poll() is None:
                    process.kill()
                process.wait()
            else:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
