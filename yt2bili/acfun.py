"""Experimental AcFun web-session publisher. No credentials enter task records."""
from __future__ import annotations

import http.cookiejar
import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, HTTPSHandler, HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError

from yt2bili import events, publications
from yt2bili.db import _now
from yt2bili.exceptions import Yt2BiliError


class AuthRequired(Yt2BiliError): pass
class RateLimited(Yt2BiliError): pass
class UnknownResult(Yt2BiliError): pass
class BusinessError(Yt2BiliError):
    """A parsed AcFun response explicitly rejected the operation."""
    pass


VERIFICATION_HOSTS = {"captcha.zt.kuaishou.com", "captcha.kuaishou.com", "passport.kuaishou.com", "app.m.kuaishou.com"}
VERIFICATION_COOKIES = {"ztIdentityVerificationType", "ztIdentityVerificationCheckToken"}


def verification_page(url):
    """Format the official user-operated challenge, following AcFun's web SDK."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in VERIFICATION_HOSTS
                or parsed.username or parsed.password or parsed.port or parsed.fragment
                or len(url) > 16384 or any(ord(c) < 32 for c in url)):
            return ""
        query = parse_qs(parsed.query)
        if query.get("needQrcode") == ["1"]:
            kind = query.get("needQrType", ["identity-verify-auto"])[0]
            if not re.fullmatch(r"[\w-]+", kind): return ""
            return "https://passport.kuaishou.com/pc/identity/qrcode?" + urlencode({
                "env": "production", "type": kind, "kpn": "ACFUN_APP", "sid": "acfun.api",
                "url": url, "displayType": "popup"})
        if "captcha" in parsed.hostname:
            parsed = parsed._replace(path="/iframe/index.html" if parsed.path.endswith(".html") else parsed.path)
            query = {("configUrl" if k == "url" else k): v for k, v in query.items()}
        query["displayType"] = ["popup"]
        return urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))
    except (TypeError, ValueError):
        return ""


class VerificationRequired(BusinessError):
    def __init__(self, code, url=""):
        super().__init__(f"AcFun 需要安全验证（代码 {code}）。请在任务详情完成官方验证，再确认投稿；视频素材已保留。")
        self.url = verification_page(url)


def tags_from_bilibili(value):
    if not isinstance(value, str):
        raise Yt2BiliError("Bilibili 标签格式无效，无法同步到 AcFun。")
    tags = [part.strip() for part in re.split(r"[,，、]", value) if part.strip()]
    if not tags or len(tags) > 6 or any(len(tag) > 30 for tag in tags):
        raise Yt2BiliError("同步 AcFun 时，Bilibili 标签须为 1～6 个且每个不超过 30 字。")
    return tags


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AuthRequired("AcFun 登录已跳转，请重新扫码。")


class WebClient:
    """A small HTTPS client with an explicit destination allowlist and cookie jar."""
    HOSTS = {"scan.acfun.cn", "www.acfun.cn", "member.acfun.cn", "upload.kuaishouzt.com"}

    def __init__(self, cookies=None):
        self.jar = http.cookiejar.CookieJar()
        for item in cookies or []:
            if item.get("domain", "").lstrip(".") not in {"acfun.cn", "scan.acfun.cn", "www.acfun.cn", "member.acfun.cn"}:
                continue
            self.jar.set_cookie(http.cookiejar.Cookie(0, item["name"], item["value"], None, False,
                item["domain"], True, item["domain"].startswith("."), item.get("path", "/"), True,
                False, None, False, None, None, {}, False))
        self.opener = build_opener(HTTPSHandler(), HTTPCookieProcessor(self.jar), _NoRedirect())

    def cookies(self):
        return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.jar if c.domain.lstrip(".") in {"acfun.cn", "scan.acfun.cn", "www.acfun.cn", "member.acfun.cn"}]

    def request(self, method, url, data=None, raw=None, code=0, timeout=60, expect_list=False):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in self.HOSTS or parsed.username or parsed.password or parsed.port or parsed.fragment:
            raise Yt2BiliError("AcFun 请求目标未获准。")
        body = raw if raw is not None else (urlencode(data).encode() if data is not None else None)
        origin = "https://www.acfun.cn" if parsed.hostname in {"scan.acfun.cn", "www.acfun.cn"} else "https://member.acfun.cn"
        headers = {"User-Agent": "Mozilla/5.0 StarDazz/0.2", "Accept": "application/json, text/plain, */*",
                   "Origin": origin, "Referer": origin + ("/login" if parsed.hostname == "scan.acfun.cn" else "/upload-video")}
        if body is not None: headers["Content-Type"] = "application/octet-stream" if raw is not None else "application/x-www-form-urlencoded"
        # The upload CDN must never receive AcFun session cookies.
        if parsed.hostname == "upload.kuaishouzt.com":
            opener = build_opener(HTTPSHandler(), _NoRedirect())
        else:
            opener = self.opener
        try:
            with opener.open(Request(url, data=body, headers=headers, method=method), timeout=timeout) as response:
                if response.status != 200:
                    raise Yt2BiliError("AcFun 请求未成功，已停止操作。")
                contents = response.read(2_000_001)
                if len(contents) > 2_000_000:
                    raise Yt2BiliError("AcFun 响应超出安全限制。")
                payload = json.loads(contents)
        except HTTPError as exc:
            if exc.code in (401, 403): raise AuthRequired("AcFun 登录已失效，请重新扫码。") from exc
            if exc.code == 429: raise RateLimited("AcFun 限流，请稍后手动恢复。") from exc
            raise Yt2BiliError(f"AcFun HTTP {exc.code}，已停止操作。") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise UnknownResult("AcFun 网络或响应不确定，请核对投稿状态。") from exc
        endpoint = parsed.path.rsplit("/", 1)[-1]
        if expect_list and isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise UnknownResult(f"AcFun {endpoint} 响应格式异常。")
        error = payload.get("errMsg")
        error = error if isinstance(error, dict) else {}
        result_code = error.get("result", payload.get("result"))
        if type(result_code) is int and 400001 <= result_code <= 410999:
            raise VerificationRequired(result_code, error.get("error_url") or payload.get("error_url") or "")
        if payload.get("isError") is True or (type(result_code) is int and result_code != code):
            detail = error.get("error_msg") or payload.get("error_msg") or payload.get("msg") or ""
            detail = " ".join(detail.split()) if isinstance(detail, str) else ""
            # Only expose the message, never the response or its tokens/cookies.
            for secret in [c.value for c in self.jar] + [str(v) for k, v in (data or {}).items() if re.search(r"token|password|secret", k, re.I)]:
                if secret: detail = detail.replace(secret, "[已隐藏]")
            detail = re.sub(r"https?://\S+", "[链接已隐藏]", detail)
            label = str(result_code) if type(result_code) is int else "未知"
            raise BusinessError(f"AcFun {endpoint} 业务响应失败（代码 {label}）。" + detail[:300])
        if expect_list or payload.get("result") != code:
            raise UnknownResult(f"AcFun {endpoint} 响应缺少有效结果，无法确认操作结果。")
        return payload

    def set_verification(self, proof=None):
        for cookie in list(self.jar):
            if cookie.name in VERIFICATION_COOKIES:
                self.jar.clear(cookie.domain, cookie.path, cookie.name)
        if proof:
            for name, value in zip(("ztIdentityVerificationType", "ztIdentityVerificationCheckToken"), proof):
                self.jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False,
                    ".acfun.cn", True, True, "/", True, True, None, True, None, None, {}, False))

    def warmup_session(self):
        """Allow the two AcFun web hosts to issue their own session cookies after QR confirmation."""
        for url in ("https://www.acfun.cn/", "https://member.acfun.cn/"):
            try:
                with self.opener.open(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as response:
                    response.read(1)
            except (HTTPError, URLError, TimeoutError, OSError, AuthRequired):
                pass


class AcfunService:
    def __init__(self, store, config, emit, client_factory=WebClient):
        self.store, self.config, self.emit = store, config, emit
        self.client_factory = client_factory
        self.qr = None
        self._qr_poll_lock = threading.Lock()
        self._channels_cache = None
        # Challenge URLs are kept in the OS vault; proofs remain one-use in-memory values.
        self._verifications = {}
        self._verification_proofs = {}
        self._verification_lock = threading.RLock()

    def _verification_context(self, publication_id):
        p = publications.get(self.store, publication_id)
        account = self.account()
        if p["platform"] != "acfun" or not account or p["account_id"] != account["account_id"] or account["auth_state"] != "valid":
            raise AuthRequired("AcFun 验证账号已变化，请重新登录并继续此目标。")
        snapshot = json.loads(p["snapshot"])
        task = self.store.require(p["task_id"])
        # Bind the actual submission, not mutable queue/UI bookkeeping fields.
        request = {k: snapshot.get(k) for k in ("user_id", "title", "description", "tags", "channel_id", "_uploaded_media")}
        request.update(url=task.url, account_id=account["account_id"], binding_revision=account["binding_revision"])
        return p, hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _challenge_vault(self, publication_id, value=...):
        # A separate key per target avoids read/modify/write races across submissions.
        vault = self.config.vault
        name = "acfun_verification:" + publication_id
        try:
            if value is None:
                if vault.get_password(self.config.vault_service, name):
                    vault.delete_password(self.config.vault_service, name)
                return None
            if value is not ...:
                vault.set_password(self.config.vault_service, name, json.dumps(value))
                return value
            saved = vault.get_password(self.config.vault_service, name)
            return json.loads(saved) if saved else None
        except Exception:
            # Keep the in-memory entry usable even if the credential store is unavailable.
            return None

    def _forget_challenge(self, publication_id):
        self._verifications.pop(publication_id, None)
        self._challenge_vault(publication_id, None)

    def verification(self, publication_id):
        with self._verification_lock:
            p, context = self._verification_context(publication_id)
            if p["status"] != "failed":
                return {"status": "unavailable", "reason": "state_changed", "message": "投稿状态已变化，请刷新任务详情。"}
            challenge = self._verifications.get(publication_id) or self._challenge_vault(publication_id)
            reason = ""
            if not isinstance(challenge, dict) or not {"id", "url", "context", "expires_at"} <= challenge.keys():
                reason = "missing"
            elif challenge["context"] != context:
                reason = "context_changed"
            elif not isinstance(challenge["expires_at"], (int, float)) or time.time() >= challenge["expires_at"]:
                reason = "expired"
            if reason:
                self._forget_challenge(publication_id)
                logging.getLogger(__name__).info("AcFun verification unavailable: publication=%s reason=%s", publication_id, reason)
                messages = {"missing": "本次验证入口未保存或来自旧版后台。", "context_changed": "账号或投稿内容已变化，需要新的验证入口。", "expired": "官方验证入口已过期。"}
                return {"status": "refresh_required", "reason": reason, "message": messages[reason]}
            if not challenge["url"]:
                return {"status": "unavailable", "reason": "unsupported_url", "message": "AcFun 未返回受支持的官方验证地址，请到创作中心处理。"}
            self._verifications[publication_id] = challenge
            return {"status": "ready", "challenge_id": challenge["id"], "url": challenge["url"]}

    def complete_verification(self, publication_id, challenge_id, verification_type, token):
        with self._verification_lock:
            current = self.verification(publication_id)
            if current["status"] != "ready":
                raise Yt2BiliError(current["message"])
            if current["challenge_id"] != challenge_id:
                raise Yt2BiliError("验证入口已更新，请重新打开。")
            if (not isinstance(verification_type, (str, int)) or isinstance(verification_type, bool)
                    or not re.fullmatch(r"[\w-]{1,64}", str(verification_type))
                    or not isinstance(token, str) or not re.fullmatch(r"[\x21-\x7e]{1,8192}", token)
                    or any(c in token for c in ';,"\\')):
                raise Yt2BiliError("官方验证结果无效，请重新完成验证。")
            challenge = self._verifications[publication_id]
            self._forget_challenge(publication_id)
            self._verification_proofs[publication_id] = {
                "context": challenge["context"], "created": time.monotonic(),
                "proof": (str(verification_type), token)}
            publications.change(self.store, publication_id, status="ready", error="")
            publications.project(self.store, publications.get(self.store, publication_id)["task_id"])
            return {"ready": True}

    def _credential(self, value=...):
        vault = self.config.vault
        if not hasattr(vault, "get_password"):
            import keyring
            vault = keyring
        try:
            if value is not ...:
                if value is None:
                    if vault.get_password(self.config.vault_service, "acfun_cookies"):
                        vault.delete_password(self.config.vault_service, "acfun_cookies")
                else: vault.set_password(self.config.vault_service, "acfun_cookies", json.dumps(value))
            saved = vault.get_password(self.config.vault_service, "acfun_cookies")
            return json.loads(saved) if saved else []
        except Exception as exc:
            raise Yt2BiliError("无法访问系统凭据库中的 AcFun 登录态。") from exc

    def account(self):
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM acfun_accounts WHERE lifecycle='active'").fetchone()
            return dict(row) if row else None

    def _client(self): return self.client_factory(self._credential())

    def _identity(self, client):
        result = client.request("POST", "https://www.acfun.cn/rest/pc-direct/user/personalInfo", data={})
        info = result.get("info")
        if not isinstance(info, dict) or not re.fullmatch(r"[1-9][0-9]*", str(info.get("userId", ""))):
            raise AuthRequired("AcFun 未返回可验证的账号 ID。")
        return str(info["userId"]), str(info.get("name") or info.get("userName") or info["userId"])

    def status(self, verify=False):
        account = self.account()
        error = ""
        if verify and account:
            try:
                user_id, _ = self._identity(self._client())
                if user_id != account["user_id"]: raise AuthRequired("AcFun 登录账号与绑定账号不一致。")
                with self.store.transaction() as db:
                    db.execute("UPDATE acfun_accounts SET auth_state='valid' WHERE account_id=?", (account["account_id"],))
                account = self.account()
            except Yt2BiliError as exc:
                error = str(exc)
                with self.store.transaction() as db:
                    db.execute("UPDATE acfun_accounts SET auth_state='expired' WHERE account_id=?", (account["account_id"],))
                account = self.account()
        enabled = self.config.values.get("acfun_experimental_enabled", False)
        return {"account": account, "can_sync": bool(enabled and account and account["auth_state"] == "valid"),
                "capabilities": {"auto_publish": True, "experimental": True}, "enabled": enabled,
                "error": error or ("AcFun 实验性网页投稿尚未启用。" if not enabled else ""), "limit": 1}

    def start(self):
        qr = {"client": self.client_factory(), "created": time.monotonic(), "phase": "scan"}
        self.qr = qr
        try:
            response = qr["client"].request("GET", "https://scan.acfun.cn/rest/pc-direct/qr/start?type=WEB_LOGIN")
        except Exception:
            if self.qr is qr: self.qr = None
            raise
        if self.qr is not qr: return {"status": "idle"}
        token, signature, image = response.get("qrLoginToken"), response.get("qrLoginSignature"), response.get("imageData")
        if not all(isinstance(v, str) and v for v in (token, signature, image)):
            if self.qr is qr: self.qr = None
            raise Yt2BiliError("AcFun 二维码响应缺少必要字段。")
        qr.update(token=token, signature=signature)
        return {"status": "waiting", "qrcode": image, "expires_in_ms": min(int(response.get("expireTime") or 120000), 120000)}

    def poll(self):
        if not self._qr_poll_lock.acquire(blocking=False):
            qr = self.qr
            return {"status": "scanned" if qr and qr["phase"] == "accept" else "waiting" if qr else "idle"}
        try:
            return self._poll()
        finally:
            self._qr_poll_lock.release()

    def _poll(self):
        qr = self.qr
        if not qr: return {"status": "idle"}
        if time.monotonic() - qr["created"] > 120:
            self.qr = None
            return {"status": "expired"}
        path = "scanResult" if qr["phase"] == "scan" else "acceptResult"
        url = "https://scan.acfun.cn/rest/pc-direct/qr/" + path + "?" + urlencode({"qrLoginToken": qr["token"], "qrLoginSignature": qr["signature"]})
        try: result = qr["client"].request("GET", url, timeout=15)
        except UnknownResult:
            if self.qr is not qr: return {"status": "idle"}
            return {"status": "waiting" if qr["phase"] == "scan" else "scanned"}
        except Yt2BiliError as exc:
            if self.qr is not qr: return {"status": "idle"}
            if "100400002" in str(exc): self.qr = None; return {"status": "expired"}
            raise
        if self.qr is not qr: return {"status": "idle"}
        if qr["phase"] == "scan":
            qr["signature"] = str(result.get("qrLoginSignature") or qr["signature"])
            qr["phase"] = "accept"
            return {"status": "scanned"}
        warmup = getattr(qr["client"], "warmup_session", None)
        if callable(warmup): warmup()
        user_id, nickname = self._identity(qr["client"])
        if self.qr is not qr: return {"status": "idle"}
        cookies = qr["client"].cookies()
        if not cookies: raise AuthRequired("扫码成功但未取得 AcFun 登录凭据。")
        with self.store.transaction() as db:
            old = self.account()
            if old and old["user_id"] != user_id:
                raise Yt2BiliError("扫码账号与已绑定的 AcFun 账号不一致，请先处理旧任务。")
            if old:
                db.execute("UPDATE acfun_accounts SET nickname=?,auth_state='valid',binding_revision=binding_revision+1 WHERE account_id=?", (nickname, old["account_id"]))
            else:
                archived = db.execute("SELECT account_id FROM acfun_accounts WHERE user_id=?", (user_id,)).fetchone()
                if archived:
                    db.execute("UPDATE acfun_accounts SET lifecycle='active',auth_state='valid',nickname=?,binding_revision=binding_revision+1 WHERE account_id=?", (nickname, archived[0]))
                else:
                    db.execute("INSERT INTO acfun_accounts(account_id,user_id,nickname) VALUES(?,?,?)", (str(uuid.uuid4()), user_id, nickname))
            self._credential(cookies)
        self.qr = None
        self.emit("acfun.auth.changed", {"account_id": self.account()["account_id"]})
        return {"status": "done", "account": self.account()}

    def cancel(self): self.qr = None; return {"status": "idle"}

    def clear(self):
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM task_publications WHERE platform='acfun' AND status IN ('uploading_media','creating')").fetchone():
                raise Yt2BiliError("AcFun 正在投稿，请等待结果。")
            self._credential(None)
            db.execute("UPDATE acfun_accounts SET auth_state='missing',binding_revision=binding_revision+1 WHERE lifecycle='active'")
        return self.status()

    def archive(self):
        with self.store.transaction() as db:
            account = self.account()
            if not account: return self.status()
            if db.execute("SELECT 1 FROM task_publications WHERE platform='acfun' AND account_id=? AND status NOT IN ('submitted','abandoned')", (account["account_id"],)).fetchone():
                raise Yt2BiliError("请先完成、核对或放弃该 AcFun 账号的投稿。")
            self._credential(None)
            db.execute("UPDATE acfun_accounts SET lifecycle='archived',auth_state='missing' WHERE account_id=?", (account["account_id"],))
        return self.status()

    def check(self, account_id=None, revision=None, auto=False):
        status = self.status(True)
        account = status["account"]
        if not status["can_sync"]: raise AuthRequired(status["error"] or "请先登录 AcFun。")
        if account_id and account_id != account["account_id"] or revision is not None and revision != account["binding_revision"]:
            raise Yt2BiliError("AcFun 账号状态已变化，请刷新新建任务窗口。")
        return account

    def resume(self):
        self.emit("acfun.queue.resume", {})
        return {"resumed": True}

    def channels(self):
        if not self.account(): raise AuthRequired("请先登录 AcFun，再选择投稿分区。")
        if self._channels_cache and time.monotonic() - self._channels_cache[0] < 300:
            return {"items": self._channels_cache[1]}
        tree = self._client().request("POST", "https://member.acfun.cn/common/api/getAllChannels", {}, expect_list=True)
        items = []
        def visit(nodes, parent=""):
            if not isinstance(nodes, list): raise Yt2BiliError("AcFun 分区列表格式异常。")
            for node in nodes:
                if not isinstance(node, dict): raise Yt2BiliError("AcFun 分区列表格式异常。")
                if node.get("channelType") != 2 or node.get("disableContribute"): continue
                name = str(node.get("name") or "").strip()
                label = f"{parent} / {name}" if parent else name
                if node.get("children"):
                    visit(node["children"], label)
                elif name and re.fullmatch(r"[1-9][0-9]*", str(node.get("channelId", ""))):
                    items.append({"channel_id": int(node["channelId"]), "name": label})
        visit(tree)
        if not items: raise Yt2BiliError("AcFun 未返回可投稿的视频子分区。")
        self._channels_cache = (time.monotonic(), items)
        return {"items": items}

    def validate_channel(self, channel_id):
        if type(channel_id) is not int or channel_id <= 0:
            raise Yt2BiliError("请选择 AcFun 分区。")
        if channel_id not in {item["channel_id"] for item in self.channels()["items"]}:
            raise Yt2BiliError(f"AcFun 分区 ID {channel_id} 不是可投稿的视频子分区，请重新选择具体分区。")

    def validate_assets(self, item, snapshot):
        task = self.store.require(item.task_id)
        title = snapshot.get("title") or task.title_zh
        description = snapshot.get("description") or task.desc_zh
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 50: raise Yt2BiliError("AcFun 标题须为 1～50 字，请在预览中编辑。")
        if not isinstance(description, str) or len(description) > 1000: raise Yt2BiliError("AcFun 简介超过 1000 字，请在预览中编辑。")
        if not isinstance(snapshot.get("channel_id"), int) or snapshot["channel_id"] <= 0: raise Yt2BiliError("请选择 AcFun 分区。")
        if not isinstance(snapshot.get("tags"), list) or not 1 <= len(snapshot["tags"]) <= 6 or any(not isinstance(t, str) or not t.strip() or len(t) > 30 for t in snapshot["tags"]):
            raise Yt2BiliError("AcFun 标签须为 1～6 个，每个不超过 30 字。")
        if not Path(task.video_path).is_file() or not Path(task.cover_path).is_file(): raise Yt2BiliError("AcFun 投稿素材缺失。")

    def _attempt(self, publication_id, **values):
        with self.store.transaction() as db:
            row = db.execute("SELECT attempt_id FROM acfun_attempts WHERE publication_id=? ORDER BY started_at DESC LIMIT 1", (publication_id,)).fetchone()
            if row:
                db.execute("UPDATE acfun_attempts SET " + ",".join(k + "=?" for k in values) + " WHERE attempt_id=?", (*values.values(), row[0]))

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            while block := source.read(1024 * 1024):
                events.check_cancelled()
                digest.update(block)
        return digest.hexdigest()

    def _upload_blob(self, client, path, token, part_size, item):
        size = Path(path).stat().st_size
        if not 0 < part_size <= 128 * 1024 * 1024 or size <= 0: raise Yt2BiliError("AcFun 分片参数无效。")
        count = math.ceil(size / part_size)
        with open(path, "rb") as source:
            for index in range(count):
                events.check_cancelled()
                part = source.read(part_size)
                if not part: raise Yt2BiliError("AcFun 本地素材读取中断。")
                client.request("POST", "https://upload.kuaishouzt.com/api/upload/fragment?" + urlencode({"fragment_id": index, "upload_token": token}), raw=part, code=1)
                events.progress("uploading_media", force=True, percent=round((index + 1) * 100 / count, 1))
        client.request("POST", "https://upload.kuaishouzt.com/api/upload/complete?" + urlencode({"fragment_count": count, "upload_token": token}), raw=b"", code=1)

    def publish(self, item):
        p = publications.for_platform(self.store, item.task_id, "acfun")
        account = self.check(p["account_id"], auto=item.mode == "auto")
        task = self.store.require(item.task_id)
        snapshot = json.loads(p["snapshot"])
        if snapshot.get("user_id") != account["user_id"]:
            raise AuthRequired("AcFun 投稿快照账号与当前登录账号不一致。")
        self.validate_assets(item, snapshot)
        self.validate_channel(snapshot["channel_id"])
        video_hash = self._sha256(task.video_path)
        cover_hash = self._sha256(task.cover_path)
        media_hash = hashlib.sha256((video_hash + ":" + cover_hash).encode()).hexdigest()
        request_hash = hashlib.sha256(json.dumps({"snapshot": {k: v for k, v in snapshot.items() if k != "_uploaded_media"}, "url": task.url}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        client = self._client()
        with self.store.transaction() as db:
            previous = db.execute("SELECT 1 FROM acfun_attempts WHERE publication_id=? AND create_intent_at!='' AND phase NOT IN ('resolved_not_submitted','rejected')", (p["publication_id"],)).fetchone()
            if previous: raise Yt2BiliError("此 AcFun 目标已有投稿意图，请先核对，禁止自动重发。")
            # Includes attempts from older versions that did not save a cover URL.
            reusable = db.execute("SELECT video_id FROM acfun_attempts WHERE publication_id=? AND phase='rejected' AND media_sha256=? AND video_id!='' ORDER BY started_at DESC LIMIT 1",
                                  (p["publication_id"], media_hash)).fetchone()
            db.execute("INSERT INTO acfun_attempts(attempt_id,publication_id,phase,started_at,media_sha256,request_sha256) VALUES(?,?,'uploading',?,?,?)",
                       (str(uuid.uuid4()), p["publication_id"], _now(), media_hash, request_hash))
            publications.change(self.store, p["publication_id"], status="uploading_media", error="")
        publications.project(self.store, item.task_id)
        video_id = int(reusable[0]) if reusable and re.fullmatch(r"[1-9][0-9]*", reusable[0]) else None
        if video_id is None:
            video_id = self._upload_video(client, task, item, p["publication_id"])
        self._attempt(p["publication_id"], video_id=str(video_id), phase="cover")
        cached = snapshot.get("_uploaded_media", {})
        cover_url = cached.get("cover_url") if (reusable and cached.get("media_sha256") == media_hash
                    and str(cached.get("video_id")) == str(video_id)) else None
        if not cover_url:
            cover_url = self._upload_cover(client, task, item)
        snapshot["_uploaded_media"] = {"media_sha256": media_hash, "video_id": video_id, "cover_url": cover_url}
        publications.change(self.store, p["publication_id"], snapshot=json.dumps(snapshot, ensure_ascii=False))
        events.check_cancelled()
        data = {"title": snapshot.get("title") or task.title_zh, "description": snapshot.get("description") or task.desc_zh,
                "tagNames": json.dumps(snapshot["tags"], ensure_ascii=False), "creationType": 1,
                "channelId": snapshot["channel_id"], "coverUrl": cover_url,
                "videoInfos": json.dumps([{"videoId": video_id, "title": snapshot.get("title") or task.title_zh}], ensure_ascii=False),
                "originalLinkUrl": task.url, "originalDeclare": "0", "isJoinUpCollege": "0", "isSyncKs": "0"}
        with self._verification_lock:
            proof = self._verification_proofs.pop(p["publication_id"], None)
            _, context = self._verification_context(p["publication_id"])
            self._forget_challenge(p["publication_id"])
        # Only the exact target/account/metadata receives the one-use official proof.
        if proof and proof["context"] == context and time.monotonic() - proof["created"] < 120:
            client.set_verification(proof["proof"])
        elif hasattr(client, "set_verification"):
            client.set_verification()
        with self.store.transaction():
            self._attempt(p["publication_id"], phase="creating", create_intent_at=_now())
            publications.change(self.store, p["publication_id"], status="creating")
        publications.project(self.store, item.task_id)
        try: result = client.request("POST", "https://member.acfun.cn/video/api/createDouga", data)
        except BusinessError as exc:
            message = str(exc) if isinstance(exc, VerificationRequired) else f"稿件未创建：{exc} 视频已上传，创作中心可能保留未填写投稿信息的素材草稿。"
            if isinstance(exc, VerificationRequired):
                with self._verification_lock:
                    self._verifications[p["publication_id"]] = {
                        "id": str(uuid.uuid4()), "url": exc.url, "context": context, "expires_at": time.time() + 600}
                    self._challenge_vault(p["publication_id"], self._verifications[p["publication_id"]])
            with self.store.transaction():
                self._attempt(p["publication_id"], phase="rejected", error=str(exc))
                publications.change(self.store, p["publication_id"], status="failed", retain_assets=1, error=message)
            publications.project(self.store, item.task_id)
            raise BusinessError(message) from exc
        except Yt2BiliError as exc:
            self._attempt(p["publication_id"], error=str(exc))
            publications.change(self.store, p["publication_id"], status="submission_unknown", retain_assets=1, error=f"AcFun 创建作品结果待核对：{exc}")
            publications.project(self.store, item.task_id)
            raise UnknownResult("AcFun 创建作品响应不确定；请到稿件管理核对，禁止重发。") from exc
        finally:
            if hasattr(client, "set_verification"): client.set_verification()
        douga_id = str(result.get("dougaId") or "")
        if not re.fullmatch(r"[1-9][0-9]*", douga_id):
            self._attempt(p["publication_id"], error="AcFun 响应缺少有效 AC 号。")
            publications.change(self.store, p["publication_id"], status="submission_unknown", retain_assets=1, error="AcFun 响应缺少有效 AC 号，需核对。")
            raise UnknownResult("AcFun 响应缺少有效 AC 号，需核对。")
        self._attempt(p["publication_id"], phase="submitted", douga_id=douga_id)
        publications.change(self.store, p["publication_id"], status="submitted", remote_id="AC" + douga_id, error="")
        publications.project(self.store, item.task_id)

    def _upload_video(self, client, task, item, publication_id):
        filename = Path(task.video_path).name
        token_data = client.request("POST", "https://member.acfun.cn/video/api/getKSCloudToken", {"fileName": filename, "size": Path(task.video_path).stat().st_size, "template": "1"})
        upload_id, token, config = token_data.get("taskId"), token_data.get("token"), token_data.get("uploadConfig")
        if not upload_id or not token or not isinstance(config, dict): raise Yt2BiliError("AcFun 未返回完整视频上传参数。")
        self._attempt(publication_id, upload_task_id=str(upload_id))
        self._upload_blob(client, task.video_path, token, int(config.get("partSize") or 0), item)
        # A lost createVideo response is unknown media state. Never proceed to createDouga.
        self._attempt(publication_id, phase="creating_video")
        try: video = client.request("POST", "https://member.acfun.cn/video/api/createVideo", {"videoKey": upload_id, "fileName": filename, "vodType": "ksCloud"})
        except UnknownResult as exc:
            publications.change(self.store, publication_id, status="submission_unknown", retain_assets=1, error="AcFun 视频素材创建结果不确定。")
            raise UnknownResult("AcFun 视频素材创建结果不确定，请核对后手动处理。") from exc
        video_id = video.get("videoId")
        if not video_id:
            publications.change(self.store, publication_id, status="submission_unknown", retain_assets=1, error="AcFun 视频素材响应缺少 videoId。")
            raise UnknownResult("AcFun 视频素材响应缺少 videoId，请核对后手动处理。")
        self._attempt(publication_id, video_id=str(video_id), phase="cover")
        client.request("POST", "https://member.acfun.cn/video/api/uploadFinish", {"taskId": upload_id})
        return video_id

    def _upload_cover(self, client, task, item):
        cover = Path(task.cover_path)
        cover_token = client.request("POST", "https://member.acfun.cn/common/api/getQiniuToken", {"fileName": cover.name})
        info = cover_token.get("info")
        token = info.get("token") if isinstance(info, dict) else None
        if not token: raise Yt2BiliError("AcFun 未返回封面上传令牌。")
        self._upload_blob(client, cover, token, min(cover.stat().st_size, 8 * 1024 * 1024), item)
        cover_result = client.request("POST", "https://member.acfun.cn/common/api/getUrlAfterUpload", {"bizFlag": "web-douga-cover", "token": token})
        cover_url = cover_result.get("url")
        if not isinstance(cover_url, str) or urlsplit(cover_url).scheme != "https": raise Yt2BiliError("AcFun 未返回有效封面地址。")
        return cover_url
