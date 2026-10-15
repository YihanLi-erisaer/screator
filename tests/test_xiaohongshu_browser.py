"""Opt-in browser contract tests against an isolated, intercepted creator page.

Run with SCREATOR_BROWSER_TESTS=1. No real account or platform request is used.
"""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from screator.xiaohongshu_browser import BrowserClient
from screator.xiaohongshu import UnknownResult, Rejected

USER = "0123456789abcdef01234567"
NOTE = "abcdef0123456789abcdef01"


class FixtureBrowser(BrowserClient):
    def __init__(self, response, *, widget=False, video=False, cancel=None):
        super().__init__()
        self.response, self.widget = response, widget
        self.video, self.cancel_event = video, cancel
        self.sent = []
        self.uploaded = []

    def _browser(self):
        if self.browser: return self.browser
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(channel="msedge", headless=True)
        return self.browser

    def _new_context(self, cookies=None):
        context = super()._new_context()
        button = '<xhs-publish-btn is-publish="true" submit-disabled="false" style="display:block;width:150px;height:40px;background:red">发布</xhs-publish-btn>' if self.widget else '<button>发布</button>'
        html = '''<html><head><meta charset="utf-8"></head><body><input class="upload-input" type="file" accept="image/*" multiple>
          <div class="img-preview-area"></div><div class="d-input"><input placeholder="填写标题"></div>
          <div role="textbox" contenteditable="true"></div>''' + button + '''
          <script>
          document.querySelector('input[type=file]').onchange = e => {
            for (const file of e.target.files) {
              const preview = document.createElement('div'); preview.className='pr';
              preview.textContent=file.name; document.querySelector('.img-preview-area').appendChild(preview);
            }
          };
          document.querySelector('button,xhs-publish-btn').onclick = () => fetch('/web_api/sns/v2/note', {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
              title:document.querySelector('.d-input input').value,
              description:document.querySelector('[contenteditable]').innerText,
              images:Array.from(document.querySelectorAll('.pr')).map(e => e.textContent)
            })});
          </script></body></html>'''
        if self.video:
            html = '''<html><head><meta charset="utf-8"></head><body>
            <input type="file" accept="image/*" id="decoy">
            <input type="file" accept="video/mp4,video/quicktime" id="video">
            <div id="editor" hidden><input placeholder="填写标题">
            <div role="textbox" contenteditable="true"></div>''' + button + '''</div>
            <script>
            document.querySelector('#decoy').onchange = () => { throw Error('wrong input'); };
            document.querySelector('#video').onchange = () => {
              fetch('/uploaded', {method:'POST', body:JSON.stringify({file:document.querySelector('#video').files[0].name})});
              setTimeout(() => { document.querySelector('#editor').hidden=false; }, 350);
            };
            document.querySelector('button,xhs-publish-btn').onclick = () => fetch('/web_api/sns/v2/note', {
              method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
                title:document.querySelector('[placeholder]').value,
                description:document.querySelector('[contenteditable]').innerText,
                video:document.querySelector('#video').files[0].name
              })});
            </script></body></html>'''
        def route_handler(route):
            if route.request.url.endswith("/uploaded"):
                self.uploaded.append(json.loads(route.request.post_data))
                if self.cancel_event: self.cancel_event.set()
                route.fulfill(status=200, body="{}")
                return
            if route.request.method == "POST":
                self.sent.append(route.request.post_data_json)
                route.fulfill(status=200, content_type="application/json", body=json.dumps(self.response))
            else:
                route.fulfill(status=200, content_type="text/html", body=html)
        context.route("**/*", route_handler)
        return context


@unittest.skipUnless(os.environ.get("SCREATOR_BROWSER_TESTS") == "1", "requires local Chrome/Edge; opt in with SCREATOR_BROWSER_TESTS=1")
class BrowserContractTests(unittest.TestCase):
    def run_publish(self, response, widget=False):
        client = FixtureBrowser(response, widget=widget)
        self.addCleanup(client.close)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        paths = []
        for name in ("first.png", "second.png"):
            path = Path(temporary.name) / name
            Image.new("RGB", (32, 32), "blue").save(path)
            paths.append(path)
        intent = []
        with patch("screator.xiaohongshu_browser.identity", return_value=(USER, "Fixture")):
            result = client.publish([], paths, {"user_id": USER, "title": "笔记标题", "description": "第一段\n第二段 🌸"},
                                    lambda: intent.append(True), threading.Event(), None)
        self.assertEqual(intent, [True])
        self.assertEqual(client.sent, [{"title": "笔记标题", "description": "第一段\n第二段 🌸", "images": ["first.png", "second.png"]}])
        return result

    def test_old_and_new_buttons_upload_order_fill_text_and_capture_note_receipt(self):
        for widget in (False, True):
            with self.subTest(widget=widget):
                self.assertEqual(self.run_publish({"success": True, "data": {"noteId": NOTE}}, widget), NOTE)

    def test_missing_id_is_unknown_and_business_rejection_remains_retryable(self):
        with self.assertRaises(UnknownResult): self.run_publish({"success": True, "data": {}})
        with self.assertRaises(Rejected): self.run_publish({"success": False, "code": -1})

    def video_publish(self, response, *, widget=False, cancel_during_upload=False):
        cancel = threading.Event()
        client = FixtureBrowser(response, widget=widget, video=True, cancel=cancel if cancel_during_upload else None)
        self.addCleanup(client.close)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "prepared.mp4"
        path.write_bytes(b"fixture video")
        intent = []
        with patch("screator.xiaohongshu_browser.identity", return_value=(USER, "Fixture")):
            result = client.publish([], [path], {"user_id": USER, "media_type": "video", "title": "视频标题", "description": ""},
                                    lambda: intent.append(True), cancel, None)
        self.assertEqual(intent, [True])
        self.assertEqual(client.uploaded, [{"file": "prepared.mp4"}])
        self.assertEqual(client.sent, [{"title": "视频标题", "description": "", "video": "prepared.mp4"}])
        return result

    def test_video_waits_for_transcode_and_uses_video_input_for_both_buttons(self):
        for widget in (False, True):
            with self.subTest(widget=widget):
                self.assertEqual(self.video_publish({"code": 0, "data": {"id": NOTE}}, widget=widget), NOTE)

    def test_video_missing_receipt_and_explicit_rejection(self):
        with self.assertRaises(UnknownResult): self.video_publish({"success": True, "data": {}})
        with self.assertRaises(Rejected): self.video_publish({"success": False, "code": -1})

    def test_video_cancel_during_upload_does_not_create_a_note(self):
        from screator.events import Cancelled
        with self.assertRaises(Cancelled):
            self.video_publish({"success": True, "data": {"id": NOTE}}, cancel_during_upload=True)
