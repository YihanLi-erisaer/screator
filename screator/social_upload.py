"""Official X, Facebook Page and TikTok publishers with independent receipts.

User access tokens are supplied from the user's developer application and kept
in the OS vault. No app secrets or session URLs enter publication snapshots.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

from screator import events, media, publications
from screator.acfun import AcfunService
from screator.db import _now
from screator.exceptions import AppError

PLATFORMS = ("twitter", "facebook", "tiktok")
NAMES = {"twitter": "X（Twitter）", "facebook": "Facebook Page", "tiktok": "TikTok"}
PRIVACY_LEVELS = {"PUBLIC_TO_EVERYONE", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"}
PROTECTED_PHASES = {"creating", "processing", "unknown", "submitted"}


class AuthRequired(AppError): pass
class RateLimited(AppError): pass
class Rejected(AppError): pass
class PublishFailed(Rejected): pass
class UnknownResult(AppError): pass


def platform_name(platform):
    if platform not in PLATFORMS: raise AppError("同步投稿平台无效。")
    return NAMES[platform]


class SocialClient:
    HOSTS = {"api.x.com", "graph.facebook.com", "graph-video.facebook.com", "open.tiktokapis.com"}
    UPLOAD_HOSTS = {"open-upload.tiktokapis.com", "upload.us.tiktokapis.com", "upload.eu.tiktokapis.com"}

    def request(self, method, url, *, token="", data=None, form=None, files=None, raw=None, headers=None):
        target = urlsplit(url)
        if (target.scheme != "https" or target.hostname not in self.HOSTS | self.UPLOAD_HOSTS
                or target.username or target.password or target.port or target.fragment):
            raise AppError("平台请求地址未获准。")
        outgoing = dict(headers or {})
        if token:
            if target.hostname in self.UPLOAD_HOSTS: raise AppError("禁止向上传地址发送账号访问凭据。")
            outgoing["Authorization"] = "Bearer " + token
        try:
            with requests.request(method, url, json=data, data=raw if raw is not None else form,
                                  files=files, headers=outgoing, timeout=(15, 90), allow_redirects=False, stream=True) as response:
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 2_000_000: raise UnknownResult("平台响应过大，请核对投稿结果。")
                # TikTok's binary upload endpoint can return an empty or text body.
                try: payload = json.loads(body) if body else {}
                except ValueError:
                    if target.hostname in self.UPLOAD_HOSTS and response.status_code in (201, 206): payload = {}
                    else: raise UnknownResult("平台响应格式异常，请核对投稿结果。") from None
                if not isinstance(payload, dict): raise UnknownResult("平台响应格式异常。")
                error = payload.get("error") or {}
                code = error.get("code") if isinstance(error, dict) else None
                if response.status_code == 401 or code in (190, "access_token_invalid", "scope_not_authorized"):
                    raise AuthRequired("平台授权已失效或权限不足，请重新导入授权凭据。")
                if response.status_code in (402, 429) or code in (4, 17, 32, 613, "rate_limit_exceeded", "spam_risk_too_many_posts", "reached_active_user_cap"):
                    raise RateLimited("平台配额、余额或上传次数受限，请处理后手动恢复队列。")
                if code == "unaudited_client_can_only_post_to_private_accounts":
                    raise Rejected("TikTok 未审核客户端只能向私密账号发布，请检查账号可见性或完成应用审核。")
                if response.status_code >= 500: raise UnknownResult("平台服务暂时不可用，请核对结果。")
                if not 200 <= response.status_code < 300:
                    raise Rejected(f"平台拒绝请求（HTTP {response.status_code}），请检查权限、视频或文案。")
                if error and code != "ok":
                    # Never echo a remote response, token, upload URL or request body.
                    raise Rejected("平台明确拒绝本次请求，请检查应用审核、权限和投稿信息。")
                if payload.get("errors") and not (payload.get("data") or {}).get("id"):
                    raise Rejected("X API 返回业务错误，请检查权限和投稿信息。")
                return payload
        except requests.RequestException as exc:
            raise UnknownResult("平台网络中断，请核对投稿状态。") from exc


class SocialUploadService:
    def __init__(self, store, config, emit, platform, client_factory=SocialClient):
        self.name = platform_name(platform)
        self.platform, self.store, self.config, self.emit = platform, store, config, emit
        self.client_factory = client_factory

    def _vault(self, key="credentials", value=...):
        vault = self.config.vault
        if not hasattr(vault, "get_password"):
            import keyring
            vault = keyring
        name = f"social:{self.platform}:{key}"
        try:
            if value is None:
                if vault.get_password(self.config.vault_service, name): vault.delete_password(self.config.vault_service, name)
                return None
            if value is not ...: vault.set_password(self.config.vault_service, name, json.dumps(value))
            saved = vault.get_password(self.config.vault_service, name)
            return json.loads(saved) if saved else None
        except Exception as exc:
            raise AppError(f"无法访问系统凭据库中的 {self.name} 授权。") from exc

    def account(self):
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM social_accounts WHERE platform=? AND lifecycle='active'", (self.platform,)).fetchone()
            return dict(row) if row else None

    def _credentials(self):
        value = self._vault()
        if not value or not value.get("access_token"): raise AuthRequired(f"请先绑定 {self.name}。")
        return value

    def _identity(self, value):
        client, token = self.client_factory(), value["access_token"]
        if self.platform == "twitter":
            user = client.request("GET", "https://api.x.com/2/users/me", token=token).get("data", {})
            identity, nickname = user.get("id"), user.get("name")
        elif self.platform == "facebook":
            user = client.request("GET", f"https://graph.facebook.com/{value['api_version']}/me?fields=id,name,category", token=token)
            identity, nickname = user.get("id"), user.get("name")
            if identity != value["page_id"] or not user.get("category"):
                raise AuthRequired("请使用目标 Facebook 公共主页的 Page Access Token，不能使用个人账号 Token。")
        else:
            user = client.request("GET", "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name", token=token).get("data", {}).get("user", {})
            identity, nickname = user.get("open_id"), user.get("display_name")
        if not isinstance(identity, str) or not identity or len(identity) > 128 or not isinstance(nickname, str) or not nickname:
            raise AuthRequired(f"{self.name} 未返回可验证的账号身份。")
        if self.platform != "tiktok" and not re.fullmatch(r"[1-9][0-9]{0,29}", identity):
            raise AuthRequired("平台账号 ID 无效。")
        return identity, nickname

    def bind(self, access_token, page_id="", api_version="v25.0"):
        if not isinstance(access_token, str) or not 8 <= len(access_token) <= 16384 or any(c.isspace() for c in access_token):
            raise AppError("授权凭据格式无效。")
        if self.platform == "facebook" and (not isinstance(page_id, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", page_id)
                or not isinstance(api_version, str) or not re.fullmatch(r"v[1-9][0-9]\.0", api_version)):
            raise AppError("请填写有效的 Facebook Page ID 和 Graph API 版本。")
        credentials = {"access_token": access_token, "page_id": page_id, "api_version": api_version}
        identity, nickname = self._identity(credentials)
        if self.platform == "tiktok": self._creator(access_token)
        with self.store.transaction() as db:
            old = self.account()
            if old and old["subject_id"] != identity: raise AppError("授权账号与已绑定账号不一致，请先处理旧目标并归档。")
            if db.execute("SELECT 1 FROM task_publications WHERE platform=? AND status IN ('uploading_media','creating')", (self.platform,)).fetchone():
                raise AppError("平台正在投稿，请等待结果后更新凭据。")
            self._vault(value=credentials)
            row = db.execute("SELECT account_id FROM social_accounts WHERE platform=? AND subject_id=?", (self.platform, identity)).fetchone()
            if row:
                db.execute("UPDATE social_accounts SET lifecycle='active',auth_state='valid',nickname=?,binding_revision=binding_revision+1 WHERE account_id=?", (nickname, row[0]))
            else:
                db.execute("INSERT INTO social_accounts(account_id,platform,subject_id,nickname) VALUES(?,?,?,?)", (str(uuid.uuid4()), self.platform, identity, nickname))
        self.emit("social.auth.changed", {"platform": self.platform})
        return self.status()

    def status(self, verify=False):
        account, error = self.account(), ""
        if account and verify:
            try:
                subject, _ = self._identity(self._credentials())
                if subject != account["subject_id"]: raise AuthRequired("当前授权账号与投稿目标不一致。")
                state = "valid"
            except AppError as exc:
                error, state = str(exc), "expired" if isinstance(exc, AuthRequired) else "unavailable"
            with self.store.transaction() as db:
                db.execute("UPDATE social_accounts SET auth_state=? WHERE account_id=?", (state, account["account_id"]))
            account = self.account()
        return {"account": account, "can_sync": bool(account and account["auth_state"] == "valid"), "error": error,
                "limit": 1, "capabilities": {"auto_publish": self.platform != "tiktok", "official_api": True}}

    def check(self, account_id=None, revision=None, auto=False):
        if auto and self.platform == "tiktok": raise AppError("TikTok 需要逐次预览、手动选择可见性并确认上传，请使用预览模式。")
        status = self.status(True)
        account = status["account"]
        if not status["can_sync"]:
            if not account or account["auth_state"] in ("missing", "expired"):
                raise AuthRequired(status["error"] or f"请先绑定 {self.name}。")
            raise AppError(status["error"] or "平台授权暂不可验证。")
        if account_id and account_id != account["account_id"] or revision is not None and revision != account["binding_revision"]:
            raise AppError("平台账号状态已变化，请刷新窗口。")
        return account

    def clear(self, archive=False):
        with self.store.transaction() as db:
            account = self.account()
            if account:
                states = "status NOT IN ('submitted','abandoned')" if archive else "status IN ('uploading_media','creating')"
                if db.execute(f"SELECT 1 FROM task_publications WHERE platform=? AND account_id=? AND {states}", (self.platform, account["account_id"])).fetchone():
                    raise AppError("请先等待在途投稿结束；归档前需完成、核对或放弃旧目标。")
                self._vault(value=None)
                db.execute("UPDATE social_accounts SET auth_state='missing',binding_revision=binding_revision+1,lifecycle=? WHERE account_id=?", ("archived" if archive else "active", account["account_id"]))
        return self.status()

    def resume(self):
        self.check()
        self.emit("social.queue.resume", {"platform": self.platform})
        return {"resumed": True}

    def _creator(self, token):
        value = self.client_factory().request("POST", "https://open.tiktokapis.com/v2/post/publish/creator_info/query/", token=token, data={}).get("data", {})
        options = value.get("privacy_level_options")
        if (not isinstance(options, list) or not options or any(v not in PRIVACY_LEVELS for v in options)
                or not isinstance(value.get("creator_nickname"), str) or type(value.get("max_video_post_duration_sec")) is not int):
            raise AppError("TikTok 未返回可用的创作者权限。")
        return {k: value.get(k) for k in ("creator_nickname", "creator_username", "privacy_level_options", "comment_disabled", "duet_disabled", "stitch_disabled", "max_video_post_duration_sec")}

    def creator(self, account_id=None):
        if self.platform != "tiktok": raise AppError("此平台无需创作者权限选项。")
        self.check(account_id)
        return self._creator(self._credentials()["access_token"])

    def validate_metadata(self, snapshot, *, require_consent=False):
        title = snapshot.get("title")
        limit = {"twitter": 280, "facebook": 255, "tiktok": 2200}[self.platform]
        if not isinstance(title, str) or not title.strip() or len(title.encode("utf-16-le")) // 2 > limit:
            raise AppError(f"{self.name} 文案须为 1～{limit} 字，请在平台预览中编辑。")
        if self.platform == "tiktok":
            if snapshot.get("privacy_level") not in PRIVACY_LEVELS: raise AppError("请手动选择 TikTok 可见性。")
            for name in ("allow_comment", "allow_duet", "allow_stitch", "brand_organic", "brand_content", "is_aigc"):
                if type(snapshot.get(name)) is not bool: raise AppError("TikTok 互动或内容声明格式无效。")
            cover = snapshot.get("cover_timestamp_ms")
            if type(cover) is not int or not 0 <= cover <= 2_147_483_647: raise AppError("TikTok 封面时间无效。")
            if snapshot["brand_content"] and snapshot["privacy_level"] == "SELF_ONLY": raise AppError("TikTok 品牌合作内容不能选择仅自己可见。")
            disclosure = snapshot.get("content_disclosure", False)
            if type(disclosure) is not bool or disclosure != bool(snapshot["brand_organic"] or snapshot["brand_content"]):
                raise AppError("请明确选择 TikTok 推广声明，以及推广自己的品牌或第三方品牌。")
            if require_consent and (snapshot.get("consent") is not True or snapshot.get("original_content") is not True):
                raise AppError("请确认这是你的原创内容，并同意 TikTok 对应的音乐使用及品牌内容声明。")

    def validate_assets(self, item, snapshot, *, require_consent=True):
        self.validate_metadata(snapshot, require_consent=require_consent)
        task = self.store.require(item.task_id)
        path = Path(task.video_path)
        limit = 4_000_000_000 if self.platform == "tiktok" else 16 * 1024**3
        if not path.is_file() or not 0 < path.stat().st_size <= limit: raise AppError("平台视频缺失或超出大小限制。")
        if self.platform == "tiktok":
            creator = self.creator(snapshot.get("account_id"))
            if snapshot["privacy_level"] not in creator["privacy_level_options"]: raise AppError("TikTok 可见性选项已变化，请重新预览。")
            for feature in ("comment", "duet", "stitch"):
                if snapshot[f"allow_{feature}"] and creator[f"{feature}_disabled"]: raise AppError("TikTok 账号已禁用所选互动，请重新预览。")
            info = media.probe_brief(path)
            if not info or not info.get("duration") or info["duration"] > creator["max_video_post_duration_sec"]:
                raise AppError("视频时长超过此 TikTok 账号的投稿限制或无法验证时长。")
            if snapshot["cover_timestamp_ms"] >= info["duration"] * 1000: raise AppError("TikTok 封面时间须在视频时长内。")

    def attempt(self, publication_id):
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM social_upload_attempts WHERE publication_id=?", (publication_id,)).fetchone()
            return dict(row) if row else None

    def phase(self, publication_id, phase, **fields):
        if set(fields) - {"media_id", "remote_id"}: raise ValueError("Invalid attempt fields")
        with self.store.transaction() as db:
            db.execute("UPDATE social_upload_attempts SET phase=?,updated_at=?" + "".join(f",{key}=?" for key in fields) + " WHERE publication_id=?", (phase, _now(), *fields.values(), publication_id))

    def _creating(self, p):
        with self.store.transaction():
            self.phase(p["publication_id"], "creating")
            publications.change(self.store, p["publication_id"], status="creating")
        publications.project(self.store, p["task_id"])

    def _success(self, p, remote_id):
        if not isinstance(remote_id, str) or not remote_id or len(remote_id) > 128: raise UnknownResult("平台没有返回作品标识，请核对结果。")
        with self.store.transaction():
            self.phase(p["publication_id"], "submitted", remote_id=remote_id)
            publications.change(self.store, p["publication_id"], status="submitted", remote_id=remote_id, error="")
        publications.project(self.store, p["task_id"])

    def publish(self, item):
        p = publications.for_platform(self.store, item.task_id, self.platform)
        account = self.check(p["account_id"], auto=item.mode == "auto")
        snapshot = json.loads(p["snapshot"])
        if snapshot.get("subject_id") != account["subject_id"]: raise AuthRequired("投稿快照账号与当前授权不一致。")
        previous = self.attempt(p["publication_id"])
        if previous and previous["phase"] in PROTECTED_PHASES: raise AppError("此目标已有发布意图，请先核对结果，禁止重发。")
        self.validate_assets(item, snapshot)
        task = self.store.require(item.task_id)
        source = Path(task.video_path)
        info = media.probe_brief(source)
        if not info: raise AppError("无法读取投稿视频媒体信息。")
        if not media._upload_compatible(info):
            # An isolated file preserves Bilibili/YouTube's original-byte strategy.
            source = media.ensure_bilibili_mp4(source, Path(task.work_dir) / f"{self.platform}-compatible.mp4")
        limit = 4_000_000_000 if self.platform == "tiktok" else 16 * 1024**3
        if source.stat().st_size > limit: raise AppError("兼容视频超过此平台的上传大小上限。")
        video_hash = AcfunService._sha256(source)
        request_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO social_upload_attempts(publication_id,phase,media_sha256,request_sha256,updated_at) VALUES(?,'uploading',?,?,?)", (p["publication_id"], video_hash, request_hash, _now()))
            publications.change(self.store, p["publication_id"], status="uploading_media", error="")
        publications.project(self.store, item.task_id)
        try:
            credentials = self._credentials()
            getattr(self, "_publish_" + self.platform)(p, snapshot, source, credentials)
        except (Rejected, AuthRequired, RateLimited) as exc:
            attempt = self.attempt(p["publication_id"])
            if attempt and attempt["phase"] == "processing" and not isinstance(exc, PublishFailed):
                self.phase(p["publication_id"], "unknown")
                publications.change(self.store, p["publication_id"], status="submission_unknown", retain_assets=1,
                    error="TikTok 已接收视频，但状态查询失败，请核对原发布标识，禁止重发。")
                raise UnknownResult("TikTok 状态待核对。") from exc
            # Explicit API rejection means this request did not publish a work.
            self.phase(p["publication_id"], "rejected")
            publications.change(self.store, p["publication_id"], status="queued", error="")
            raise
        except Exception:
            attempt = self.attempt(p["publication_id"])
            if attempt and attempt["phase"] in PROTECTED_PHASES - {"submitted"}:
                self.phase(p["publication_id"], "unknown")
                publications.change(self.store, p["publication_id"], status="submission_unknown", retain_assets=1,
                    error=f"{self.name} 发布结果待核对，禁止自动重发；请检查对应账号的作品。")
            raise
        finally:
            publications.project(self.store, item.task_id)

    def _publish_twitter(self, p, snapshot, path, credentials):
        client, token = self.client_factory(), credentials["access_token"]
        size = path.stat().st_size
        result = client.request("POST", "https://api.x.com/2/media/upload/initialize", token=token,
            data={"media_type": "video/mp4", "media_category": "tweet_video", "total_bytes": size})
        media_id = str(result.get("data", {}).get("id") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,29}", media_id): raise AppError("X 未返回视频上传标识。")
        self.phase(p["publication_id"], "uploading", media_id=media_id)
        with path.open("rb") as source:
            index = 0
            while chunk := source.read(4 * 1024 * 1024):
                events.check_cancelled()
                client.request("POST", f"https://api.x.com/2/media/upload/{media_id}/append", token=token,
                    form={"segment_index": str(index)}, files={"media": ("chunk", chunk, "application/octet-stream")})
                index += 1
                events.progress("uploading_media", force=True, percent=round(source.tell() * 100 / size, 1))
        result = client.request("POST", f"https://api.x.com/2/media/upload/{media_id}/finalize", token=token, data={})
        processing = result.get("data", {}).get("processing_info")
        for _ in range(24):
            if not processing or processing.get("state") == "succeeded": break
            if processing.get("state") == "failed": raise Rejected("X 视频处理失败，请检查媒体格式。")
            events.check_cancelled()
            time.sleep(min(5, max(1, float(processing.get("check_after_secs") or 1))))
            result = client.request("GET", "https://api.x.com/2/media/upload?" + urlencode({"command": "STATUS", "media_id": media_id}), token=token)
            processing = result.get("data", {}).get("processing_info")
        else: raise AppError("X 视频处理尚未完成，请稍后继续此目标。")
        events.check_cancelled()
        self._creating(p)
        result = client.request("POST", "https://api.x.com/2/tweets", token=token, data={"text": snapshot["title"], "media": {"media_ids": [media_id]}})
        remote = str(result.get("data", {}).get("id") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,29}", remote): raise UnknownResult("X 未返回有效作品 ID。")
        self._success(p, remote)

    def _publish_facebook(self, p, snapshot, path, credentials):
        client, token = self.client_factory(), credentials["access_token"]
        url = f"https://graph-video.facebook.com/{credentials['api_version']}/{credentials['page_id']}/videos"
        size = path.stat().st_size
        result = client.request("POST", url, token=token, form={"upload_phase": "start", "file_size": str(size)})
        session, video_id = str(result.get("upload_session_id") or ""), str(result.get("video_id") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,29}", video_id) or not re.fullmatch(r"[1-9][0-9]{0,29}", session):
            raise AppError("Facebook 未返回有效视频上传会话。")
        self.phase(p["publication_id"], "uploading", media_id=video_id)
        start, end = int(result.get("start_offset", -1)), int(result.get("end_offset", -1))
        with path.open("rb") as source:
            while start < size:
                if not 0 <= start < end <= size or end - start > 128 * 1024**2: raise AppError("Facebook 分片范围无效。")
                events.check_cancelled()
                source.seek(start)
                chunk = source.read(end - start)
                if len(chunk) != end - start: raise AppError("视频文件读取中断。")
                result = client.request("POST", url, token=token, form={"upload_phase": "transfer", "upload_session_id": session, "start_offset": str(start)}, files={"video_file_chunk": ("chunk", chunk, "application/octet-stream")})
                next_start, next_end = int(result.get("start_offset", -1)), int(result.get("end_offset", -1))
                if next_start != end or not next_start <= next_end <= size: raise AppError("Facebook 上传进度未确认，请继续原目标。")
                start, end = next_start, next_end
                events.progress("uploading_media", force=True, percent=round(start * 100 / size, 1))
        events.check_cancelled()
        self._creating(p)
        result = client.request("POST", url, token=token, form={"upload_phase": "finish", "upload_session_id": session,
            "title": snapshot["title"], "description": snapshot["description"], "published": "true"})
        if result.get("success") is not True: raise UnknownResult("Facebook 未确认视频发布结果。")
        self._success(p, video_id)

    def _publish_tiktok(self, p, snapshot, path, credentials):
        client, token = self.client_factory(), credentials["access_token"]
        size = path.stat().st_size
        chunk_size = min(size, 10_000_000)
        count = max(1, size // chunk_size)
        result = client.request("POST", "https://open.tiktokapis.com/v2/post/publish/video/init/", token=token, data={
            "post_info": {"title": snapshot["title"], "privacy_level": snapshot["privacy_level"],
                "disable_comment": not snapshot["allow_comment"], "disable_duet": not snapshot["allow_duet"], "disable_stitch": not snapshot["allow_stitch"],
                "brand_organic_toggle": snapshot["brand_organic"], "brand_content_toggle": snapshot["brand_content"],
                "video_cover_timestamp_ms": snapshot["cover_timestamp_ms"], "is_aigc": snapshot["is_aigc"]},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}})
        data = result.get("data", {})
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        target = urlsplit(upload_url or "")
        if not isinstance(publish_id, str) or not 1 <= len(publish_id) <= 64 or target.scheme != "https" or target.hostname not in SocialClient.UPLOAD_HOSTS or target.username or target.password or target.port or target.fragment:
            raise AppError("TikTok 未返回有效上传会话。")
        self.phase(p["publication_id"], "uploading", media_id=publish_id)
        # URI contains private upload credentials; do not save it in SQLite.
        self._vault("session:" + p["publication_id"], upload_url)
        with path.open("rb") as source:
            for index in range(count):
                events.check_cancelled()
                offset = source.tell()
                chunk = source.read(size - offset if index == count - 1 else chunk_size)
                if not chunk: raise AppError("TikTok 视频读取中断。")
                if index == count - 1: self._creating(p)
                client.request("PUT", upload_url, raw=chunk, headers={"Content-Type": "video/mp4", "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}"})
                events.progress("uploading_media", force=True, percent=round(source.tell() * 100 / size, 1))
        self.phase(p["publication_id"], "processing")
        for _ in range(6):
            if self._check_tiktok(p, token):
                self._vault("session:" + p["publication_id"], None)
                return
            time.sleep(2)
        raise UnknownResult("TikTok 仍在处理，请通过原发布标识核对状态，不能重新上传。")

    def _check_tiktok(self, p, token):
        attempt = self.attempt(p["publication_id"])
        result = self.client_factory().request("POST", "https://open.tiktokapis.com/v2/post/publish/status/fetch/", token=token, data={"publish_id": attempt["media_id"]}).get("data", {})
        if result.get("status") == "FAILED": raise PublishFailed("TikTok 明确报告发布失败，请检查素材及平台限制后重试。")
        if result.get("status") != "PUBLISH_COMPLETE": return False
        # Private posts do not return public IDs; the publish_id is the receipt.
        ids = result.get("publicaly_available_post_id") or []
        remote = str(ids[0]) if ids else attempt["media_id"]
        self._success(p, remote)
        return True

    def check_publication(self, publication_id):
        p = publications.get(self.store, publication_id)
        if p["platform"] != self.platform or p["status"] != "submission_unknown": raise AppError("此目标无需平台核对。")
        self.check(p["account_id"])
        attempt = self.attempt(publication_id)
        if not attempt or not attempt["media_id"]: raise AppError("缺少可查询的发布标识，请在平台人工核对。")
        credentials = self._credentials()
        try:
            if self.platform == "tiktok": done = self._check_tiktok(p, credentials["access_token"])
            elif self.platform == "facebook":
                value = self.client_factory().request("GET", f"https://graph.facebook.com/{credentials['api_version']}/{attempt['media_id']}?fields=id,published", token=credentials["access_token"])
                done = value.get("published") is True and str(value.get("id")) == attempt["media_id"]
                if done: self._success(p, attempt["media_id"])
            else: raise AppError("X 创建帖子结果丢失时，请到该账号主页人工核对帖子 ID。")
        except PublishFailed:
            if self.platform != "tiktok": raise
            self.phase(publication_id, "rejected")
            publications.change(self.store, publication_id, status="failed", retain_assets=1, error="TikTok 已明确报告发布失败，可以重新预览后确认。")
            publications.project(self.store, p["task_id"])
            return {"status": "failed"}
        return {"status": "submitted" if done else "processing"}

    def resolve(self, publication_id, remote_id, not_submitted):
        if not not_submitted and self.platform in ("twitter", "facebook") and not re.fullmatch(r"[1-9][0-9]{0,29}", remote_id):
            raise AppError("平台作品 ID 必须为数字。")
        if not not_submitted and self.platform == "tiktok" and not re.fullmatch(r"[A-Za-z0-9_.~+-]{1,128}", remote_id):
            raise AppError("TikTok 作品 ID 或发布标识格式无效。")
        self.phase(publication_id, "resolved_not_submitted" if not_submitted else "submitted", remote_id=remote_id)
        self._vault("session:" + publication_id, None)
