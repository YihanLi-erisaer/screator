from __future__ import annotations

import base64
import io
import json
import hashlib
import uuid
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from collections import deque
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from screator import __version__, bili_upload, youtube, translate, publications, history_transfer
from screator.db import Task, TaskStore
from screator.desktop_auth import LoginSession, account_status, validate_login
from screator.desktop_settings import DesktopSettings, atomic_json
from screator.exceptions import AppError
from screator.process_manager import creation_options
from screator.scheduler import Scheduler
from screator.accounts import AccountService
from screator.data_center import DataCenter
from screator.identity import VIDEO_ID, parse_single_video_url
from screator.locking import FileLock, account_guard, coordination_dir, work_lock
from dataclasses import replace
from screator.tools import find_tool


def parse_urls(text):
    # Internal compatibility helper; all public input is strictly one URL.
    return [parse_single_video_url(text)]

class DesktopService:
    def __init__(self, paths, emit, vault=None, config=None):
        self.paths, self.output = paths, emit
        self._closed = False
        self.logs = deque(maxlen=1500)
        self.logs_lock = threading.Lock()
        from screator.resource_monitor import ResourceMonitor
        self.resource_monitor = ResourceMonitor()
        self.mutation = threading.RLock()
        self.login = None
        self.auth_state = {"status": "idle"}
        self.config = config or DesktopSettings(paths, vault)
        self.owner_lock = FileLock(paths.root / "execution.lock")
        self.owner_lock.__enter__()
        try:
            self.store = TaskStore(paths.root / "data/tasks.sqlite", self.emit)
            from screator.materials import MaterialLibrary
            self.materials = MaterialLibrary(paths.root / "materials", self.store)
            history_transfer.initialize(self.store)
            self.accounts = AccountService(paths.root, self.store, self.emit)
            self.data_center = DataCenter(self.store, self.accounts)
            from screator.douyin import DouyinService
            from screator.acfun import AcfunService
            from screator.youtube_upload import YoutubeUploadService
            self.douyin = DouyinService(self.store, self.config, self.emit)
            self.acfun = AcfunService(self.store, self.config, self.emit)
            self.youtube_upload = YoutubeUploadService(self.store, self.config, self.emit)
            from screator.social_upload import PLATFORMS, SocialUploadService
            self.social = {platform: SocialUploadService(self.store, self.config, self.emit, platform) for platform in PLATFORMS}
            from screator.xiaohongshu import XiaohongshuService
            self.xiaohongshu = XiaohongshuService(self.store, self.config, self.emit)
            self.scheduler = Scheduler(self.store, self.config, self.emit, self.accounts, self.douyin, self.acfun, self.youtube_upload, self.social, self.xiaohongshu)
            self.scheduler.materials = self.materials
        except BaseException:
            self.owner_lock.__exit__(None, None, None)
            raise
        from screator.translation.jobs import TranslationJobs
        self.translation_jobs = TranslationJobs(paths.root / "translation", self.emit)
        self.migration_notes = []
        legacy_cookie = paths.root / "secrets/bili_cookies.json"
        if not self.store.accounts(True) and legacy_cookie.is_file():
            try:
                if legacy_cookie.stat().st_size > 5_000_000:
                    raise ValueError("oversize")
                self.accounts.bind(json.loads(legacy_cookie.read_text(encoding="utf-8-sig")), verify=False)
                self.migration_notes.append("旧登录文件已导入为待验证账号；历史任务仍需确认归属。")
            except (ValueError, OSError, AppError):
                self.migration_notes.append("旧登录文件无法确认 UID，已保留原文件，请重新登录。")
        self._closed = False
        os.environ["SCREATOR_BIN_DIR"] = str(paths.resources / "bin")
        self.apply_environment()

    def apply_environment(self):
        os.environ["SCREATOR_HWACCEL"] = self.config.values["hwaccel"]
        os.environ["SCREATOR_VALIDATION_CACHE"] = "1" if self.config.values["validation_cache"] else "0"

    def emit(self, event, payload):
        if event in ("xiaohongshu.auth.changed", "xiaohongshu.queue.resume") and hasattr(self, "scheduler"):
            lane = self.scheduler.xiaohongshu_lane
            if event == "xiaohongshu.auth.changed":
                lane.wake(credentials=True)
            else:
                with lane.condition:
                    for item in lane.items: item.wait_reason, item.deadline = "", 0
                    lane.condition.notify_all()
        if event == "auth.status":
            if self.login and payload.get("session_id") != self.login.session_id:
                return
            self.auth_state = {**self.auth_state, **payload}
            if payload.get("status") not in ("waiting", "scanned"):
                self.auth_state.pop("qrcode", None)
        if event == "accounts.changed" and hasattr(self, "scheduler"):
            self.scheduler.sync_accounts(payload.get("account_id"))
        if event == "douyin.auth.changed" and hasattr(self, "scheduler"):
            self.scheduler.douyin_lane.wake(credentials=True)
        if event == "acfun.auth.changed" and hasattr(self, "scheduler"):
            self.scheduler.acfun_lane.wake(credentials=True)
        if event == "youtube.auth.changed" and hasattr(self, "scheduler"):
            self.scheduler.youtube_lane.wake(credentials=True)
        if event in ("social.auth.changed", "social.queue.resume") and hasattr(self, "scheduler"):
            lane = self.scheduler.social_lanes[payload["platform"]]
            if event == "social.auth.changed": lane.wake(credentials=True)
            else:
                with lane.condition:
                    for item in lane.items: item.wait_reason, item.deadline = "", None
                    lane.condition.notify_all()
        if event == "youtube.queue.resume" and hasattr(self, "scheduler"):
            lane = self.scheduler.youtube_lane
            with lane.condition:
                for item in lane.items:
                    item.wait_reason, item.deadline = "", None
                lane.condition.notify_all()
        if event == "acfun.queue.resume" and hasattr(self, "scheduler"):
            lane = self.scheduler.acfun_lane
            with lane.condition:
                for item in lane.items:
                    item.wait_reason, item.deadline = "", None
                lane.condition.notify_all()
        if event == "douyin.queue.resume" and hasattr(self, "scheduler"):
            lane = self.scheduler.douyin_lane
            with lane.condition:
                for item in lane.items:
                    if item.wait_reason == "rate_limited": item.wait_reason, item.deadline = "", None
                lane.condition.notify_all()
        self.output(event, payload)
        if (event in ("queue.changed", "translation.job.finished")
                and hasattr(self, "translation_jobs") and self.scheduler.closing and not self._closed):
            self.output("system.shutdown_status", self.shutdown_status())

    def add_log(self, entry):
        with self.logs_lock:
            self.logs.append(entry)
        self.output("task.log", entry)

    def ensure_idle(self):
        if self.scheduler.snapshot()["active"] or self.translation_jobs.active():
            raise AppError("请等待当前任务结束后修改设置或账号。")

    def task(self, task_id):
        if not isinstance(task_id, str) or len(task_id) > 100:
            raise AppError("任务 ID 无效。")
        return self.store.require(task_id)

    def ensure_inactive(self, task_id):
        task = self.task(task_id)
        job = self.store.get_job(task.task_id) or {}
        if job.get("owner_session_id") == self.scheduler.session_id and job.get("execution_state") in ("queued", "running", "waiting"):
            raise AppError("任务正在执行，请先等待或取消。")

    def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise AppError("请求参数必须为对象。")
        if self.scheduler.closing and method not in ("system.prepare_shutdown", "system.shutdown_status"):
            raise AppError("应用正在退出。")
        handlers = {
            "materials.libraries": self.materials.libraries, "materials.create_library": self.materials.create,
            "materials.list": self.materials.list, "materials.rename": self.materials.rename,
            "materials.import": self.materials.import_files,
            "materials.delete": self.materials.delete, "materials.open": self.materials.open,
            "materials.download": self.create_download,
            "system.health": self.health, "system.diagnostics": self.diagnostics,
            "system.resources": self.resource_monitor.snapshot,
            "settings.get": self.config.public, "settings.status": self.config.service_status,
            "settings.update": self.update_settings,
            "translation.status": self.translation_status, "translation.test": self.translation_test,
            "translation.install": self.translation_install, "translation.uninstall": self.translation_uninstall,
            "translation.jobs.get": self.translation_jobs.get,
            "translation.jobs.cancel": self.translation_jobs.cancel, "tasks.retranslate": self.retranslate,
            "credentials.set": self.set_key, "credentials.test": self.test_key,
            "tasks.create": self.create, "tasks.list": self.list_tasks, "tasks.get": self.get_task,
            "data_center.list": self.data_center.list,
            "history.export": self.export_history, "history.import": self.import_history,
            "history.delete": self.delete_history, "history.delete_all": self.delete_all_history,
            "tasks.retry": self.retry, "tasks.cancel": self.cancel, "tasks.submit": self.submit,
            "tasks.repair": self.repair, "tasks.update_metadata": self.update_metadata,
            "tasks.resolve": self.resolve, "tasks.cover": self.cover,
            "tasks.open_folder": self.open_folder,
            "image_posts.inspect": self.inspect_images, "image_posts.create": self.create_image_post,
            "image_posts.images": self.post_images,
            "xiaohongshu.auth.status": self.xiaohongshu.status,
            "xiaohongshu.auth.start": self.xiaohongshu.start,
            "xiaohongshu.auth.poll": self.xiaohongshu.poll,
            "xiaohongshu.auth.cancel": self.xiaohongshu.cancel,
            "xiaohongshu.auth.clear": self.xiaohongshu.clear,
            "xiaohongshu.accounts.archive": self.xiaohongshu.archive,
            "xiaohongshu.accounts.resume_uploads": self.xiaohongshu.resume,
            "auth.status": self.auth_status, "auth.login.start": self.login_start,
            "auth.login.cancel": self.login_cancel, "auth.renew": self.renew,
            "auth.import": self.import_cookies, "auth.youtube_export": self.export_youtube,
            "auth.clear": self.clear_auth,
            "data.import": self.import_data,
            "accounts.list": lambda: {"items": self.accounts.list(True), "limit": 5},
            "accounts.add": self.login_start, "accounts.rename": self.accounts.rename,
            "accounts.archive": self.archive_account, "accounts.resume_uploads": self.resume_uploads,
            "accounts.verify": self.accounts.verify,
            "tasks.bind_legacy_account": self.bind_legacy,
            "douyin.auth.status": self.douyin.status, "douyin.auth.start": self.douyin.start,
            "douyin.auth.configure": self.configure_douyin,
            "douyin.auth.cancel": lambda: self.douyin.request("POST", "/v1/auth/cancel"),
            "douyin.auth.clear": self.douyin.clear, "douyin.accounts.archive": self.douyin.archive,
            "douyin.accounts.resume_uploads": self.douyin.resume,
            "acfun.auth.status": self.acfun.status, "acfun.auth.start": self.acfun.start,
            "acfun.auth.poll": self.acfun.poll, "acfun.auth.cancel": self.acfun.cancel,
            "acfun.auth.clear": self.acfun.clear, "acfun.accounts.archive": self.acfun.archive,
            "acfun.accounts.resume_uploads": self.acfun.resume,
            "acfun.channels": self.acfun.channels,
            "youtube.auth.status": self.youtube_upload.status, "youtube.auth.start": self.youtube_upload.start,
            "youtube.auth.configure": self.configure_youtube,
            "youtube.auth.poll": self.youtube_upload.poll, "youtube.auth.cancel": self.youtube_upload.cancel,
            "youtube.auth.clear": self.youtube_upload.clear, "youtube.accounts.archive": self.youtube_upload.archive,
            "youtube.accounts.resume_uploads": self.youtube_upload.resume,
            "youtube.categories": self.youtube_upload.categories,
            "social.auth.status": self.social_status, "social.auth.bind": self.bind_social,
            "social.auth.clear": self.clear_social, "social.accounts.archive": self.archive_social,
            "social.accounts.resume_uploads": self.resume_social,
            "social.creator_info": self.social_creator, "social.publications.check": self.check_social_publication,
            "social.publications.preview": self.preview_social_video,
            "acfun.verification": self.acfun.verification,
            "acfun.verification.complete": self.complete_acfun_verification,
            "acfun.verification.refresh": self.refresh_acfun_verification,
            "publications.update_metadata": self.update_publication,
            "publications.retry": self.retry_publication, "publications.abandon": self.abandon_publication,
            "publications.resolve": self.resolve_publication, "publications.cancel": self.cancel_publication,
            "system.prepare_shutdown": self.prepare_shutdown,
            "system.shutdown_status": self.shutdown_status,
            "logs.tail": self.log_tail, "logs.export": self.log_export,
        }
        handler = handlers.get(method)
        if handler is None:
            raise AppError("不支持的方法：" + str(method))
        return handler(**params)

    def health(self):
        from screator.db import SCHEMA_VERSION
        capabilities = ["single_url", "account_lanes", "graceful_shutdown", "local_translation_v1", "douyin_sync_v1", "acfun_sync_v1", "youtube_sync_v1", "social_sync_v1", "materials_v1", "local_publish_v1", "image_posts_v1"]
        capabilities.append("xiaohongshu_sync_v1")
        return {"protocol_version": 2, "schema_version": SCHEMA_VERSION, "account_limit": 5,
                "capabilities": capabilities,
                "migration_notes": self.migration_notes, "version": __version__,
                "data_dir": str(self.paths.root)}

    def translation_status(self):
        from screator.translation.runtime import status, manifest
        from screator.translation.runtime import runtime_spec
        try:
            selected_runtime = runtime_spec(manifest())
        except Exception:
            selected_runtime = manifest()["runtime"]
        return {"local": status(self.config.values, self.paths.root / "translation"),
                "primary": self.config.values["translation_primary"],
                "fallback_enabled": self.config.values["translation_fallback_enabled"],
                "model": manifest()["model"], "runtime": selected_runtime}

    def translation_install(self, operation_id, model_id="qwen3.5:4b", offline_path=None):
        if model_id != "qwen3.5:4b":
            raise AppError("不支持的模型。")
        from screator.translation.deployment import install
        with self.mutation:
            if self.scheduler.snapshot()["active"]:
                raise AppError("请等待视频任务结束后安装组件。")
            config = dict(self.config.values)
            return self.translation_jobs.start("install", operation_id,
                lambda: install(config, self.paths.root / "translation", offline_path=offline_path))

    def translation_uninstall(self, operation_id):
        from screator.translation.deployment import uninstall_model

        with self.mutation:
            if self.scheduler.snapshot()["active"]:
                raise AppError("请等待视频任务结束后卸载模型。")
            config = dict(self.config.values)
            if config["local_llm_mode"] != "managed":
                raise AppError("外部模式的模型请在外部 Ollama 中管理。")
            return self.translation_jobs.start("uninstall", operation_id,
                lambda: uninstall_model(config, self.paths.root / "translation"))

    def translation_test(self, provider, operation_id):
        if provider not in ("local_llm", "deepl"):
            raise AppError("不支持的翻译服务。")
        from dataclasses import replace
        from screator.translation.service import translate as translate_group
        with self.mutation:
            if self.scheduler.snapshot()["active"]:
                raise AppError("请等待视频任务结束后试译。")
            settings = replace(self.config.build(), translation_primary=provider, translation_fallback_enabled=False)
            return self.translation_jobs.start("test:" + provider, operation_id,
                lambda: asdict(translate_group(settings, "A better workflow", "Build useful tools. Keep version 2.0.", "en", 80, 1000)))

    def retranslate(self, task_id, operation_id, replace_edited=False):
        def action():
            self.ensure_inactive(task_id)
            task = self.task(task_id)
            if task.source_kind != "youtube":
                raise AppError("只有 YouTube 搬运任务可以重新翻译。")
            if task.status != "ready":
                raise AppError("只能重新翻译尚未投稿且已准备好的任务。")
            record = self.store.translation(task_id) or {}
            if record.get("user_edited") and replace_edited is not True:
                raise AppError("重新翻译将替换已编辑内容，请确认后继续。")
            saved = (self.store.get_job(task_id) or {}).get("settings", self.config.snapshot())
            from screator.translation.config import DEFAULTS
            from screator.translation.tasks import sync_files
            saved = {**saved, **{k: self.config.values[k] for k in DEFAULTS}}
            old_title, old_description = task.title_zh, task.desc_zh
            task.title_zh = task.desc_zh = ""
            task.metadata_revision += 1
            self.store.save_translation(task, {
                "state": "queued", "user_edited": False,
                "revision": record.get("revision", 0),
                "config_snapshot": {k: saved[k] for k in DEFAULTS},
            })
            for publication in publications.items(self.store, task.task_id):
                if publication["status"] != "ready":
                    continue
                if publication["platform"] == "bilibili":
                    publications.change(self.store, publication["publication_id"], text="")
                elif publication["platform"] == "douyin" and publication["text"] == old_title:
                    publications.change(self.store, publication["publication_id"], text="")
                elif publication["platform"] in ("acfun", "youtube", "twitter", "facebook", "tiktok"):
                    preview = json.loads(publication["snapshot"])
                    preview["description"] = ""
                    if publication["platform"] == "tiktok": preview["consent"] = False
                    if preview.get("title_source") == "generated":
                        preview["title"] = ""
                    publications.change(self.store, publication["publication_id"],
                                        snapshot=json.dumps(preview, ensure_ascii=False))
            self.scheduler.add(task, "retranslate", saved, stage="upload")
            try:
                sync_files(task, task.work_dir)
            except OSError:
                task.title_zh, task.desc_zh = old_title, old_description
                try:
                    sync_files(task, task.work_dir)
                except OSError:
                    pass
                raise
            return {"queued": True}
        return self.operation(operation_id, "tasks.retranslate", action, {"task_id": task_id, "replace_edited": replace_edited})

    def diagnostics(self):
        tools = []
        for name in ("ffmpeg", "ffprobe", "biliup", "deno", "node"):
            path = find_tool(name, self.paths.resources / "bin")
            found = shutil.which(path)
            version = "未找到"
            if found:
                try:
                    args = [path, "-version" if name.startswith("ff") else "--version"]
                    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                            timeout=10, **creation_options())
                    version = (result.stdout or result.stderr).splitlines()[0][:180]
                    found = found if result.returncode == 0 else None
                except (OSError, subprocess.TimeoutExpired, IndexError):
                    found = None
                    version = "无法运行"
            tools.append({"name": name, "path": path, "available": bool(found), "version": version})
        work = Path(self.config.values["work_dir"])
        work.mkdir(parents=True, exist_ok=True)
        return {"tools": tools, "free_bytes": shutil.disk_usage(work).free, "work_dir": str(work)}

    def update_settings(self, values):
        if not isinstance(values, dict): raise AppError("设置参数必须为对象。")
        with self.mutation:
            self.ensure_idle()
            if values.get("douyin_broker_url", self.config.values["douyin_broker_url"]) != self.config.values["douyin_broker_url"] and self.douyin.account():
                raise AppError("更换抖音授权服务前须先归档抖音账号。")
            result = self.config.update(values)
            self.apply_environment()
            return result

    def set_key(self, value):
        with self.mutation:
            self.ensure_idle()
            self.config.set_key(value)
            return {"saved": True}

    def test_key(self):
        import deepl
        key = self.config.key()
        if not key:
            raise AppError("请先保存 DeepL API 密钥。")
        try:
            usage = deepl.Translator(key).get_usage()
            return {"ok": True, "used": usage.character.count, "limit": usage.character.limit}
        except Exception as exc:
            raise AppError("DeepL 检测失败，请检查密钥、配额或网络。") from exc

    def operation(self, operation_id, method, action, params=None, preflight=None):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", operation_id):
            raise AppError("缺少有效操作 ID。")
        fingerprint = hashlib.sha256(json.dumps(params or {}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.mutation:
            previous = self.store.operation(operation_id, method, request_hash=fingerprint)
            if previous is not None:
                return previous
            if self.scheduler.closing:
                raise AppError("应用正在退出。")
            if preflight:
                preflight()
            with self.store.transaction():
                previous = self.store.operation(operation_id, method, request_hash=fingerprint)
                if previous is not None:
                    return previous
                result = action()
                self.store.operation(operation_id, method, result, request_hash=fingerprint)
            self.scheduler.signal.set()
            return result

    def create(self, url, operation_id, account_id=None, mode="preview", sync_douyin=False, douyin_account_id=None, douyin_binding_revision=None,
               sync_acfun=False, acfun_account_id=None, acfun_binding_revision=None,
               sync_youtube=False, youtube_account_id=None, youtube_binding_revision=None,
               local_path=None, title="", description="", cover_path=None, sync_social=None):
        if type(sync_douyin) is not bool: raise AppError("同步抖音必须为开关值。")
        if type(sync_acfun) is not bool: raise AppError("同步 AcFun 必须为开关值。")
        if type(sync_youtube) is not bool: raise AppError("同步 YouTube 必须为开关值。")
        dy_account = ac_account = None
        yt_account = None
        social_accounts = {}
        social_targets = sync_social if sync_social is not None else []
        if (not isinstance(social_targets, list) or len(social_targets) > 3 or any(not isinstance(v, dict)
                or set(v) - {"platform", "account_id", "binding_revision"} or v.get("platform") not in self.social for v in social_targets)
                or len({v["platform"] for v in social_targets}) != len(social_targets)):
            raise AppError("同步平台目标无效。")
        ac_tags = None
        local_cover = ""
        if cover_path is not None:
            if not isinstance(cover_path, str):
                raise AppError("封面文件路径必须为字符串。")
            local_cover = cover_path.strip()
        if local_cover:
            if local_path is None:
                raise AppError("自定义封面仅支持本地视频任务。")
            cover = Path(local_cover)
            if not cover.is_absolute() or not cover.is_file() or cover.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                raise AppError("请选择一个存在的封面图片文件（JPG、PNG、WebP、BMP）。")
            try:
                from PIL import Image
                with Image.open(cover) as image:
                    image.verify()
            except Exception as exc:
                raise AppError("封面图片无法读取，请选择有效的图片文件。") from exc
            local_cover = str(cover.resolve())
        if local_path is not None:
            from screator.materials import EXTENSIONS
            path = Path(local_path)
            if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in EXTENSIONS["video"]:
                raise AppError("请选择一个存在的本地视频文件。")
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 80:
                raise AppError("请填写 1 至 80 字的标题。")
            if not isinstance(description, str) or len(description) > (1000 if sync_acfun else 2000):
                raise AppError("简介超出平台长度限制。")
            video_id, canonical = "local-" + uuid.uuid4().hex, ""
        else:
            video_id, canonical = parse_single_video_url(url)
        if mode not in ("preview", "auto"):
            raise AppError("任务模式无效。")
        if not account_id or not isinstance(account_id, str):
            raise AppError("请选择本次投稿的 Bilibili 账号。")
        def preflight():
            nonlocal dy_account, ac_account, ac_tags, yt_account
            for target in social_targets:
                platform = target["platform"]
                social_accounts[platform] = self.social[platform].check(target.get("account_id"), target.get("binding_revision"), auto=mode == "auto")
            if sync_youtube:
                yt_account = self.youtube_upload.check(youtube_account_id, youtube_binding_revision, auto=mode == "auto")
            if sync_douyin:
                dy_account = self.douyin.check(douyin_account_id, douyin_binding_revision, auto=mode == "auto")
            if sync_acfun:
                from screator.acfun import tags_from_bilibili
                ac_account = self.acfun.check(acfun_account_id, acfun_binding_revision, auto=mode == "auto")
                ac_tags = tags_from_bilibili(self.config.values["bili_tags"])
                if mode == "auto" and not self.config.values["acfun_channel_id"]:
                    raise AppError("自动投稿 AcFun 前，请在投稿默认值中填写 AcFun 分区 ID。")
                if self.config.values["acfun_channel_id"]:
                    self.acfun.validate_channel(self.config.values["acfun_channel_id"])
            account = self.store.account(account_id)
            if local_path is None and self.translation_jobs.active():
                raise AppError("请等待翻译组件操作结束后创建任务。")
            if mode == "auto" and account["auth_state"] != "valid":
                self.accounts.verify(account_id)
        def action():
            account = self.store.account(account_id)
            old = self.store.for_account(video_id, account_id)
            if old:
                selected = {"bilibili"} | ({"douyin"} if sync_douyin else set()) | ({"acfun"} if sync_acfun else set()) | ({"youtube"} if sync_youtube else set())
                selected.update(social_accounts)
                existing = {p["platform"] for p in publications.items(self.store, old.task_id)}
                if selected != existing:
                    raise AppError("此 Bilibili 账号已有同视频任务，目标不可追加；请继续原任务。")
                return {"task_id": old.task_id, "account_id": account_id, "created": False, "status": old.status}
            task = Task(video_id, canonical, "pending", task_id=str(uuid.uuid4()), account_id=account_id,
                        account_uid_snapshot=account["uid"], account_name_snapshot=account["nickname"] or account["uid"])
            if local_path is not None:
                task.source_kind, task.local_path = "local", str(path.resolve())
                task.local_cover_path = local_cover
                task.title_orig = task.title_zh = title.strip()
                task.desc_orig = task.desc_zh = description
            self.store.upsert(task)
            publications.ensure_bili(self.store, task)
            if sync_douyin:
                account_now = self.douyin.account()
                if not account_now or account_now != dy_account:
                    raise AppError("抖音账号状态已变化，请重试。")
                duplicate = self.store._conn.execute("SELECT task_id FROM task_publications WHERE platform='douyin' AND account_id=? AND source_video_id=?", (dy_account["account_id"], video_id)).fetchone()
                if duplicate: raise AppError("此抖音账号已有同视频任务：" + duplicate[0] + "；请取消同步或继续原任务。")
                self.store._conn.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id) VALUES(?,?,'douyin',?,?)", (str(uuid.uuid4()), task.task_id, dy_account["account_id"], video_id))
                dy = publications.for_platform(self.store, task.task_id, "douyin")
                publications.change(self.store, dy["publication_id"], snapshot=json.dumps({k: dy_account[k] for k in ("client_key", "open_id", "nickname", "binding_revision")}))
            if sync_acfun:
                account_now = self.acfun.account()
                if not account_now or account_now != ac_account:
                    raise AppError("AcFun 账号状态已变化，请重试。")
                duplicate = self.store._conn.execute("SELECT task_id FROM task_publications WHERE platform='acfun' AND account_id=? AND source_video_id=?", (ac_account["account_id"], video_id)).fetchone()
                if duplicate: raise AppError("此 AcFun 账号已有同视频任务：" + duplicate[0] + "；请继续原任务。")
                self.store._conn.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id) VALUES(?,?,'acfun',?,?)", (str(uuid.uuid4()), task.task_id, ac_account["account_id"], video_id))
                ac = publications.for_platform(self.store, task.task_id, "acfun")
                publications.change(self.store, ac["publication_id"], snapshot=json.dumps({"user_id": ac_account["user_id"], "nickname": ac_account["nickname"],
                    "binding_revision": ac_account["binding_revision"], "adapter_version": ac_account["adapter_version"],
                    "channel_id": self.config.values["acfun_channel_id"], "tags": ac_tags}, ensure_ascii=False))
            if sync_youtube:
                from screator.youtube_upload import tags_from_bilibili
                if self.youtube_upload.account() != yt_account:
                    raise AppError("YouTube 账号状态已变化，请重试。")
                if self.store._conn.execute("SELECT 1 FROM task_publications WHERE platform='youtube' AND account_id=? AND source_video_id=?", (yt_account["account_id"], video_id)).fetchone():
                    raise AppError("此 YouTube 频道已有同视频任务，请继续原任务。")
                self.store._conn.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id) VALUES(?,?,'youtube',?,?)", (str(uuid.uuid4()), task.task_id, yt_account["account_id"], video_id))
                yt = publications.for_platform(self.store, task.task_id, "youtube")
                publications.change(self.store, yt["publication_id"], snapshot=json.dumps({
                    "channel_id": yt_account["channel_id"], "nickname": yt_account["nickname"],
                    "binding_revision": yt_account["binding_revision"], "category_id": self.config.values["youtube_category_id"],
                    "privacy_status": self.config.values["youtube_privacy_status"], "made_for_kids": self.config.values["youtube_made_for_kids"],
                    "tags": tags_from_bilibili(self.config.values["bili_tags"])}, ensure_ascii=False))
            for platform, social_account in social_accounts.items():
                if self.social[platform].account() != social_account: raise AppError("同步账号状态已变化，请重试。")
                if self.store._conn.execute("SELECT 1 FROM task_publications WHERE platform=? AND account_id=? AND source_video_id=?", (platform, social_account["account_id"], video_id)).fetchone():
                    raise AppError("此平台账号已有同视频任务，请继续原任务。")
                publication_id = str(uuid.uuid4())
                self.store._conn.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id) VALUES(?,?,?,?,?)", (publication_id, task.task_id, platform, social_account["account_id"], video_id))
                snapshot = {"subject_id": social_account["subject_id"], "nickname": social_account["nickname"], "binding_revision": social_account["binding_revision"],
                    "title_source": "generated", "privacy_level": "", "allow_comment": False, "allow_duet": False, "allow_stitch": False,
                    "content_disclosure": False, "brand_organic": False, "brand_content": False, "is_aigc": False, "consent": False, "original_content": False, "cover_timestamp_ms": 0}
                publications.change(self.store, publication_id, snapshot=json.dumps(snapshot, ensure_ascii=False))
            self.scheduler.add(task, mode)
            return {"task_id": task.task_id, "account_id": account_id, "created": True, "status": task.status}
        params = {"url": canonical, "account_id": account_id, "mode": mode}
        if local_path is not None:
            params.update(local_path=str(path.resolve()), title=title, description=description, cover_path=local_cover)
        if sync_douyin: params.update(sync_douyin=True, douyin_account_id=douyin_account_id, douyin_binding_revision=douyin_binding_revision)
        if sync_acfun: params.update(sync_acfun=True, acfun_account_id=acfun_account_id, acfun_binding_revision=acfun_binding_revision)
        if sync_youtube: params.update(sync_youtube=True, youtube_account_id=youtube_account_id, youtube_binding_revision=youtube_binding_revision)
        if sync_social is not None: params["sync_social"] = sync_social
        return self.operation(operation_id, "tasks.create", action, params, preflight)

    def inspect_images(self, paths):
        from screator.image_posts import inspect
        return inspect(paths)

    def post_images(self, task_id):
        from screator.image_posts import prepared_paths, inspect
        task = self.task(task_id)
        if task.source_kind != "image_post" or task.image_paths == "[]":
            return {"items": []}
        return inspect([str(p) for p in prepared_paths(task)])

    def create_image_post(self, paths, text, account_id, operation_id, mode="preview",
                          sync_xiaohongshu=False, xiaohongshu_account_id=None,
                          xiaohongshu_binding_revision=None, xiaohongshu_title=""):
        from screator import image_posts
        if type(sync_xiaohongshu) is not bool: raise AppError("同步小红书必须为开关值。")
        image_posts.validate_text(text)
        sources = image_posts.paths_list(paths)
        if sync_xiaohongshu:
            from screator.xiaohongshu import validate_metadata
            validate_metadata({"title": xiaohongshu_title, "description": text})
            if not isinstance(xiaohongshu_account_id, str) or not xiaohongshu_account_id or type(xiaohongshu_binding_revision) is not int:
                raise AppError("请选择已绑定的小红书账号并刷新登录状态。")
        if mode not in ("preview", "auto"):
            raise AppError("任务模式无效。")
        if not isinstance(account_id, str) or not account_id:
            raise AppError("请选择本次投稿的 Bilibili 账号。")
        def preflight():
            for path in sources:
                info = image_posts.image_info(path)
                if sync_xiaohongshu and info["extension"] not in (".jpg", ".png"):
                    raise AppError("同步小红书的图片仅支持 JPG、PNG，请替换 GIF 图片。")
            if sync_xiaohongshu:
                self.xiaohongshu.check(xiaohongshu_account_id, xiaohongshu_binding_revision)
            account = self.store.account(account_id)
            if mode == "auto" and account["auth_state"] != "valid":
                self.accounts.verify(account_id)
        def action():
            account = self.store.account(account_id)
            xhs_account = self.xiaohongshu.check(xiaohongshu_account_id, xiaohongshu_binding_revision) if sync_xiaohongshu else None
            identity = str(uuid.uuid4())
            title = text.strip().splitlines()[0][:80]
            task = Task("image-" + identity, "", "pending", task_id=identity, source_kind="image_post", asset_type="image",
                        account_id=account_id, account_uid_snapshot=account["uid"],
                        account_name_snapshot=account["nickname"] or account["uid"],
                        title_orig=title, title_zh=title, desc_orig=text, desc_zh=text,
                        source_image_paths=json.dumps([str(p.resolve()) for p in sources], ensure_ascii=False))
            self.store.upsert(task)
            publications.ensure_bili(self.store, task)
            if xhs_account:
                self.store._conn.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id,snapshot,text) VALUES(?,?,'xiaohongshu',?,?,?,?)",
                    (str(uuid.uuid4()), task.task_id, xhs_account["account_id"], task.video_id,
                     json.dumps({"user_id": xhs_account["user_id"], "nickname": xhs_account["nickname"],
                                 "title": xiaohongshu_title.strip(), "description": text}, ensure_ascii=False), xiaohongshu_title.strip()))
            self.scheduler.add(task, mode)
            return {"created": True, "task_id": identity, "account_id": account_id, "status": task.status}
        params = {"paths": [str(p.resolve()) for p in sources], "text": text, "account_id": account_id, "mode": mode}
        if sync_xiaohongshu:
            params.update(sync_xiaohongshu=True, xiaohongshu_account_id=xiaohongshu_account_id,
                          xiaohongshu_binding_revision=xiaohongshu_binding_revision, xiaohongshu_title=xiaohongshu_title)
        return self.operation(operation_id, "image_posts.create", action, params, preflight)

    def create_download(self, url, library_id, asset_type, operation_id):
        from screator.materials import validate_url, EXTENSIONS
        url = validate_url(url)
        self.materials.folder(library_id)
        if asset_type not in EXTENSIONS:
            raise AppError("请选择视频、图片或音频。")
        def action():
            identity = str(uuid.uuid4())
            task = Task("material-" + identity, url, "pending", task_id=identity,
                        source_kind="material", library_id=library_id, asset_type=asset_type,
                        title_orig=url, account_name_snapshot="素材下载")
            self.store.upsert(task)
            self.scheduler.add(task, "download")
            return {"created": True, "task_id": identity}
        return self.operation(operation_id, "materials.download", action,
                              {"url": url, "library_id": library_id, "asset_type": asset_type})

    def bind_legacy(self, task_id, account_id):
        with self.mutation, self.store.transaction():
            task = self.task(task_id)
            self.ensure_inactive(task.task_id)
            if task.account_id:
                raise AppError("已绑定账号的任务不能改投。")
            account = self.store.account(account_id, active=task.status != "submitted")
            if self.store.for_account(task.video_id, account_id):
                raise AppError("该账号已存在同视频任务，请保留并核对历史记录。")
            task.account_id, task.account_uid_snapshot = account_id, account["uid"]
            task.account_name_snapshot = account["nickname"] or account["uid"]
            self.store.upsert(task)
            self.store._conn.execute("UPDATE task_publications SET account_id=? WHERE task_id=? AND platform='bilibili' AND account_id IS NULL", (account_id, task.task_id))
            return asdict(task)

    def list_tasks(self, offset=0, limit=20, search="", status="", history=False, account_id="", platform=""):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 20:
            raise AppError("分页参数无效。")
        if platform and platform not in publications.PLATFORMS:
            raise AppError("投稿记录平台筛选参数无效。")
        page, total, counts, all_total = self.store.desktop_page(
            offset=offset, limit=limit, search=search, status=status, account_id=account_id,
            history_statuses=tuple(sorted(history_transfer.HISTORY_STATUSES)) if history else (),
            platform=platform)
        items = [history_transfer.view(item) if isinstance(item, dict) else
                 {**{key: value for key, value in asdict(item).items() if key not in ("desc_orig", "desc_zh")},
                  "publications": publications.items(self.store, item.task_id),
                  "run_id": (self.store.get_job(item.task_id) or {}).get("run_id")} for item in page]
        today = datetime.now().astimezone().date()
        start = datetime.combine(today, time.min).astimezone(timezone.utc).isoformat()
        end = datetime.combine(today + timedelta(days=1), time.min).astimezone(timezone.utc).isoformat()
        # Manual confirmations and older records have no successful upload attempt.
        with self.store._lock:
            submission_trend = None
            if history:
                first_day = today - timedelta(days=29)
                trend_start = datetime.combine(first_day, time.min).astimezone(timezone.utc).isoformat()
                trend_end = end
                dates = [(first_day + timedelta(days=index)).isoformat() for index in range(30)]
                counts_by_account = {}
                for row in self.store._conn.execute("""
                    SELECT p.account_id, COALESCE(receipt.submitted_at, t.updated_at) AS submitted_at
                    FROM task_publications AS p
                    JOIN tasks AS t ON t.task_id = p.task_id
                    LEFT JOIN (
                        SELECT task_id, MAX(ended_at) AS submitted_at
                        FROM upload_attempts WHERE outcome = 'submitted'
                        GROUP BY task_id
                    ) AS receipt ON receipt.task_id = p.task_id
                    WHERE p.platform = 'bilibili' AND p.status = 'submitted'
                      AND p.account_id IS NOT NULL
                      AND COALESCE(receipt.submitted_at, t.updated_at) >= ?
                      AND COALESCE(receipt.submitted_at, t.updated_at) < ?
                """, (trend_start, trend_end)):
                    day = datetime.fromisoformat(row["submitted_at"]).astimezone().date()
                    index = (day - first_day).days
                    if 0 <= index < 30:
                        counts_by_account.setdefault(row["account_id"], [0] * 30)[index] += 1
                submission_trend = {"dates": dates, "by_account": counts_by_account}
                today_submitted = {account_id: values[-1] for account_id, values in counts_by_account.items() if values[-1]}
            else:
                today_submitted = {
                    row["account_id"]: row["count"] for row in self.store._conn.execute("""
                        SELECT p.account_id, COUNT(*) AS count
                        FROM task_publications AS p
                        JOIN tasks AS t ON t.task_id = p.task_id
                        LEFT JOIN (
                            SELECT task_id, MAX(ended_at) AS submitted_at
                            FROM upload_attempts WHERE outcome = 'submitted'
                            GROUP BY task_id
                        ) AS receipt ON receipt.task_id = p.task_id
                        WHERE p.platform = 'bilibili' AND p.status = 'submitted'
                          AND p.account_id IS NOT NULL
                          AND COALESCE(receipt.submitted_at, t.updated_at) >= ?
                          AND COALESCE(receipt.submitted_at, t.updated_at) < ?
                        GROUP BY p.account_id
                    """, (start, end))
                }
        return {"items": items, "total": total,
                "counts": {name: counts.get(name, 0) for name in ("downloading", "validating", "uploading", "ready", "submitted", "failed")},
                "today_submitted_by_account": today_submitted,
                "submission_trend": submission_trend,
                "queue": self.scheduler.snapshot(), "all_total": all_total}

    def get_task(self, task_id):
        imported = history_transfer.get_imported(self.store, task_id)
        if imported:
            return imported
        task = self.task(task_id)
        result = asdict(task)
        result["publications"] = publications.items(self.store, task_id)
        result["translation"] = self.store.translation(task_id)
        result["snapshot"] = self.store.get_job(task_id)
        result["run_id"] = (result["snapshot"] or {}).get("run_id")
        result["file_exists"] = bool(task.video_path and Path(task.video_path).is_file())
        return result

    def export_history(self, path):
        return history_transfer.export_file(self.store, path)

    def import_history(self, path):
        with self.mutation:
            result = history_transfer.import_file(self.store, path)
        if result["imported"]:
            self.emit("history.changed", {"imported": result["imported"]})
        return result

    def delete_history(self, task_id, expected_revision, confirmed=False):
        if confirmed is not True:
            raise AppError("请先在任务详情中确认删除投稿记录。")
        with self.mutation, self.scheduler.guard:
            if self.scheduler.closing:
                raise AppError("应用正在退出，不能删除投稿记录。")
            if task_id in self.scheduler.active:
                raise AppError("任务仍在执行，不能删除投稿记录。")
            result = history_transfer.delete_record(self.store, task_id, expected_revision)
        self.emit("history.changed", {"deleted_task_id": task_id})
        return result

    def delete_all_history(self, confirmed=False):
        if confirmed is not True:
            raise AppError("请先在弹窗中确认删除全部投稿记录。")
        with self.mutation, self.scheduler.guard:
            if self.scheduler.closing:
                raise AppError("应用正在退出，不能删除投稿记录。")
            with self.store._lock:
                active_history = {task.task_id for task in self.store.list_all()
                                  if task.status in history_transfer.HISTORY_STATUSES}
            if active_history.intersection(self.scheduler.active):
                raise AppError("仍有投稿记录正在执行，不能删除全部记录。")
            result = history_transfer.delete_all_records(self.store)
        if result["deleted"]:
            self.emit("history.changed", {"deleted": result["deleted"]})
        return result

    def retry(self, task_id, operation_id, use_current_translation_settings=False, mode="preview"):
        if mode not in ("preview", "auto"):
            raise AppError("任务模式无效。")
        def action():
            task = self.task(task_id)
            pubs = publications.items(self.store, task_id)
            if any(p["status"] in publications.TERMINAL | {"submission_unknown"} for p in pubs):
                raise AppError("请在平台投稿记录中单独继续未完成目标。")
            if task.status not in ("failed", "cancelled", "interrupted"):
                raise AppError("只有失败、取消或中断的任务可以继续；待核对投稿需先核对结果。")
            snapshot = self.store.get_job(task_id) or {}
            settings = snapshot.get("settings")
            if mode == "auto" and task.source_kind != "material":
                account = self.store.account(task.account_id)
                if account["auth_state"] != "valid":
                    self.accounts.verify(task.account_id)
                services = {"douyin": self.douyin, "acfun": self.acfun,
                            "youtube": self.youtube_upload, "xiaohongshu": self.xiaohongshu, **self.social}
                for p in pubs:
                    if p["platform"] in services:
                        services[p["platform"]].check(p["account_id"], auto=True)
                    if p["platform"] == "acfun":
                        self.acfun.validate_channel(json.loads(p["snapshot"]).get("channel_id"))
            if use_current_translation_settings is True:
                from screator.translation.config import DEFAULTS
                settings = {**(settings or self.config.snapshot()), **{k: self.config.values[k] for k in DEFAULTS}}
                record = self.store.translation(task_id) or {}
                record["config_snapshot"] = {k: self.config.values[k] for k in DEFAULTS}
                self.store.save_translation(task, record)
            self.scheduler.add(task, "download" if task.source_kind == "material" else mode, settings)
            return {"queued": True}
        return self.operation(operation_id, "tasks.retry", action, {"task_id": task_id, "use_current_translation_settings": use_current_translation_settings, "mode": mode})

    def submit(self, task_id, operation_id, targets=None, revisions=None):
        if targets is not None and (not isinstance(targets, list) or not targets or len(targets) > len(publications.PLATFORMS) or any(not isinstance(v, str) for v in targets) or len(set(targets)) != len(targets)):
            raise AppError("投稿目标列表无效。")
        if revisions is not None and not isinstance(revisions, dict): raise AppError("投稿版本快照无效。")
        def action():
            task = self.task(task_id)
            self.ensure_inactive(task_id)
            if task.status not in ("ready", "partial_success"):
                raise AppError("只有已准备好素材的任务可以确认投稿。")
            saved = self.store.get_job(task_id) or {}
            pubs = publications.items(self.store, task_id)
            if targets is not None and set(targets) - {p["publication_id"] for p in pubs}: raise AppError("投稿目标不属于此任务。")
            selected = [p for p in pubs if targets is None and p["status"] == "ready" or targets is not None and p["publication_id"] in targets]
            self.store.account(task.account_id, active=any(p["platform"] == "bilibili" for p in selected))
            if not selected or any(p["status"] != "ready" for p in selected): raise AppError("投稿目标尚未准备好。")
            if revisions is not None and any(revisions.get(p["publication_id"]) != p["revision"] for p in selected): raise AppError("文案已变化，请刷新预览后确认。")
            for p in selected:
                if p["platform"] == "xiaohongshu":
                    self.xiaohongshu.check(p["account_id"])
                    self.xiaohongshu.validate_assets(type("TaskRef", (), {"task_id": task_id})(), json.loads(p["snapshot"]))
                if p["platform"] in self.social:
                    self.social[p["platform"]].check(p["account_id"])
                    self.social[p["platform"]].validate_assets(type("TaskRef", (), {"task_id": task_id})(), json.loads(p["snapshot"]))
                if p["platform"] == "youtube":
                    self.youtube_upload.check(p["account_id"])
                    self.youtube_upload.validate_assets(type("TaskRef", (), {"task_id": task_id})(), json.loads(p["snapshot"]))
                if p["platform"] == "acfun":
                    self.acfun.check(p["account_id"])
                    self.acfun.validate_assets(type("TaskRef", (), {"task_id": task_id})(), json.loads(p["snapshot"]))
                    self.acfun.validate_channel(json.loads(p["snapshot"])["channel_id"])
            publications.freeze(self.store, task_id, [p["publication_id"] for p in selected])
            self.scheduler.add(task, "submit", saved.get("settings"), stage="validate", targets=[p["publication_id"] for p in selected])
            return {"queued": True}
        params = {"task_id": task_id}
        if targets is not None: params.update(targets=targets, revisions=revisions)
        return self.operation(operation_id, "tasks.submit", action, params)

    def configure_douyin(self, url, pairing_key):
        with self.mutation:
            self.ensure_idle()
            return self.douyin.configure(url, pairing_key)

    def configure_youtube(self, path):
        with self.mutation:
            self.ensure_idle()
            return self.youtube_upload.configure(path)

    def social_service(self, platform):
        if platform not in self.social: raise AppError("同步平台无效。")
        return self.social[platform]

    def social_status(self, platform, verify=False): return self.social_service(platform).status(verify)

    def bind_social(self, platform, access_token, page_id="", api_version="v25.0"):
        with self.mutation:
            return self.social_service(platform).bind(access_token, page_id, api_version)

    def clear_social(self, platform):
        with self.mutation: return self.social_service(platform).clear()

    def archive_social(self, platform):
        with self.mutation: return self.social_service(platform).clear(archive=True)

    def resume_social(self, platform): return self.social_service(platform).resume()

    def social_creator(self, platform="tiktok", account_id=None): return self.social_service(platform).creator(account_id)

    def check_social_publication(self, publication_id):
        with self.mutation:
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            return self.social_service(p["platform"]).check_publication(publication_id)

    def preview_social_video(self, publication_id):
        p = publications.get(self.store, publication_id)
        self.social_service(p["platform"])
        task = self.store.require(p["task_id"])
        from screator.task_paths import validate_task_paths
        validate_task_paths(task)
        if not task.video_path or not Path(task.video_path).is_file(): raise AppError("投稿视频已不存在。")
        if sys.platform == "win32": os.startfile(task.video_path)
        else: subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", task.video_path])
        return {"opened": True}

    def update_publication(self, publication_id, text=None, revision=None, title=None, description=None, channel_id=None, tags=None,
                           category_id=None, privacy_status=None, made_for_kids=None, social_metadata=None):
        with self.mutation, self.store.transaction():
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            if p["status"] != "ready" or p["revision"] != revision:
                raise AppError("只能编辑预览中的投稿目标，请刷新后重试。")
            if p["platform"] == "douyin":
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000: raise AppError("抖音文案须为 1～1000 字。")
                publications.change(self.store, publication_id, text=text.strip())
            elif p["platform"] == "xiaohongshu":
                from screator.xiaohongshu import validate_metadata
                task = self.store.require(p["task_id"])
                snapshot = json.loads(p["snapshot"])
                snapshot.update(title=title.strip() if isinstance(title, str) else title, description=task.desc_zh)
                validate_metadata(snapshot)
                publications.change(self.store, publication_id, text=snapshot["title"], snapshot=json.dumps(snapshot, ensure_ascii=False))
            elif p["platform"] == "acfun":
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 50: raise AppError("AcFun 标题须为 1～50 字。")
                # Allow an incomplete draft so a corrected title can be saved before
                # the user has chosen a channel. Submission still requires channel_id > 0.
                if type(channel_id) is not int or channel_id < 0: raise AppError("AcFun 分区 ID 无效。")
                if channel_id: self.acfun.validate_channel(channel_id)
                from screator.acfun import tags_from_bilibili
                task = self.store.require(p["task_id"])
                saved = self.store.get_job(p["task_id"]) or {}
                shared_tags = tags_from_bilibili(saved.get("settings", {}).get("bili_tags", self.config.values["bili_tags"]))
                snapshot = json.loads(p["snapshot"])
                snapshot.update(title=title.strip(), title_source="manual", description=task.desc_zh,
                                channel_id=channel_id, tags=shared_tags)
                publications.change(self.store, publication_id, snapshot=json.dumps(snapshot, ensure_ascii=False))
            elif p["platform"] == "youtube":
                from screator.youtube_upload import validate_metadata, tags_from_bilibili
                task = self.store.require(p["task_id"])
                saved = self.store.get_job(p["task_id"]) or {}
                snapshot = json.loads(p["snapshot"])
                snapshot.update(title=title.strip() if isinstance(title, str) else title, title_source="manual",
                    description=task.desc_zh, category_id=category_id, privacy_status=privacy_status, made_for_kids=made_for_kids,
                    tags=tags_from_bilibili(saved.get("settings", {}).get("bili_tags", self.config.values["bili_tags"])))
                validate_metadata(snapshot)
                publications.change(self.store, publication_id, text=snapshot["title"], snapshot=json.dumps(snapshot, ensure_ascii=False))
            elif p["platform"] in self.social:
                service = self.social[p["platform"]]
                if not isinstance(social_metadata, dict) or set(social_metadata) - {"title", "privacy_level", "allow_comment", "allow_duet", "allow_stitch", "content_disclosure", "brand_organic", "brand_content", "is_aigc", "consent", "original_content", "cover_timestamp_ms"}:
                    raise AppError("平台投稿信息无效。")
                snapshot = json.loads(p["snapshot"])
                snapshot.update(social_metadata)
                snapshot.update(title_source="manual", description=self.store.require(p["task_id"]).desc_zh)
                service.validate_metadata(snapshot)
                publications.change(self.store, publication_id, text=snapshot["title"], snapshot=json.dumps(snapshot, ensure_ascii=False))
            else: raise AppError("此平台不支持编辑投稿目标。")
            return publications.get(self.store, publication_id)

    def complete_acfun_verification(self, publication_id, challenge_id, verification_type, token):
        with self.mutation, self.scheduler.guard:
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            return self.acfun.complete_verification(publication_id, challenge_id, verification_type, token)

    def refresh_acfun_verification(self, publication_id, operation_id):
        """Explicit user retry of one rejected target; never upload other platforms."""
        def action():
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            if p["platform"] != "acfun" or p["status"] != "failed":
                raise AppError("只有被 AcFun 拒绝、等待安全验证的目标可以重新获取入口。")
            attempt = self.store._conn.execute("SELECT phase,error FROM acfun_attempts WHERE publication_id=? ORDER BY started_at DESC LIMIT 1", (publication_id,)).fetchone()
            if not attempt or attempt["phase"] != "rejected" or not re.search(r"安全验证|(?:40\d{4}|410\d{3})", attempt["error"]):
                raise AppError("未找到明确的安全验证拒绝记录，不能自动重发。")
            if self.store._conn.execute("SELECT 1 FROM acfun_attempts WHERE publication_id=? AND create_intent_at!='' AND phase NOT IN ('rejected','resolved_not_submitted')", (publication_id,)).fetchone():
                raise AppError("此目标存在待核对的投稿，请先核对，禁止重发。")
            task = self.task(p["task_id"])
            self.acfun.check(p["account_id"])
            snapshot = json.loads(p["snapshot"])
            self.acfun.validate_assets(task, snapshot)
            self.acfun.validate_channel(snapshot["channel_id"])
            saved = self.store.get_job(task.task_id) or {}
            publications.change(self.store, publication_id, status="ready", error="")
            publications.freeze(self.store, task.task_id, [publication_id])
            self.scheduler.add(task, "submit", saved.get("settings"), stage="validate", targets=[publication_id])
            return {"queued": True}
        return self.operation(operation_id, "acfun.verification.refresh", action, {"publication_id": publication_id})

    def retry_publication(self, publication_id, operation_id):
        def action():
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            if p["status"] not in ("failed", "cancelled", "interrupted", "blocked_validation"):
                raise AppError("只能继续已失败、取消或中断的目标；结果待核对时禁止重投。")
            if p["platform"] == "acfun" and self.store._conn.execute("SELECT 1 FROM acfun_attempts WHERE publication_id=? AND create_intent_at!='' AND phase NOT IN ('resolved_not_submitted','rejected')", (publication_id,)).fetchone():
                raise AppError("AcFun 已记录创建作品意图，须先核对；禁止重投。")
            if p["platform"] == "youtube" and self.store._conn.execute("SELECT 1 FROM youtube_attempts WHERE publication_id=? AND phase IN ('finalizing','unknown','submitted')", (publication_id,)).fetchone():
                raise AppError("YouTube 已记录最终上传意图，须先核对；禁止重投。")
            if p["platform"] in self.social:
                from screator.social_upload import PROTECTED_PHASES
                attempt = self.social[p["platform"]].attempt(publication_id)
                if attempt and attempt["phase"] in PROTECTED_PHASES: raise AppError("此平台已记录发布意图，须先核对，禁止重投。")
            task = self.task(p["task_id"])
            if p["platform"] == "xiaohongshu" and self.store._conn.execute("SELECT 1 FROM xiaohongshu_attempts WHERE publication_id=? AND phase IN ('creating','unknown','submitted')", (publication_id,)).fetchone():
                raise AppError("小红书已记录发布意图，须先核对；禁止重发。")
            if task.source_kind == "image_post":
                from screator.image_posts import prepared_paths
                prepared_paths(task)
            elif not task.video_path or not Path(task.video_path).is_file(): raise AppError("本地素材缺失，请先恢复素材；不会自动重发已成功目标。")
            publications.change(self.store, publication_id, status="ready", error="")
            publications.project(self.store, task.task_id)
            return {"ready": True}
        return self.operation(operation_id, "publications.retry", action, {"publication_id": publication_id})

    def cancel_publication(self, publication_id):
        with self.mutation, self.scheduler.guard:
            p = publications.get(self.store, publication_id)
            if p["status"] in publications.INFLIGHT | publications.TERMINAL | {"submission_unknown"}:
                raise AppError("此目标不能取消，请等待或核对结果。")
            root = self.scheduler.active.get(p["task_id"])
            if root:
                child = root.children.get(p["platform"])
                if not child: raise AppError("共享素材准备阶段请取消整个任务。")
                child.cancel.set()
            publications.change(self.store, publication_id, status="cancelled", error="用户取消，素材保留。")
            publications.project(self.store, p["task_id"])
            return {"cancelled": True}

    def abandon_publication(self, publication_id):
        with self.mutation, self.store.transaction():
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            if p["status"] in publications.INFLIGHT | {"submitted", "submission_unknown"}: raise AppError("请先核对投稿结果；已提交目标不能放弃。")
            publications.change(self.store, publication_id, status="abandoned", error="用户明确放弃该目标。")
            publications.project(self.store, p["task_id"])
        with work_lock(Path(self.task(p["task_id"]).work_dir)):
            self.scheduler.uploader.cleanup(p["task_id"])
        return {"abandoned": True}

    def resolve_publication(self, publication_id, remote_id="", not_submitted=False):
        if type(not_submitted) is not bool: raise AppError("核对结果必须为明确的开关值。")
        with self.mutation, self.store.transaction():
            p = publications.get(self.store, publication_id)
            self.ensure_inactive(p["task_id"])
            if p["status"] != "submission_unknown": raise AppError("此目标无需核对。")
            if not_submitted is not True and (not isinstance(remote_id, str) or not remote_id.strip() or len(remote_id) > 512): raise AppError("请填写已核对的作品 ID。")
            if p["platform"] == "bilibili" and not not_submitted:
                is_image = self.task(p["task_id"]).source_kind == "image_post"
                if not re.fullmatch(r"[1-9][0-9]{0,29}" if is_image else r"BV[0-9A-Za-z]{10}", remote_id):
                    raise AppError("动态 ID 须为有效数字。" if is_image else "BV 号格式无效。")
            if p["platform"] == "acfun" and not not_submitted and not re.fullmatch(r"(?i:AC)[1-9][0-9]*", remote_id): raise AppError("AC 号格式无效。")
            if p["platform"] == "youtube":
                from screator.youtube_upload import VIDEO_ID
                if not not_submitted and not VIDEO_ID.fullmatch(remote_id): raise AppError("YouTube 视频 ID 须为 11 位字母、数字、下划线或连字符。")
                self.youtube_upload.resolve(publication_id, remote_id if not not_submitted else "", not_submitted)
            if p["platform"] in self.social:
                self.social[p["platform"]].resolve(publication_id, remote_id if not not_submitted else "", not_submitted)
            if p["platform"] == "douyin":
                self.douyin.request("POST", "/v1/publications/" + publication_id + "/resolve", {"not_submitted": not_submitted, "remote_id": remote_id})
            if p["platform"] == "xiaohongshu":
                from screator.xiaohongshu import NOTE_ID
                if not not_submitted and not NOTE_ID.fullmatch(remote_id): raise AppError("小红书笔记 ID 须为 24 位小写十六进制字符。")
                self.xiaohongshu.resolve(publication_id, "" if not_submitted else remote_id, not_submitted)
            if p["platform"] == "acfun":
                self.store._conn.execute("UPDATE acfun_attempts SET phase=?,douga_id=? WHERE publication_id=? AND create_intent_at!=''",
                    ("resolved_not_submitted" if not_submitted else "manually_submitted", "" if not_submitted else remote_id.removeprefix("AC"), publication_id))
            publications.change(self.store, publication_id, status="interrupted" if not_submitted else "submitted", remote_id="" if not_submitted else remote_id, retain_assets=1, error="用户核对结果；保留本地素材。")
            if p["platform"] == "bilibili" and not not_submitted: self.store.update(p["task_id"], bv_id=remote_id)
            publications.project(self.store, p["task_id"])
            return self.get_task(p["task_id"])

    def cancel(self, task_id):
        self.task(task_id)
        self.scheduler.cancel(task_id)
        return {"requested": True, "status": self.task(task_id).status}

    def repair(self, task_id, operation_id):
        def action():
            task = self.task(task_id)
            if task.source_kind == "image_post":
                raise AppError("图文动态不支持视频修复。")
            if task.status != "submitted" or not task.bv_id:
                raise AppError("修复用于已取得 BV 号的稿件；只准备本地替换文件。")
            saved = self.store.get_job(task_id) or {}
            self.scheduler.add(task, "preview", saved.get("settings"), repair=True)
            return {"queued": True}
        return self.operation(operation_id, "tasks.repair", action, {"task_id": task_id})

    def update_metadata(self, task_id, title, description):
        with self.mutation:
            task = self.task(task_id)
            self.ensure_inactive(task_id)
            if task.status != "ready":
                raise AppError("素材准备完成后才可编辑。")
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
                raise AppError("标题须为 1～80 字。")
            ac = publications.for_platform(self.store, task.task_id, "acfun")
            xhs = publications.for_platform(self.store, task.task_id, "xiaohongshu")
            desc_limit = 1000 if ac and ac["status"] == "ready" or xhs and xhs["status"] == "ready" else 2000
            if not isinstance(description, str) or len(description) > desc_limit:
                raise AppError(f"简介不能超过 {desc_limit} 字。")
            task.metadata_revision += 1
            task.title_zh = title.strip().replace("\n", " ")
            body = re.split(r"\n\n————————\n(?:原标题|原作者)：", description, maxsplit=1)[0]
            if task.source_kind == "image_post":
                from screator.image_posts import validate_text
                validate_text(description)
                if xhs and xhs["status"] == "ready":
                    from screator.xiaohongshu import validate_metadata
                    validate_metadata({**json.loads(xhs["snapshot"]), "description": description})
            task.desc_zh = description if task.source_kind in ("local", "image_post") else translate.build_description(body, task.uploader, desc_limit)
            record = self.store.translation(task_id) or {}
            record.update(state="edited", user_edited=True, description=body)
            if task.source_kind in ("local", "image_post"):
                self.store.upsert(task)
            else:
                self.store.save_translation(task, record)
            p = publications.for_platform(self.store, task.task_id, "bilibili")
            if p: publications.change(self.store, p["publication_id"], text=task.title_zh)
            if xhs and xhs["status"] == "ready":
                snapshot = json.loads(xhs["snapshot"])
                snapshot["description"] = task.desc_zh
                publications.change(self.store, xhs["publication_id"], snapshot=json.dumps(snapshot, ensure_ascii=False))
            if ac and ac["status"] == "ready":
                ac_snapshot = json.loads(ac["snapshot"])
                ac_snapshot["description"] = task.desc_zh
                publications.change(self.store, ac["publication_id"], snapshot=json.dumps(ac_snapshot, ensure_ascii=False))
            yt = publications.for_platform(self.store, task.task_id, "youtube")
            if yt and yt["status"] == "ready":
                snapshot = json.loads(yt["snapshot"])
                snapshot["description"] = task.desc_zh
                if snapshot.get("title_source") == "generated": snapshot["title"] = task.title_zh
                publications.change(self.store, yt["publication_id"], snapshot=json.dumps(snapshot, ensure_ascii=False))
            for platform in self.social:
                pub = publications.for_platform(self.store, task.task_id, platform)
                if pub and pub["status"] == "ready":
                    snapshot = json.loads(pub["snapshot"])
                    snapshot["description"] = task.desc_zh
                    if snapshot.get("title_source") == "generated": snapshot["title"] = task.title_zh
                    if platform == "tiktok": snapshot["consent"] = False
                    publications.change(self.store, pub["publication_id"], text=snapshot["title"], snapshot=json.dumps(snapshot, ensure_ascii=False))
            for name, content in (("title.txt", task.title_zh), ("desc.txt", task.desc_zh)):
                (Path(task.work_dir) / name).write_text(content, encoding="utf-8")
            return asdict(task)

    def resolve(self, task_id, bv_id="", not_submitted=False):
        with self.mutation:
            task = self.task(task_id)
            if publications.dual(self.store, task_id): raise AppError("请在对应平台记录中分别核对结果。")
            self.ensure_inactive(task_id)
            if task.status != "submission_unknown":
                raise AppError("此任务无需核对投稿结果。")
            if not_submitted is True:
                task.status = "interrupted"
                task.error = "用户已核对未提交，可手动继续准备素材。"
            else:
                if task.source_kind == "image_post":
                    if not isinstance(bv_id, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", bv_id):
                        raise AppError("请填写核对后的数字动态 ID。")
                elif not re.fullmatch(r"BV[0-9A-Za-z]{10}", bv_id):
                    raise AppError("请填写核对后的完整 BV 号。")
                task.status, task.bv_id, task.error = "submitted", bv_id, "用户登记 BV 号；本地素材保留。"
            self.store.upsert(task)
            p = publications.for_platform(self.store, task.task_id, "bilibili")
            if p: publications.change(self.store, p["publication_id"], status=task.status, remote_id=task.bv_id, retain_assets=1)
            return asdict(task)

    def cover(self, task_id):
        from PIL import Image
        task = self.task(task_id)
        if not task.cover_path or not task.work_dir:
            return {"image": None}
        file = Path(task.cover_path).resolve()
        if Path(task.work_dir).resolve() not in file.parents or not file.is_file():
            return {"image": None}
        with Image.open(file) as image:
            image.thumbnail((640, 360))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=80)
        return {"image": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()}

    def open_folder(self, task_id):
        task = self.task(task_id)
        path = Path(task.work_dir) if task.work_dir else None
        if not path or not path.is_dir():
            raise AppError("素材目录已清理或不存在。")
        if os.name == "nt":
            os.startfile(str(path))
        else:
            import sys
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        return {"opened": True}

    def auth_status(self, verify=False, account_id=None):
        if verify and account_id:
            self.accounts.verify(account_id)
        items = self.accounts.list()
        return {"accounts": items, "limit": 5, "configured": bool(items),
                "login": self.auth_state}

    def login_start(self, account_id=None):
        if self.scheduler.closing:
            raise AppError("应用正在退出。")
        if account_id:
            self.store.account(account_id)
        elif len(self.accounts.list()) >= 5:
            raise AppError("最多支持 5 个 Bilibili 账号。")
        self.login_cancel()
        self.auth_state = {"status": "loading", "account_id": account_id}
        self.login = LoginSession(None, self.emit, account_id=account_id,
                                  commit=lambda info: self.accounts.bind(info, account_id))
        return self.login.start()

    def login_cancel(self, session_id=None, account_id=None):
        if self.login and (not session_id or self.login.session_id == session_id):
            self.login.cancel()
            self.auth_state = {"status": "idle"}
        return {"cancelled": True}

    def renew(self, account_id):
        account = self.store.account(account_id)
        with account_guard(account["uid"]):
            account, info = self.accounts.read(account_id)
            temporary = self.paths.root / "secrets/bilibili" / account_id / ("renew-" + str(uuid.uuid4()) + ".json")
            try:
                atomic_json(temporary, info)
                bili_upload.renew(replace(self.config.build(), bili_cookies=temporary))
                self.accounts.bind(json.loads(temporary.read_text(encoding="utf-8")), account_id, locked=True)
            finally:
                temporary.unlink(missing_ok=True)
        return self.auth_status()

    def import_cookies(self, path, kind, account_id=None):
        source = Path(path)
        if not source.is_file() or source.stat().st_size > 5_000_000:
            raise AppError("Cookie 文件不存在或过大。")
        content = source.read_text(encoding="utf-8-sig")
        if kind == "bilibili":
            result = self.accounts.bind(validate_login(json.loads(content)), account_id)
            return {"imported": True, "account_id": result["account_id"]}
        if kind != "youtube":
            raise AppError("不支持的 Cookie 类型。")
        with self.mutation:
            self.ensure_idle()
            import http.cookiejar
            jar = http.cookiejar.MozillaCookieJar(str(source))
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception as exc:
                raise AppError("请使用 Netscape 格式的 YouTube Cookie 文件。") from exc
            target = self.paths.root / "secrets/youtube_cookies.txt"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            if os.name != "nt":
                target.chmod(0o600)
            return {"imported": True}

    def archive_account(self, account_id):
        with self.mutation:
            return self.accounts.archive(account_id)

    def resume_uploads(self, account_id, confirmed_no_upload=False):
        account = self.store.account(account_id)
        if confirmed_no_upload is not True:
            raise AppError("请先核对没有仍在运行的投稿进程及该账号创作中心。")
        with account_guard(account["uid"]):
            marker = coordination_dir(account["uid"]) / "attempt-state.json"
            from screator.process_manager import process_alive
            try:
                state = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
            except (OSError, ValueError):
                state = {}
            if state.get("inflight") and (process_alive(state.get("child_pid")) or process_alive(state.get("owner_pid"))):
                raise AppError("检测到原投稿进程仍可能运行，不能恢复；请先结束原实例并核对。")
            # Explicit user confirmation only; unknown tasks remain frozen.
            import time
            atomic_json(marker, {"ended": time.time()})
        self.scheduler.sync_accounts(account_id)
        return {"resumed": True}

    def prepare_shutdown(self):
        self.login_cancel()
        with self.translation_jobs.lock:
            self.translation_jobs.closing = True
            for cancel, _ in self.translation_jobs.running.values():
                cancel.set()
        self.scheduler.prepare_shutdown()
        result = self.shutdown_status()
        self.output("system.shutdown_status", result)
        return result

    def shutdown_status(self):
        if self._closed:
            return {"closing": True, "ready": True, "pending_task_ids": [], "inflight_task_ids": [],
                    "stages": [], "translation_active": False}
        result = self.scheduler.shutdown_status()
        result["ready"] = result["ready"] and not self.translation_jobs.active()
        result["translation_active"] = self.translation_jobs.active()
        return result

    def export_youtube(self, browser="edge"):
        with self.mutation:
            self.ensure_idle()
            if browser not in ("edge", "chrome", "firefox"):
                raise AppError("不支持的浏览器。")
            from dataclasses import replace
            settings = replace(self.config.build(), youtube_cookies=self.paths.root / "secrets/youtube_cookies.txt")
            youtube.export_browser_cookies(settings, browser)
            return {"exported": True}

    def clear_auth(self, kind, account_id=None):
        if kind == "bilibili":
            return self.accounts.clear(account_id)
        if kind != "youtube":
            raise AppError("账号类型无效。")
        with self.mutation:
            self.ensure_idle()
            (self.paths.root / "secrets/youtube_cookies.txt").unlink(missing_ok=True)
        return {"cleared": True}

    def import_data(self, path):
        with self.mutation:
            self.ensure_idle()
            source = Path(path).resolve()
            db = source / "data/tasks.sqlite"
            if not db.is_file() or db.resolve() == self.store.path.resolve():
                raise AppError("请选择另一个有效项目的数据目录。")
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as old:
                version = old.execute("PRAGMA user_version").fetchone()[0]
                from screator.db import SCHEMA_VERSION
                if version > SCHEMA_VERSION:
                    raise AppError("不支持的来源数据库版本。")
                old.row_factory = sqlite3.Row
                rows = [dict(r) for r in old.execute("SELECT * FROM tasks")]
                tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "task_publications" in tables and old.execute("SELECT 1 FROM task_publications WHERE platform!='bilibili'").fetchone():
                    raise AppError("包含抖音或 AcFun 投稿的数据须整体备份/恢复，不能以旧版导入方式重建投稿身份；源数据未修改。")
                records = {r[0]: json.loads(r[1]) for r in old.execute("SELECT * FROM task_translation")} if "task_translation" in tables else {}
                attempts = [dict(r) for r in old.execute("SELECT * FROM translation_attempts ORDER BY id")] if "translation_attempts" in tables else []
            source_id = hashlib.sha256(str(db).encode()).hexdigest()
            imported, conflicts = 0, 0
            with self.store.transaction() as conn:
                for row in rows:
                    if not VIDEO_ID.fullmatch(row["video_id"]):
                        continue
                    legacy_id = row.get("task_id") or row["video_id"]
                    if conn.execute("SELECT 1 FROM legacy_task_map WHERE source_id=? AND legacy_id=?", (source_id, legacy_id)).fetchone():
                        continue
                    task = Task(**{k: v for k, v in row.items() if k in Task.__dataclass_fields__})
                    task.task_id = str(uuid.uuid4())
                    target = next((a for a in self.store.accounts(True) if version >= 2 and a["uid"] == row.get("account_uid_snapshot")), None)
                    task.account_id = target["account_id"] if target else None
                    task.account_uid_snapshot = target["uid"] if target else None
                    task.account_name_snapshot = (target["nickname"] or target["uid"]) if target else ""
                    existing = self.store.for_account(task.video_id, task.account_id) if target else None
                    if existing:
                        conn.execute("INSERT OR IGNORE INTO import_conflicts VALUES(?,?,?,?)",
                                     (source_id, legacy_id, json.dumps(row, ensure_ascii=False), existing.task_id))
                        conflicts += 1
                        continue
                    task.cancel_requested = 0
                    if task.work_dir:
                        candidate = source / "work" / (legacy_id if row.get("task_id") else task.video_id)
                        expected = candidate.resolve()
                        if Path(task.work_dir).resolve() != expected or candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()) or not expected.is_dir():
                            task.work_dir = task.work_root = task.video_path = task.cover_path = ""
                        else:
                            destination = Path(self.config.values["work_dir"]) / task.task_id
                            with work_lock(expected):
                                unsafe = any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in expected.rglob("*"))
                                if unsafe:
                                    raise AppError("来源素材含目录跳转，请先整理素材后导入。")
                                shutil.copytree(expected, destination)
                            task.work_root, task.work_dir = str(destination.parent), str(destination)
                            for attr in ("video_path", "cover_path"):
                                if getattr(task, attr) and expected not in Path(getattr(task, attr)).resolve().parents:
                                    setattr(task, attr, "")
                                elif getattr(task, attr):
                                    setattr(task, attr, str(destination / Path(getattr(task, attr)).resolve().relative_to(expected)))
                    if task.status == "ready" and not task.work_dir:
                        task.status = "interrupted"
                    if task.status == "uploading" or task.status == "submitted" and not task.bv_id:
                        task.status = "submission_unknown"
                    elif task.status not in ("submitted", "ready", "failed", "cancelled", "submission_unknown"):
                        task.status = "interrupted"
                    self.store.upsert(task)
                    publications.ensure_bili(self.store, task)
                    from screator.translation.config import legacy_snapshot
                    record = records.get(legacy_id) or {"config_snapshot": legacy_snapshot({})}
                    if task.title_zh and not record.get("state"):
                        record.update(state="legacy_preserved", provider="unknown", user_edited=False)
                    self.store.save_translation(task, record)
                    for attempt in attempts:
                        if (attempt.get("task_id") or attempt.get("video_id")) == legacy_id:
                            conn.execute("INSERT INTO translation_attempts(task_id,payload) VALUES(?,?)", (task.task_id, attempt["payload"]))
                    conn.execute("INSERT INTO legacy_task_map VALUES(?,?,?)", (source_id, legacy_id, task.task_id))
                    imported += 1
            return {"imported": imported, "conflicts": conflicts, "note": "素材复制到独立任务目录，源文件保留；未知归属请人工确认，未导入凭据。"}

    def log_tail(self, task_id=None, account_id=None):
        with self.logs_lock:
            return {"items": [entry for entry in self.logs
                             if (not task_id or entry.get("task_id") == task_id)
                             and (not account_id or entry.get("account_id") == account_id)][-300:]}

    def log_export(self, path):
        target = Path(path)
        if target.suffix.lower() not in (".txt", ".log"):
            raise AppError("日志请保存为 .txt 或 .log。")
        from screator.desktop_worker import redact
        parts = []
        for index in (3, 2, 1, 0):
            file = self.paths.root / "logs" / (f"desktop.log.{index}" if index else "desktop.log")
            if file.is_file():
                parts.append("\n".join(redact(line) for line in file.read_text(encoding="utf-8", errors="replace").splitlines()))
        if parts:
            text = "\n".join(parts)
        else:
            with self.logs_lock:
                text = "\n".join(f"{item['time']} {item['level']} {redact(item['message'])}" for item in self.logs)
        target.write_text(text, encoding="utf-8")
        return {"exported": True}

    def close(self):
        if self._closed:
            return
        self.login_cancel()
        self.youtube_upload.close()
        self.translation_jobs.close()
        self.scheduler.close()
        self.xiaohongshu.close()
        self.store.close()
        self.owner_lock.__exit__(None, None, None)
        self._closed = True
