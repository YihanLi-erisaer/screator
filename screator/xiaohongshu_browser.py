"""Visible Chrome/Edge adapter; no private API signing or captcha bypass.

Playwright runs on one dedicated thread because its sync objects are thread
affine. Each publishing context is fresh and receives only vaulted XHS cookies.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests

from screator import events
from screator.exceptions import AppError
from screator.xiaohongshu import AuthRequired, RateLimited, Rejected, UnknownResult, NOTE_ID

CREATOR = "https://creator.xiaohongshu.com"
PUBLISH_URL = CREATOR + "/publish/publish?from=menu&target=image"
VIDEO_PUBLISH_URL = CREATOR + "/publish/publish?from=menu&target=video"
IDENTITY_URL = CREATOR + "/api/galaxy/user/info"
CREATE_PATHS = {"/web_api/sns/v2/note", "/api/sns/web/v1/note/publish", "/api/galaxy/creator/note/publish"}
CREATE_HOSTS = {"creator.xiaohongshu.com", "edith.xiaohongshu.com"}


def identity(cookies):
    if not isinstance(cookies, list) or not cookies:
        raise AuthRequired("小红书尚未登录，请在浏览器完成登录。")
    try:
        with requests.Session() as session:
            for cookie in cookies:
                if cookie.get("domain", "").lstrip(".") in {"xiaohongshu.com", "creator.xiaohongshu.com"}:
                    session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie.get("path", "/"))
            response = session.get(IDENTITY_URL, headers={"User-Agent": "Mozilla/5.0", "Referer": CREATOR + "/"}, timeout=(10, 15), allow_redirects=False)
            if response.status_code in (301, 302, 401, 403): raise AuthRequired("小红书登录已失效，请重新登录。")
            if response.status_code == 429: raise RateLimited("小红书限流，请稍后恢复队列。")
            response.raise_for_status()
            body = response.json()
    except (requests.RequestException, ValueError):
        raise AppError("无法检查小红书登录状态，请检查网络后重试。") from None
    if not isinstance(body, dict): raise AppError("小红书账号响应格式异常。")
    if body.get("success") is False or body.get("code") in (-100, -101, 401):
        raise AuthRequired("小红书登录已失效，请重新登录。")
    if body.get("success") is not True and not (type(body.get("code")) is int and body["code"] == 0):
        raise AppError("小红书账号响应未确认登录状态。")
    data = body.get("data")
    if not isinstance(data, dict): raise AppError("小红书未返回可验证的账号信息。")
    user_id, nickname = data.get("userId"), data.get("userName")
    if not isinstance(user_id, str) or not NOTE_ID.fullmatch(user_id):
        raise AppError("小红书未返回有效账号 ID。")
    if not isinstance(nickname, str) or not nickname.strip() or len(nickname) > 200:
        raise AppError("小红书未返回有效账号昵称。")
    return user_id, nickname


def receipt(body):
    """Only an explicit business rejection or valid ID establishes an outcome."""
    if not isinstance(body, dict): raise UnknownResult("小红书发布响应格式异常，请核对创作中心。")
    code = body.get("code")
    if body.get("success") is False or (type(code) is int and code != 0):
        # No remote message, URL or token is copied into a task or log.
        raise Rejected("小红书明确拒绝发布，请在创作中心检查内容、登录或安全验证。")
    if body.get("success") is not True and not (type(code) is int and code == 0):
        raise UnknownResult("小红书未确认发布结果，请核对创作中心。")
    data = body.get("data")
    note_id = data.get("id") or data.get("noteId") or data.get("note_id") if isinstance(data, dict) else None
    if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
        raise UnknownResult("小红书未返回笔记 ID，请核对创作中心。")
    return note_id


class BrowserClient:
    def __init__(self):
        self.executor = None
        self.lock = threading.Lock()
        self.playwright = self.browser = self.login_context = self.login_page = None
        self.login_started = 0

    def _call(self, work, *args):
        with self.lock:
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xiaohongshu-browser")
            future = self.executor.submit(work, *args)
        return future.result()

    def _browser(self):
        if self.browser and self.browser.is_connected(): return self.browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise AppError("此版本未包含小红书浏览器组件，请安装新版应用。开发环境请安装 requirements-desktop.txt。") from None
        if self.playwright is None: self.playwright = sync_playwright().start()
        for channel in ("chrome", "msedge"):
            try:
                self.browser = self.playwright.chromium.launch(channel=channel, headless=False)
                return self.browser
            except Exception:
                pass
        raise AppError("小红书发布需要本机安装 Google Chrome 或 Microsoft Edge，请安装后重试。")

    def _new_context(self, cookies=None):
        context = self._browser().new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(15000)
        if cookies:
            allowed = [cookie for cookie in cookies if cookie.get("domain", "").lstrip(".") in {"xiaohongshu.com", "creator.xiaohongshu.com", "www.xiaohongshu.com", "edith.xiaohongshu.com"}]
            context.add_cookies(allowed)
        return context

    def start(self): return self._call(self._start)

    def _start(self):
        self._cancel()
        try:
            self.login_context = self._new_context()
            self.login_page = self.login_context.new_page()
            self.login_page.goto(CREATOR + "/", wait_until="domcontentloaded", timeout=45000)
            self.login_started = time.monotonic()
        except AppError:
            self._cancel()
            raise
        except Exception:
            self._cancel()
            raise AppError("无法打开小红书登录页面，请检查浏览器和网络。") from None

    def poll(self): return self._call(self._poll)

    def _poll(self):
        if not self.login_context: return {"status": "idle"}
        if time.monotonic() - self.login_started > 300:
            self._cancel()
            return {"status": "expired"}
        try:
            if self.login_page.is_closed():
                self._cancel()
                return {"status": "cancelled"}
            cookies = [cookie for cookie in self.login_context.cookies()
                       if cookie.get("domain", "").lstrip(".") in {"xiaohongshu.com", "creator.xiaohongshu.com", "www.xiaohongshu.com", "edith.xiaohongshu.com"}]
            try: identity(cookies)
            except AuthRequired: return {"status": "waiting"}
            self._cancel()
            return {"status": "done", "cookies": cookies}
        except AppError: raise
        except Exception:
            self._cancel()
            raise AppError("小红书登录窗口已关闭，请重新打开。") from None

    def cancel(self): return self._call(self._cancel) if self.executor else None

    def _cancel(self):
        if self.login_context:
            try: self.login_context.close()
            except Exception: pass
        self.login_context = self.login_page = None

    def identity(self, cookies): return identity(cookies)

    def publish(self, cookies, paths, snapshot, creating, cancel, progress):
        return self._call(self._publish, cookies, paths, snapshot, creating, cancel, progress)

    @staticmethod
    def _check(cancel):
        if cancel.is_set(): raise events.Cancelled("小红书任务已取消，素材已保留。")

    def _wait(self, page, predicate, cancel, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check(cancel)
            if predicate(): return
            page.wait_for_timeout(250)
        raise AppError("小红书页面未完成准备，请检查素材、网络或网页安全验证后继续此目标。")

    def _publish(self, cookies, paths, snapshot, creating, cancel, progress):
        context, create_started = None, False
        try:
            self._check(cancel)
            video = snapshot.get("media_type") == "video"
            if video and len(paths) != 1: raise AppError("小红书视频任务必须包含一个视频文件。")
            context = self._new_context(cookies)
            page = context.new_page()
            page.goto(VIDEO_PUBLISH_URL if video else PUBLISH_URL, wait_until="domcontentloaded", timeout=45000)
            if urlsplit(page.url).hostname != "creator.xiaohongshu.com" or "/login" in urlsplit(page.url).path:
                raise AuthRequired("小红书登录已失效，请重新登录。")
            # Read back the actual browser session before preparing any submission.
            user_id, _ = identity(context.cookies())
            if user_id != snapshot["user_id"]: raise AuthRequired("小红书浏览器账号与投稿目标不一致。")
            tab = page.get_by_text("上传视频" if video else "上传图文", exact=True)
            if tab.count() and tab.first.is_visible(): tab.first.click()
            title = page.locator('input[placeholder*="标题"], div.d-input input').first
            content = page.locator('div[role="textbox"][contenteditable="true"], div.tiptap[contenteditable="true"], div.ql-editor[contenteditable="true"]').first
            if video:
                # Never choose an image/cover input when the video input is absent.
                upload = page.locator('input[type="file"][accept*="video"], input[type="file"][accept*=".mp4"], input[type="file"][accept*=".mov"]').first
                self._wait(page, lambda: upload.count() > 0, cancel, 20)
                video_deadline = time.monotonic() + 1200
                upload.set_input_files(str(paths[0]))
                # Upload and transcode can take minutes. Poll so cancellation works.
                self._wait(page, lambda: title.is_visible() and content.is_visible(), cancel, max(0, video_deadline - time.monotonic()))
            else:
                upload = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*=".jpg"], input[type="file"][accept*=".png"], input.upload-input').first
                self._wait(page, lambda: upload.count() > 0, cancel, 20)
                for index, path in enumerate(paths):
                    self._check(cancel)
                    upload.set_input_files(str(path))
                    self._wait(page, lambda: page.locator(".img-preview-area .pr").count() == index + 1, cancel)
                    if progress: progress(percent=(index + 1) / (len(paths) + 1) * 100)
                    upload = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*=".jpg"], input[type="file"][accept*=".png"]').first
                if page.locator(".img-preview-area .pr").count() != len(paths):
                    raise AppError("小红书图片数量与任务不一致，已停止发布。")
            title.fill(snapshot["title"])
            content.fill(snapshot["description"])
            title.click()
            if title.input_value() != snapshot["title"] or content.inner_text().strip() != snapshot["description"].strip():
                raise AppError("小红书页面中的标题或正文与任务不一致，请检查内容后继续。")
            guide = page.locator(".feature-guide__btn")
            if guide.count() and guide.first.is_visible(): guide.first.click()
            button = page.locator('xhs-publish-btn:not([is-publish="false"])').first
            if not button.count(): button = page.get_by_role("button", name="发布", exact=True)
            def enabled():
                return button.count() == 1 and button.is_visible() and button.is_enabled() and button.get_attribute("submit-disabled") != "true"
            self._wait(page, enabled, cancel, max(0, video_deadline - time.monotonic()) if video else 20)
            self._check(cancel)
            # Register a response observer BEFORE persisting intent or clicking.
            # A navigation/success toast alone is never treated as a receipt.
            def is_create(response):
                parsed = urlsplit(response.url)
                return (parsed.scheme == "https" and parsed.hostname in CREATE_HOSTS and parsed.path in CREATE_PATHS
                        and response.request.method == "POST")
            with page.expect_response(is_create, timeout=60000) as result:
                creating()
                create_started = True
                button.click()
            response = result.value
            # HTTP failures and malformed responses after click are uncertain.
            if response.status >= 500 or response.status in (401, 403, 429):
                raise UnknownResult("小红书发布响应不确定，请核对创作中心。")
            return receipt(response.json())
        except (AppError, events.Cancelled): raise
        except Exception:
            raise (UnknownResult("小红书发布结果待核对，请检查创作中心。") if create_started
                   else AppError("小红书网页操作未完成，请检查网络、登录或网页安全验证后继续此目标。")) from None
        finally:
            if context:
                try: context.close()
                except Exception: pass

    def close(self):
        if not self.executor: return
        self._call(self._close)
        self.executor.shutdown(wait=True)
        self.executor = None

    def _close(self):
        self._cancel()
        if self.browser:
            try: self.browser.close()
            except Exception: pass
        if self.playwright:
            try: self.playwright.stop()
            except Exception: pass
        self.browser = self.playwright = None
