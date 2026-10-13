"""Official YouTube OAuth and resumable publisher; secrets stay in the OS vault."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from screator import events, publications
from screator.db import _now
from screator.exceptions import AppError


class AuthRequired(AppError): pass
class RateLimited(AppError): pass
class UnknownResult(AppError): pass
class SessionExpired(AppError): pass


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly")
CHUNK_SIZE = 8 * 1024 * 1024  # Multiple of Google's 256 KiB chunk boundary.


def tags_from_bilibili(value):
    tags = [p.strip() for p in re.split(r"[,，、]", value) if p.strip()]
    if sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1) > 500:
        raise AppError("YouTube 标签总长度不能超过 500 字。")
    return tags


def validate_metadata(snapshot):
    title, description = snapshot.get("title"), snapshot.get("description")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100 or any(c in title for c in "<>"):
        raise AppError("YouTube 标题须为 1～100 字，不能包含尖括号。")
    if not isinstance(description, str) or len(description.encode("utf-8")) > 5000 or any(c in description for c in "<>"):
        raise AppError("YouTube 简介不能超过 5000 UTF-8 字节或包含尖括号。")
    if not isinstance(snapshot.get("category_id"), str) or not re.fullmatch(r"[1-9][0-9]{0,4}", snapshot["category_id"]):
        raise AppError("请选择 YouTube 视频分类。")
    if snapshot.get("privacy_status") not in ("private", "unlisted", "public"):
        raise AppError("YouTube 可见性无效。")
    if type(snapshot.get("made_for_kids")) is not bool:
        raise AppError("请明确选择 YouTube 视频是否面向儿童。")
    if not isinstance(snapshot.get("tags"), list) or any(not isinstance(t, str) or not t.strip() for t in snapshot["tags"]):
        raise AppError("YouTube 标签格式无效。")
    tags_from_bilibili(",".join(snapshot["tags"]))


class GoogleClient:
    """Bounded HTTPS requests. Redirects never receive bearer tokens."""
    HOSTS = {"oauth2.googleapis.com", "www.googleapis.com"}

    def request(self, method, url, *, token="", data=None, raw=None, headers=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.HOSTS or parsed.username
                or parsed.password or parsed.port or parsed.fragment):
            raise AppError("YouTube 请求地址无效。")
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else raw
        outgoing = dict(headers or {})
        if data is not None: outgoing["Content-Type"] = "application/json; charset=utf-8"
        if token: outgoing["Authorization"] = "Bearer " + token
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=60)
        try:
            conn.request(method, parsed.path + ("?" + parsed.query if parsed.query else ""), body=body, headers=outgoing)
            response = conn.getresponse()
            content = response.read(2_000_001)
            if len(content) > 2_000_000: raise UnknownResult("YouTube 响应过大，需核对结果。")
            try: payload = json.loads(content) if content else {}
            except ValueError: raise UnknownResult("YouTube 返回无效响应，需核对结果。") from None
            if response.status not in (200, 201, 308):
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                reasons = {e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)} if isinstance(error, dict) else set()
                if response.status == 401 or error == "invalid_grant":
                    raise AuthRequired("YouTube 授权已失效，请重新授权。")
                if response.status == 429 or reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "uploadLimitExceeded"}:
                    raise RateLimited("YouTube 配额或上传次数受限，请稍后手动恢复队列。")
                if response.status in (404, 410) and "upload_id=" in parsed.query:
                    raise SessionExpired("YouTube 上传会话已过期。")
                if response.status >= 500:
                    raise UnknownResult("YouTube 服务暂不可用，需核对上传状态。")
                raise AppError(f"YouTube 请求被拒绝（HTTP {response.status}）。请检查 API、权限和投稿信息。")
            if not isinstance(payload, dict): raise UnknownResult("YouTube 响应格式无效。")
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), payload
        except (OSError, http.client.HTTPException) as exc:
            raise UnknownResult("YouTube 网络中断，请继续原目标或核对结果。") from exc
        finally:
            conn.close()

    def token(self, form):
        return self.request("POST", "https://oauth2.googleapis.com/token", raw=urlencode(form).encode(),
                            headers={"Content-Type": "application/x-www-form-urlencoded"})[2]


class YoutubeUploadService:
    def __init__(self, store, config, emit, client_factory=GoogleClient):
        self.store, self.config, self.emit = store, config, emit
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.auth = None
        self._categories_cache = {}

    def _vault(self, name, value=...):
        vault = self.config.vault
        if not hasattr(vault, "get_password"):
            import keyring
            vault = keyring
        try:
            if value is None:
                if vault.get_password(self.config.vault_service, name): vault.delete_password(self.config.vault_service, name)
                return None
            if value is not ...: vault.set_password(self.config.vault_service, name, json.dumps(value))
            saved = vault.get_password(self.config.vault_service, name)
            return json.loads(saved) if saved else None
        except Exception as exc:
            raise AppError("无法访问系统凭据库中的 YouTube 授权。") from exc

    def account(self):
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM youtube_accounts WHERE lifecycle='active'").fetchone()
            return dict(row) if row else None

    def configure(self, path):
        with self.store._lock, self.lock:
            if self.account(): raise AppError("请先归档 YouTube 账号再更换 OAuth 客户端。")
            self.cancel()
            source = Path(path)
            if not source.is_absolute() or not source.is_file() or source.stat().st_size > 65536:
                raise AppError("请选择 Google 下载的桌面应用 OAuth JSON 文件。")
            try: client = json.loads(source.read_text(encoding="utf-8-sig"))["installed"]
            except (OSError, ValueError, KeyError, TypeError):
                raise AppError("OAuth 文件无效，客户端类型必须为桌面应用。") from None
            if (not isinstance(client, dict) or not isinstance(client.get("client_id"), str)
                    or not client["client_id"].endswith(".apps.googleusercontent.com")
                    or not isinstance(client.get("client_secret"), str) or not client["client_secret"]):
                raise AppError("OAuth 文件缺少客户端 ID 或客户端密钥。")
            self._vault("youtube_oauth_client", {k: client[k] for k in ("client_id", "client_secret")})
            return self.status()

    def _token(self):
        with self.lock:
            credentials = self._vault("youtube_tokens") or {}
            if not credentials.get("refresh_token"): raise AuthRequired("请先授权 YouTube。")
            if credentials.get("expires_at", 0) <= time.time() + 90:
                client = self._vault("youtube_oauth_client")
                if not client: raise AuthRequired("请先配置 YouTube OAuth 客户端。")
                result = self.client_factory().token({**client, "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"]})
                if not result.get("access_token"): raise AuthRequired("YouTube 未返回有效访问凭据。")
                credentials.update(access_token=result["access_token"], expires_at=time.time() + int(result.get("expires_in", 3600)))
                if result.get("refresh_token"): credentials["refresh_token"] = result["refresh_token"]
                self._vault("youtube_tokens", credentials)
            return credentials["access_token"]

    def _identity(self, token):
        result = self.client_factory().request("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", token=token)[2]
        channels = result.get("items", [])
        if len(channels) != 1 or not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channels[0].get("id", "")):
            raise AuthRequired("未取得唯一的 YouTube 频道，请创建频道并重新选择授权频道。")
        return channels[0]["id"], channels[0].get("snippet", {}).get("title") or channels[0]["id"]

    def status(self, verify=False):
        account, error = self.account(), ""
        if verify and account:
            try:
                channel_id, _ = self._identity(self._token())
                if channel_id != account["channel_id"]: raise AuthRequired("YouTube 当前授权频道与绑定频道不一致。")
                state = "valid"
            except AppError as exc:
                state = "expired" if isinstance(exc, AuthRequired) else "unavailable"
                error = str(exc)
            with self.store.transaction() as db:
                db.execute("UPDATE youtube_accounts SET auth_state=? WHERE account_id=?", (state, account["account_id"]))
            account = self.account()
        return {"account": account, "configured": bool(self._vault("youtube_oauth_client")),
                "can_sync": bool(account and account["auth_state"] == "valid"),
                "capabilities": {"auto_publish": True}, "error": error, "limit": 1}

    def start(self):
        with self.store._lock, self.lock:
            client = self._vault("youtube_oauth_client")
            if not client: raise AppError("请先导入桌面应用 OAuth JSON 文件。")
            self.cancel()
            session = {"state": secrets.token_urlsafe(32), "verifier": secrets.token_urlsafe(64),
                       "status": "waiting", "expires": time.monotonic() + 300}
            service = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args): pass  # Never log OAuth codes.

                def do_GET(self):
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query)
                    if (parsed.path != "/oauth2/callback" or query.get("state") != [session["state"]]
                            or time.monotonic() > session["expires"]):
                        self.send_error(400, "Invalid authorization callback")
                        return
                    with service.lock:
                        if service.auth is not session or session["status"] != "waiting":
                            self.send_error(400, "Authorization cancelled")
                            return
                        session["status"] = "processing"
                    try:
                        if query.get("error") or len(query.get("code", [])) != 1: raise AuthRequired("YouTube 授权已取消或未授予权限。")
                        result = service.client_factory().token({**client, "grant_type": "authorization_code",
                            "code": query["code"][0], "redirect_uri": session["redirect"], "code_verifier": session["verifier"]})
                        if not result.get("access_token") or not result.get("refresh_token"):
                            raise AuthRequired("YouTube 未返回离线授权凭据，请重新授权并允许所需权限。")
                        if not set(SCOPES) <= set(result.get("scope", "").split()):
                            raise AuthRequired("请授予 YouTube 上传和频道读取权限。")
                        identity = service._identity(result["access_token"])
                        with service.store.transaction(), service.lock:
                            if service.auth is not session: raise AuthRequired("本次 YouTube 授权已取消。")
                            service._bind(identity, {"access_token": result["access_token"], "refresh_token": result["refresh_token"],
                                "expires_at": time.time() + int(result.get("expires_in", 3600))})
                            session["status"] = "done"
                        service.emit("youtube.auth.changed", {"account_id": service.account()["account_id"]})
                        message = "YouTube authorization completed. You can close this tab."
                    except Exception as exc:
                        session.update(status="failed", error=str(exc) if isinstance(exc, AppError) else "YouTube 授权未完成，请重试。")
                        message = "YouTube authorization failed. Return to screator for details."
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(message.encode())

            class CallbackServer(HTTPServer):
                def get_request(self):
                    connection, address = super().get_request()
                    connection.settimeout(5)
                    return connection, address

            server = CallbackServer(("127.0.0.1", 0), Callback)
            server.timeout = .25
            session["redirect"] = f"http://127.0.0.1:{server.server_port}/oauth2/callback"
            self.auth = session

            def listen():
                try:
                    while self.auth is session and session["status"] == "waiting" and time.monotonic() < session["expires"]:
                        server.handle_request()
                    if session["status"] == "waiting": session["status"] = "expired"
                finally: server.server_close()

            session["thread"] = threading.Thread(target=listen, name="youtube-oauth", daemon=True)
            session["thread"].start()
            challenge = base64.urlsafe_b64encode(hashlib.sha256(session["verifier"].encode()).digest()).rstrip(b"=").decode()
            url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": client["client_id"],
                "redirect_uri": session["redirect"], "response_type": "code", "scope": " ".join(SCOPES),
                "state": session["state"], "code_challenge": challenge, "code_challenge_method": "S256",
                "access_type": "offline", "prompt": "consent select_account"})
            if not webbrowser.open(url):
                self.cancel()
                raise AppError("无法打开系统浏览器，请检查默认浏览器设置。")
            return {"status": "waiting"}

    def _bind(self, identity, credentials):
        channel_id, nickname = identity
        with self.store.transaction() as db:
            old = self.account()
            if old and old["channel_id"] != channel_id:
                raise AppError("授权频道与已绑定的 YouTube 频道不一致，请先处理旧任务并归档。")
            self._vault("youtube_tokens", credentials)
            existing = db.execute("SELECT account_id FROM youtube_accounts WHERE channel_id=?", (channel_id,)).fetchone()
            if existing:
                db.execute("UPDATE youtube_accounts SET lifecycle='active',auth_state='valid',nickname=?,binding_revision=binding_revision+1 WHERE account_id=?", (nickname, existing[0]))
            else:
                import uuid
                db.execute("INSERT INTO youtube_accounts(account_id,channel_id,nickname) VALUES(?,?,?)", (str(uuid.uuid4()), channel_id, nickname))

    def poll(self):
        with self.lock:
            session = self.auth
            return {"status": session["status"] if session else "idle", "error": session.get("error", "") if session else ""}

    def cancel(self):
        with self.lock:
            self.auth = None
            return {"status": "idle"}

    def close(self):
        session = self.auth
        self.cancel()
        if session and session.get("thread"): session["thread"].join(timeout=2)

    def clear(self):
        with self.store.transaction() as db, self.lock:
            if db.execute("SELECT 1 FROM task_publications WHERE platform='youtube' AND status IN ('uploading_media','creating')").fetchone():
                raise AppError("YouTube 正在投稿，请等待结果。")
            self.cancel()
            self._vault("youtube_tokens", None)
            db.execute("UPDATE youtube_accounts SET auth_state='missing',binding_revision=binding_revision+1 WHERE lifecycle='active'")
        return self.status()

    def archive(self):
        with self.store.transaction() as db, self.lock:
            account = self.account()
            if account and db.execute("SELECT 1 FROM task_publications WHERE platform='youtube' AND account_id=? AND status NOT IN ('submitted','abandoned')", (account["account_id"],)).fetchone():
                raise AppError("请先完成、核对或放弃该 YouTube 频道的投稿。")
            self.cancel()
            self._vault("youtube_tokens", None)
            db.execute("UPDATE youtube_accounts SET lifecycle='archived',auth_state='missing' WHERE lifecycle='active'")
        return self.status()

    def check(self, account_id=None, revision=None, auto=False):
        status = self.status(True)
        account = status["account"]
        if not status["can_sync"]:
            if not account or account["auth_state"] in ("expired", "missing"):
                raise AuthRequired(status["error"] or "请先授权 YouTube。")
            raise AppError(status["error"] or "YouTube 授权暂不可验证。")
        if account_id and account_id != account["account_id"] or revision is not None and revision != account["binding_revision"]:
            raise AppError("YouTube 账号状态已变化，请刷新窗口。")
        return account

    def resume(self):
        self.check()
        self.emit("youtube.queue.resume", {})
        return {"resumed": True}

    def categories(self, ui_language="zh-CN"):
        locales = {"zh-CN": "zh_CN", "zh-HK": "zh_HK", "en": "en"}
        if not isinstance(ui_language, str) or ui_language not in locales:
            raise AppError("请选择支持的界面语言。")
        cached = self._categories_cache.get(ui_language)
        if cached and time.monotonic() - cached[0] < 300:
            return {"items": cached[1]}
        url = "https://www.googleapis.com/youtube/v3/videoCategories?part=snippet&regionCode=US&hl=" + locales[ui_language]
        result = self.client_factory().request("GET", url, token=self._token())[2]
        items = [{"category_id": v["id"], "name": v["snippet"]["title"]} for v in result.get("items", []) if v.get("snippet", {}).get("assignable")]
        if not items: raise AppError("YouTube 未返回可投稿的视频分类，请稍后重试。")
        self._categories_cache[ui_language] = time.monotonic(), items
        return {"items": items}

    def validate_assets(self, item, snapshot):
        validate_metadata(snapshot)
        if snapshot["category_id"] not in {v["category_id"] for v in self.categories()["items"]}:
            raise AppError("YouTube 分类不可投稿，请重新选择。")
        task = self.store.require(item.task_id)
        if not Path(task.video_path).is_file() or not 0 < Path(task.video_path).stat().st_size <= 256 * 1024**3:
            raise AppError("YouTube 视频素材缺失或大小超出限制。")
        if not Path(task.cover_path).is_file(): raise AppError("YouTube 封面素材缺失。")

    def _attempt(self, publication_id):
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM youtube_attempts WHERE publication_id=?", (publication_id,)).fetchone()
            return dict(row) if row else None

    def _phase(self, publication_id, phase, remote_id=""):
        with self.store.transaction() as db:
            db.execute("UPDATE youtube_attempts SET phase=?,remote_id=?,updated_at=? WHERE publication_id=?", (phase, remote_id, _now(), publication_id))

    def resolve(self, publication_id, remote_id, not_submitted):
        self._phase(publication_id, "resolved_not_submitted" if not_submitted else "submitted", remote_id)
        self._vault("youtube_session:" + publication_id, None)

    def publish(self, item):
        p = publications.for_platform(self.store, item.task_id, "youtube")
        account = self.check(p["account_id"])
        snapshot = json.loads(p["snapshot"])
        if snapshot.get("channel_id") != account["channel_id"]: raise AuthRequired("YouTube 投稿频道与授权频道不一致。")
        self.validate_assets(item, snapshot)
        task = self.store.require(item.task_id)
        from screator.acfun import AcfunService
        video_hash = AcfunService._sha256(task.video_path)
        metadata = {"snippet": {"title": snapshot["title"], "description": snapshot["description"],
            "tags": snapshot["tags"], "categoryId": snapshot["category_id"]},
            "status": {"privacyStatus": snapshot["privacy_status"], "selfDeclaredMadeForKids": snapshot["made_for_kids"]}}
        request_hash = hashlib.sha256(json.dumps({"channel": account["channel_id"], "body": metadata}, sort_keys=True).encode()).hexdigest()
        previous = self._attempt(p["publication_id"])
        if previous and previous["phase"] in ("finalizing", "unknown", "submitted"):
            raise AppError("YouTube 已记录最终上传意图，请先核对结果，禁止重发。")
        session_key = "youtube_session:" + p["publication_id"]
        session = self._vault(session_key) if previous and previous["media_sha256"] == video_hash and previous["request_sha256"] == request_hash and previous["phase"] != "resolved_not_submitted" else None
        client = self.client_factory()
        size = Path(task.video_path).stat().st_size
        if not session:
            # Only non-final partial uploads may be replaced when the draft changes.
            with self.store.transaction() as db:
                db.execute("INSERT OR REPLACE INTO youtube_attempts(publication_id,phase,media_sha256,request_sha256,updated_at) VALUES(?,'starting',?,?,?)", (p["publication_id"], video_hash, request_hash, _now()))
            _, headers, _ = client.request("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", token=self._token(), data=metadata,
                headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
            session = headers.get("location")
            parsed = urlsplit(session or "")
            if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com" or parsed.username or parsed.password or parsed.port or parsed.fragment or not parsed.path.startswith("/upload/youtube/"):
                raise AppError("YouTube 未返回有效上传会话。")
            self._vault(session_key, session)
            self._phase(p["publication_id"], "uploading")
        publications.change(self.store, p["publication_id"], status="uploading_media", error="")
        publications.project(self.store, item.task_id)
        finalizing = False
        remote_id = ""
        try:
            status, headers, result = client.request("PUT", session, token=self._token(), raw=b"", headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"})
            offset = self._offset(status, headers, size)
            if status in (200, 201): remote_id = result.get("id", "")
            with open(task.video_path, "rb") as source:
                while status == 308 and offset < size:
                    events.check_cancelled()
                    source.seek(offset)
                    chunk = source.read(min(CHUNK_SIZE, size - offset))
                    if not chunk: raise AppError("YouTube 视频读取中断。")
                    finalizing = offset + len(chunk) == size
                    if finalizing:
                        self._phase(p["publication_id"], "finalizing")
                        publications.change(self.store, p["publication_id"], status="creating")
                        publications.project(self.store, item.task_id)
                    try:
                        status, headers, result = client.request("PUT", session, token=self._token(), raw=chunk,
                            headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}"})
                    except UnknownResult:
                        # Query the SAME session instead of creating another video.
                        status, headers, result = client.request("PUT", session, token=self._token(), raw=b"", headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"})
                    next_offset = self._offset(status, headers, size)
                    if status == 308 and next_offset <= offset: raise UnknownResult("YouTube 上传未取得进展，请继续原目标。")
                    offset = next_offset
                    if status in (200, 201): remote_id = result.get("id", "")
                    elif finalizing:
                        finalizing = False
                        self._phase(p["publication_id"], "uploading")
                        publications.change(self.store, p["publication_id"], status="uploading_media")
                    events.progress("uploading_media", force=True, percent=round(offset * 100 / size, 1))
            if not isinstance(remote_id, str) or not VIDEO_ID.fullmatch(remote_id):
                self._unknown(p)
                raise UnknownResult("YouTube 上传响应缺少视频 ID，请核对创作中心。")
            # Persist the video receipt BEFORE the optional thumbnail operation.
            with self.store.transaction():
                self._phase(p["publication_id"], "submitted", remote_id)
                publications.change(self.store, p["publication_id"], status="submitted", remote_id=remote_id, error="")
            warning = ""
            try:
                cover = Path(task.cover_path)
                if cover.stat().st_size > 50 * 1024**2: raise AppError("封面文件过大。")
                client.request("POST", "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?" + urlencode({"videoId": remote_id, "uploadType": "media"}),
                    token=self._token(), raw=cover.read_bytes(), headers={"Content-Type": "image/jpeg"})
            except Exception:
                warning = "视频已上传；自定义封面设置未确认，请在 YouTube Studio 检查并补设封面。"
            publications.change(self.store, p["publication_id"], error=warning, retain_assets=int(bool(warning)))
            self._vault(session_key, None)
        except SessionExpired:
            if finalizing: self._unknown(p)
            else:
                self._vault(session_key, None)
                self._phase(p["publication_id"], "expired")
            raise
        except Exception:
            if finalizing and not remote_id: self._unknown(p)
            raise
        finally:
            publications.project(self.store, item.task_id)

    @staticmethod
    def _offset(status, headers, size):
        if status in (200, 201): return size
        value = headers.get("range")
        if value is None: return 0
        match = re.fullmatch(r"bytes=0-([0-9]+)", value)
        if not match or int(match[1]) >= size: raise UnknownResult("YouTube 上传进度响应无效。")
        return int(match[1]) + 1

    def _unknown(self, p):
        self._phase(p["publication_id"], "unknown")
        publications.change(self.store, p["publication_id"], status="submission_unknown", retain_assets=1,
                            error="YouTube 最终上传结果待核对，请检查该频道的 YouTube Studio；不会自动重发。")
