"""Image and video synchronization through the user-operated creator website.

Browser selectors are deliberately isolated in xiaohongshu_browser. Cookies
live only in the OS credential vault, never in tasks, IPC, backups or logs.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

from screator import events, image_posts, media, publications
from screator.db import _now
from screator.exceptions import AppError

NOTE_ID = re.compile(r"[0-9a-f]{24}")
TITLE_LIMIT = 20
TEXT_LIMIT = 1000


class AuthRequired(AppError): pass
class RateLimited(AppError): pass
class Rejected(AppError): pass
class UnknownResult(AppError): pass


def validate_metadata(snapshot):
    title, description = snapshot.get("title"), snapshot.get("description")
    # Match the website's UTF-16 counters, including emoji surrogate pairs.
    for value, limit, label in ((title, TITLE_LIMIT, "标题"), (description, TEXT_LIMIT, "正文")):
        if label == "正文" and snapshot.get("media_type") == "video" and value == "": continue
        if not isinstance(value, str) or not value.strip() or len(value.encode("utf-16-le")) // 2 > limit:
            raise AppError(f"小红书{label}须为 1～{limit} 字。")


class XiaohongshuService:
    def __init__(self, store, config, emit, client=None):
        self.store, self.config, self.emit = store, config, emit
        if client is None:
            from screator.xiaohongshu_browser import BrowserClient
            client = BrowserClient()
        self.client = client
        self.auth_lock = threading.RLock()

    def account(self):
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM xiaohongshu_accounts WHERE lifecycle='active'").fetchone()
            return dict(row) if row else None

    def _credential(self, value=...):
        vault = self.config.vault
        if not hasattr(vault, "get_password"):
            import keyring
            vault = keyring
        name = "xiaohongshu_cookies"
        try:
            if value is None:
                if vault.get_password(self.config.vault_service, name):
                    vault.delete_password(self.config.vault_service, name)
            elif value is not ...:
                vault.set_password(self.config.vault_service, name, json.dumps(value))
            saved = vault.get_password(self.config.vault_service, name)
            return json.loads(saved) if saved else []
        except Exception:
            raise AppError("无法访问系统凭据库中的小红书登录态。") from None

    def status(self, verify=False):
        # Read-only checks may run inside an existing task transaction. Do not
        # acquire the auth lock here (login takes auth lock before the DB lock).
        account, error = self.account(), ""
        if verify and account:
            try:
                user_id, _ = self.client.identity(self._credential())
                if user_id != account["user_id"]:
                    raise AuthRequired("小红书登录账号与绑定账号不一致，请重新登录。")
                state = "valid"
            except AppError as exc:
                error = str(exc)
                state = "expired" if isinstance(exc, AuthRequired) else "rate_limited" if isinstance(exc, RateLimited) else "unavailable"
            with self.store.transaction() as db:
                db.execute("UPDATE xiaohongshu_accounts SET auth_state=? WHERE account_id=? AND binding_revision=? AND lifecycle='active'", (state, account["account_id"], account["binding_revision"]))
            account = self.account()
        return {"account": account, "can_sync": bool(account and account["auth_state"] == "valid"),
                "error": error, "limit": 1, "capabilities": {"auto_publish": True, "experimental": True}}

    def start(self):
        with self.auth_lock:
            self._ensure_not_publishing()
            self.client.start()
            return {"status": "waiting"}

    def cancel(self):
        with self.auth_lock:
            self.client.cancel()
            return {"cancelled": True}

    def poll(self):
        with self.auth_lock:
            result = self.client.poll()
            if result["status"] != "done":
                return {"status": result["status"]}
            cookies = result["cookies"]
            user_id, nickname = self.client.identity(cookies)
            with self.store.transaction() as db:
                self._ensure_not_publishing()
                old = self.account()
                if old and old["user_id"] != user_id:
                    raise AppError("小红书登录账号与已绑定账号不一致，请先处理旧目标并归档账号。")
                self._credential(cookies)
                existing = db.execute("SELECT account_id FROM xiaohongshu_accounts WHERE user_id=?", (user_id,)).fetchone()
                if existing:
                    db.execute("UPDATE xiaohongshu_accounts SET lifecycle='active',auth_state='valid',nickname=?,binding_revision=binding_revision+1 WHERE account_id=?", (nickname, existing[0]))
                else:
                    db.execute("INSERT INTO xiaohongshu_accounts(account_id,user_id,nickname) VALUES(?,?,?)", (str(uuid.uuid4()), user_id, nickname))
            self.emit("xiaohongshu.auth.changed", {})
            return {"status": "done"}

    def _ensure_not_publishing(self):
        with self.store._lock:
            if self.store._conn.execute("SELECT 1 FROM task_publications WHERE platform='xiaohongshu' AND status IN ('uploading_media','creating')").fetchone():
                raise AppError("小红书正在发布，请等待结果后修改登录态。")

    def clear(self, archive=False):
        with self.auth_lock, self.store.transaction() as db:
            self._ensure_not_publishing()
            account = self.account()
            if account:
                if archive and db.execute("SELECT 1 FROM task_publications WHERE platform='xiaohongshu' AND account_id=? AND status NOT IN ('submitted','abandoned')", (account["account_id"],)).fetchone():
                    raise AppError("请先完成、核对或放弃小红书旧目标，再归档账号。")
                self.client.cancel()
                self._credential(None)
                db.execute("UPDATE xiaohongshu_accounts SET auth_state='missing',binding_revision=binding_revision+1,lifecycle=? WHERE account_id=?", ("archived" if archive else "active", account["account_id"]))
            return self.status()

    def archive(self): return self.clear(archive=True)

    def check(self, account_id=None, revision=None, auto=False):
        status = self.status(verify=True)
        account = status["account"]
        if not status["can_sync"]:
            if account and account["auth_state"] == "rate_limited":
                raise RateLimited(status["error"] or "小红书限流，请稍后手动恢复队列。")
            if account and account["auth_state"] == "unavailable":
                raise AppError(status["error"] or "小红书登录状态暂时无法检查，请稍后重试。")
            raise AuthRequired(status["error"] or "请先到账号与连接登录小红书。")
        if ((account_id is not None and account_id != account["account_id"])
                or (revision is not None and (type(revision) is not int or revision != account["binding_revision"]))):
            raise AppError("小红书账号状态已变化，请刷新窗口。")
        return account

    def resume(self):
        self.check()
        self.emit("xiaohongshu.queue.resume", {})
        return {"resumed": True}

    def validate_assets(self, item, snapshot):
        task = self.store.require(item.task_id)
        expected = "image" if task.source_kind == "image_post" else "video"
        if snapshot.get("media_type", "image") != expected:
            raise AppError("小红书素材类型与任务不一致，请重新创建任务。")
        validate_metadata(snapshot)
        if task.source_kind != "image_post":
            path = Path(task.video_path)
            if not path.is_file() or path.suffix.lower() not in (".mp4", ".mov") or path.stat().st_size <= 0:
                raise AppError("小红书视频须为有效的 MP4 或 MOV 文件。")
            info = media.probe_brief(path)
            if not info or not info.get("has_video") or not info.get("duration") or info["duration"] <= 0:
                raise AppError("无法验证小红书视频内容或时长，请检查视频文件。")
            return [path]
        paths = image_posts.prepared_paths(task)
        # Animated GIFs cannot silently turn into a different image on this target.
        if any(image_posts.image_info(path)["extension"] not in (".jpg", ".png") for path in paths):
            raise AppError("同步小红书的图片仅支持 JPG、PNG，请替换 GIF 图片。")
        return paths

    def publish(self, item):
        p = publications.for_platform(self.store, item.task_id, "xiaohongshu")
        if not p or p["status"] not in ("queued", "waiting"):
            raise AppError("小红书目标状态已变化，已停止发布。")
        with self.auth_lock:
            account = self.check(p["account_id"])
            snapshot = json.loads(p["snapshot"])
            if snapshot.get("user_id") != account["user_id"]:
                raise AuthRequired("小红书投稿账号与准备时的账号不一致。")
            paths = self.validate_assets(item, snapshot)
            with self.store.transaction() as db:
                if db.execute("SELECT 1 FROM xiaohongshu_attempts WHERE publication_id=? AND phase IN ('creating','unknown','submitted')", (p["publication_id"],)).fetchone():
                    raise AppError("小红书已记录发布意图，请先核对结果，禁止重发。")
                db.execute("INSERT OR REPLACE INTO xiaohongshu_attempts(publication_id,phase,updated_at) VALUES(?,'uploading',?)", (p["publication_id"], _now()))
                publications.change(self.store, p["publication_id"], status="uploading_media", error="")
            cookies = self._credential()
        publications.project(self.store, item.task_id)
        def creating():
            if item.cancel.is_set(): raise events.Cancelled("任务已取消，已保留素材。")
            with self.store.transaction() as db:
                db.execute("UPDATE xiaohongshu_attempts SET phase='creating',updated_at=? WHERE publication_id=?", (_now(), p["publication_id"]))
                publications.change(self.store, p["publication_id"], status="creating")
            publications.project(self.store, item.task_id)
        try:
            remote_id = self.client.publish(cookies, paths, snapshot, creating, item.cancel, events.capture_progress("uploading"))
            if not isinstance(remote_id, str) or not NOTE_ID.fullmatch(remote_id):
                raise UnknownResult("小红书未返回有效笔记 ID，请核对创作中心。")
            # A receipt wins even if cancellation arrived during the final request.
            with self.store.transaction() as db:
                db.execute("UPDATE xiaohongshu_attempts SET phase='submitted',remote_id=?,updated_at=? WHERE publication_id=?", (remote_id, _now(), p["publication_id"]))
                publications.change(self.store, p["publication_id"], status="submitted", remote_id=remote_id, error="", retain_assets=1)
            publications.project(self.store, item.task_id)
        except Exception as exc:
            with self.store.transaction() as db:
                phase = db.execute("SELECT phase FROM xiaohongshu_attempts WHERE publication_id=?", (p["publication_id"],)).fetchone()[0]
                if phase == "submitted": raise
                uncertain = phase == "creating" and not isinstance(exc, Rejected)
                state = "submission_unknown" if uncertain else "cancelled" if isinstance(exc, events.Cancelled) else "failed"
                db.execute("UPDATE xiaohongshu_attempts SET phase=?,updated_at=? WHERE publication_id=?", ("unknown" if uncertain else "rejected", _now(), p["publication_id"]))
                publications.change(self.store, p["publication_id"], status=state, retain_assets=1,
                                    error="小红书发布结果待核对，请检查创作中心后再决定是否重试。" if uncertain else str(exc))
            publications.project(self.store, item.task_id)
            # Only an auth/rate limit before the creation boundary may pause the lane.
            if uncertain and isinstance(exc, (AuthRequired, RateLimited)):
                raise UnknownResult("小红书发布结果待核对。") from None
            raise

    def resolve(self, publication_id, remote_id, not_submitted):
        with self.store.transaction() as db:
            db.execute("UPDATE xiaohongshu_attempts SET phase=?,remote_id=?,updated_at=? WHERE publication_id=?", ("resolved_not_submitted" if not_submitted else "submitted", remote_id, _now(), publication_id))

    def close(self): self.client.close()
