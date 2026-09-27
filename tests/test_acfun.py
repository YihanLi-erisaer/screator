import json
import sqlite3
import threading
from contextlib import closing
import uuid
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

import test_desktop as desktop_tests
from test_multi_account import credentials
from yt2bili import publications
from yt2bili.acfun import AcfunService, BusinessError, UnknownResult, VerificationRequired, WebClient, verification_page
from yt2bili.db import TaskStore
from yt2bili.db import Task
from yt2bili.desktop_service import DesktopService
from yt2bili.exceptions import Yt2BiliError


class FakeWeb:
    def __init__(self, fail=""):
        self.calls = []
        self.payloads = {}
        self.fail = fail
        self.proof = None

    def set_verification(self, proof=None):
        self.proof = proof

    def request(self, method, url, data=None, raw=None, code=0, expect_list=False):
        stage = url.split("?", 1)[0].split("/")[-1]
        self.calls.append(stage)
        self.payloads[stage] = data
        if stage == "getAllChannels":
            return [{"channelId": "1", "name": "动画", "channelType": 2, "children": [
                {"channelId": "190", "name": "短片", "channelType": 2}]},
                {"channelId": "70", "name": "科技", "channelType": 2, "children": [
                    {"channelId": "90", "name": "科技制造", "channelType": 2}]},
                {"channelId": "73", "name": "文章", "channelType": 1},
                {"channelId": "178", "name": "禁用", "channelType": 2, "disableContribute": True}]
        if stage == "createDouga" and self.fail == "business":
            raise BusinessError("AcFun createDouga 业务响应失败（代码 400）。分区错误")
        if stage == "createDouga" and self.fail == "verification" and not self.proof:
            raise VerificationRequired(400011, "https://captcha.zt.kuaishou.com/index.html?challenge=private-challenge&url=config")
        if stage == self.fail:
            if stage in ("createVideo", "createDouga"):
                raise UnknownResult("response lost")
            raise Yt2BiliError("upstream failure")
        return {
            "getKSCloudToken": {"result": 0, "taskId": 123, "token": "video-secret", "uploadConfig": {"partSize": 5}},
            "fragment": {"result": 1}, "complete": {"result": 1},
            "createVideo": {"result": 0, "videoId": 456},
            "getQiniuToken": {"result": 0, "info": {"token": "cover-secret"}},
            "getUrlAfterUpload": {"result": 0, "url": "https://member.acfun.cn/cover.jpg"},
            "createDouga": {"result": 0, "dougaId": 789},
        }.get(stage, {"result": 0})


class AcfunTests(unittest.TestCase):
    setUp = desktop_tests.DesktopTests.setUp
    wait_idle = desktop_tests.DesktopTests.wait_idle
    wait_until = desktop_tests.DesktopTests.wait_until
    mocks = desktop_tests.DesktopTests.mocks
    meta = desktop_tests.DesktopTests.meta
    download = desktop_tests.DesktopTests.download
    prepare = desktop_tests.DesktopTests.prepare
    upload = desktop_tests.DesktopTests.upload

    def bind(self):
        self.service.config.values["acfun_experimental_enabled"] = True
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO acfun_accounts(account_id,user_id,nickname) VALUES('ac-test','12345','测试 AcFun')")
        self.service.acfun.client_factory = lambda cookies=None: FakeWeb()
        return self.service.acfun.account()

    def test_qr_login_binds_verified_id_and_keeps_cookies_in_vault(self):
        class QrClient:
            def __init__(self): self.warmed = False
            def warmup_session(self): self.warmed = True
            def request(self, method, url, data=None, raw=None, code=0, timeout=60):
                if "/qr/start" in url:
                    return {"result": 0, "qrLoginToken": "qr-token", "qrLoginSignature": "signature", "imageData": "aW1hZ2U="}
                if "/qr/scanResult" in url: return {"result": 0, "qrLoginSignature": "next"}
                if "/qr/acceptResult" in url: return {"result": 0}
                if "/personalInfo" in url:
                    if not self.warmed: raise Yt2BiliError("会话尚未生效")
                    return {"result": 0, "info": {"userId": 12345, "name": "AcFun 测试"}}
                raise AssertionError(url)
            def cookies(self): return [{"name": "auth_key", "value": "secret", "domain": ".acfun.cn", "path": "/"}]
        self.service.acfun.client_factory = lambda cookies=None: QrClient()
        self.assertEqual(self.service.acfun.start()["status"], "waiting")
        self.assertEqual(self.service.acfun.poll()["status"], "scanned")
        self.assertEqual(self.service.acfun.poll()["status"], "done")
        self.assertEqual(self.service.acfun.account()["user_id"], "12345")
        self.assertEqual(self.service.acfun._credential()[0]["value"], "secret")
        self.assertNotIn("secret", str(self.service.acfun.account()))

    def test_qr_poll_is_single_flight(self):
        entered, release = threading.Event(), threading.Event()
        class SlowQrClient:
            def __init__(self): self.scan_calls = 0
            def request(self, method, url, data=None, raw=None, code=0, timeout=60):
                if "/qr/start" in url:
                    return {"result": 0, "qrLoginToken": "qr-token", "qrLoginSignature": "signature", "imageData": "aW1hZ2U="}
                self.scan_calls += 1
                entered.set()
                self_test.assertEqual(timeout, 15)
                self_test.assertTrue(release.wait(5))
                return {"result": 0, "qrLoginSignature": "next"}
        self_test = self
        client = SlowQrClient()
        self.service.acfun.client_factory = lambda cookies=None: client
        self.service.acfun.start()
        outcome = []
        worker = threading.Thread(target=lambda: outcome.append(self.service.acfun.poll()))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.service.acfun.poll(), {"status": "waiting"})
            self.assertEqual(client.scan_calls, 1)
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(outcome, [{"status": "scanned"}])

    def create(self, video="abcdefghijk"):
        return self.service.create("https://youtu.be/" + video, str(uuid.uuid4()), self.account["account_id"], sync_acfun=True)["task_id"]

    def ready(self, task_id):
        pub = publications.for_platform(self.service.store, task_id, "acfun")
        self.service.update_publication(pub["publication_id"], revision=pub["revision"], title="转载测试", description="已获授权", channel_id=90, tags=["转载"])
        return publications.for_platform(self.service.store, task_id, "acfun")

    def test_short_title_can_be_saved_before_channel_is_selected(self):
        self.bind()
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()):
            task_id = self.create()
            self.wait_idle()
            pub = publications.for_platform(self.service.store, task_id, "acfun")
            with self.service.store.transaction():
                publications.change(self.service.store, pub["publication_id"],
                    snapshot=json.dumps({"title": "很长的标题" * 12, "channel_id": 0, "tags": []}, ensure_ascii=False))
            pub = publications.for_platform(self.service.store, task_id, "acfun")
            saved = self.service.update_publication(pub["publication_id"], revision=pub["revision"],
                title="五字短标题", description="已获授权", channel_id=0, tags=[])
            self.assertEqual(json.loads(saved["snapshot"])["title"], "五字短标题")
            with self.assertRaisesRegex(Yt2BiliError, "请选择 AcFun 分区"):
                self.service.submit(task_id, "submit-missing-channel")

    def test_preview_and_submit_only_once(self):
        self.bind()
        fake = FakeWeb()
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            self.assertEqual(len(self.uploads), 0)
            pub = self.ready(task_id)
            self.service.submit(task_id, "submit-acfun", revisions={p["publication_id"]: p["revision"] for p in publications.items(self.service.store, task_id)})
            self.wait_idle()
        pub = publications.for_platform(self.service.store, task_id, "acfun")
        self.assertEqual((pub["status"], pub["remote_id"]), ("submitted", "AC789"))
        self.assertEqual(fake.calls.count("createDouga"), 1)
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(self.service.task(task_id).status, "submitted")
        attempt = self.service.store._conn.execute("SELECT media_sha256,request_sha256 FROM acfun_attempts").fetchone()
        self.assertEqual((len(attempt[0]), len(attempt[1])), (64, 64))

    def test_auto_uses_own_channel_and_shared_metadata(self):
        self.bind()
        self.service.config.values.update(acfun_channel_id=90, bili_tags="转载,航空")
        fake = FakeWeb()
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], mode="auto", sync_acfun=True)["task_id"]
            self.wait_idle()
        task = self.service.task(task_id)
        acfun = publications.for_platform(self.service.store, task_id, "acfun")
        snapshot = json.loads(acfun["snapshot"])
        self.assertEqual((acfun["status"], acfun["remote_id"]), ("submitted", "AC789"))
        self.assertEqual(snapshot["channel_id"], 90)
        self.assertEqual(snapshot["tags"], ["转载", "航空"])
        self.assertEqual(snapshot["description"], task.desc_zh)
        self.assertLessEqual(len(snapshot["title"]), 50)
        self.assertEqual(fake.calls.count("createDouga"), 1)
        sent = fake.payloads["createDouga"]
        self.assertEqual(sent["title"], snapshot["title"])
        self.assertEqual(sent["description"], task.desc_zh)
        self.assertEqual(sent["creationType"], 1)
        self.assertEqual(sent["channelId"], 90)
        self.assertEqual(json.loads(sent["tagNames"]), ["转载", "航空"])
        self.assertEqual(json.loads(sent["videoInfos"]), [{"videoId": 456, "title": snapshot["title"]}])
        self.assertEqual(sent["originalLinkUrl"], task.url)

    def test_auto_requires_acfun_channel_before_creating_task(self):
        self.bind()
        with patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()):
            with self.assertRaisesRegex(Yt2BiliError, "AcFun 分区 ID"):
                self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], mode="auto", sync_acfun=True)
        self.assertEqual(self.service.store._conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_parent_channel_rejected_before_task_creation_or_upload(self):
        self.bind()
        self.service.config.values["acfun_channel_id"] = 1
        fake = FakeWeb()
        with patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            with self.assertRaisesRegex(Yt2BiliError, "不是可投稿的视频子分区"):
                self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], mode="auto", sync_acfun=True)
        self.assertEqual(fake.calls, ["getAllChannels"])
        self.assertEqual(self.service.store._conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual({x["channel_id"] for x in self.service.acfun.channels()["items"]}, {90, 190})

    def test_existing_invalid_channel_rejected_before_upload(self):
        self.bind()
        fake = FakeWeb()
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            snapshot = json.loads(pub["snapshot"])
            snapshot["channel_id"] = 1
            publications.change(self.service.store, pub["publication_id"], snapshot=json.dumps(snapshot))
            with self.assertRaisesRegex(Yt2BiliError, "不是可投稿的视频子分区"):
                self.service.submit(task_id, "invalid-channel")
        self.assertNotIn("getKSCloudToken", fake.calls)

    def test_business_rejection_keeps_reason_and_allows_edit_without_ac_number(self):
        self.bind()
        fake = FakeWeb("business")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            self.ready(task_id)
            self.service.submit(task_id, "business-rejection")
            self.wait_idle()
        pub = publications.for_platform(self.service.store, task_id, "acfun")
        self.assertEqual(pub["status"], "failed")
        self.assertIn("分区错误", pub["error"])
        attempt = self.service.store._conn.execute("SELECT phase,error FROM acfun_attempts").fetchone()
        self.assertEqual(attempt[0], "rejected")
        self.assertIn("分区错误", attempt[1])
        self.assertTrue(self.service.retry_publication(pub["publication_id"], "edit-rejected")["ready"])

    def test_official_verification_resumes_same_media_without_reupload_or_bilibili_repeat(self):
        self.bind()
        fake = FakeWeb("verification")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "challenge-first")
            self.wait_idle()
            pub = publications.get(self.service.store, pub["publication_id"])
            self.assertEqual(pub["status"], "failed")
            self.assertIn("安全验证", pub["error"])
            self.assertEqual(fake.calls.count("createDouga"), 1)
            self.assertNotIn("private-challenge", json.dumps(publications.items(self.service.store, task_id)))
            self.assertNotIn("private-challenge", str(list(self.service.store._conn.execute("SELECT error FROM acfun_attempts"))))
            challenge = self.service.acfun.verification(pub["publication_id"])
            self.assertIn("/iframe/index.html?", challenge["url"])
            with self.assertRaises(Yt2BiliError):
                self.service.complete_acfun_verification(pub["publication_id"], "wrong", "captcha", "proof")
            self.service.complete_acfun_verification(pub["publication_id"], challenge["challenge_id"], "captcha", "private-proof")
            with self.assertRaises(Yt2BiliError):
                self.service.complete_acfun_verification(pub["publication_id"], challenge["challenge_id"], "captcha", "private-proof")
            self.assertEqual(fake.calls.count("createDouga"), 1)  # Completion alone never publishes.
            self.service.submit(task_id, "challenge-confirm")
            self.wait_idle()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")
        self.assertEqual(fake.calls.count("getKSCloudToken"), 1)
        self.assertEqual(fake.calls.count("getQiniuToken"), 1)
        self.assertEqual(fake.calls.count("createDouga"), 2)
        self.assertEqual(len(self.uploads), 1)
        self.assertIsNone(fake.proof)
        self.assertNotIn("private-proof", str(self.service.acfun._credential()))

    def test_legacy_rejected_upload_reuses_video_but_uploads_missing_cover(self):
        self.bind()
        fake = FakeWeb("business")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "legacy-first")
            self.wait_idle()
            snapshot = json.loads(publications.get(self.service.store, pub["publication_id"])["snapshot"])
            snapshot.pop("_uploaded_media")
            publications.change(self.service.store, pub["publication_id"], snapshot=json.dumps(snapshot))
            fake.fail = ""
            self.service.retry_publication(pub["publication_id"], "legacy-retry")
            self.service.submit(task_id, "legacy-confirm")
            self.wait_idle()
        self.assertEqual(fake.calls.count("getKSCloudToken"), 1)
        self.assertEqual(fake.calls.count("getQiniuToken"), 2)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")

    def test_verification_rejects_changed_account_and_expired_or_changed_metadata(self):
        self.bind()
        fake = FakeWeb("verification")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "stale-challenge")
            self.wait_idle()
        entry = self.service.acfun._verifications[pub["publication_id"]]
        with patch("yt2bili.acfun.time.time", return_value=entry["expires_at"] + 1):
            self.assertEqual(self.service.acfun.verification(pub["publication_id"])["reason"], "expired")
        self.service.acfun._verifications[pub["publication_id"]] = entry
        with self.service.store.transaction() as db:
            db.execute("UPDATE acfun_accounts SET binding_revision=binding_revision+1")
        self.assertEqual(self.service.acfun.verification(pub["publication_id"])["reason"], "context_changed")
        self.service.acfun._verifications[pub["publication_id"]] = entry
        with self.service.store.transaction() as db:
            db.execute("UPDATE acfun_accounts SET binding_revision=binding_revision-1")
        snapshot = json.loads(publications.get(self.service.store, pub["publication_id"])["snapshot"])
        snapshot["title"] = "changed"
        publications.change(self.service.store, pub["publication_id"], snapshot=json.dumps(snapshot))
        self.assertEqual(self.service.acfun.verification(pub["publication_id"])["reason"], "context_changed")

    def test_verification_survives_restart_and_non_submission_snapshot_changes(self):
        self.bind()
        fake = FakeWeb("verification")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "restart-challenge")
            self.wait_idle()
        before = self.service.dispatch("acfun.verification", {"publication_id": pub["publication_id"]})
        snapshot = json.loads(publications.get(self.service.store, pub["publication_id"])["snapshot"])
        snapshot.update(revision=999, metadata_revision=999, nickname="显示名称变化", title_source="manual")
        publications.change(self.service.store, pub["publication_id"], snapshot=json.dumps(snapshot))
        service = AcfunService(self.service.store, self.service.config, lambda *_: None)
        # New process/service has no in-memory challenge, so this exercises vault recovery.
        self.assertEqual(service.verification(pub["publication_id"]), before)
        service.complete_verification(pub["publication_id"], before["challenge_id"], "captcha", "proof")
        self.assertIsNone(service._challenge_vault(pub["publication_id"]))

    def test_missing_entry_can_be_refreshed_once_without_reupload(self):
        self.bind()
        fake = FakeWeb("verification")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "missing-challenge")
            self.wait_idle()
            self.service.acfun._forget_challenge(pub["publication_id"])
            params = {"publication_id": pub["publication_id"]}
            state = self.service.dispatch("acfun.verification", params)
            self.assertEqual((state["status"], state["reason"]), ("refresh_required", "missing"))
            params["operation_id"] = "refresh-missing-once"
            self.assertTrue(self.service.dispatch("acfun.verification.refresh", params)["queued"])
            self.wait_idle()
            self.assertEqual(self.service.dispatch("acfun.verification", {"publication_id": pub["publication_id"]})["status"], "ready")
            self.service.dispatch("acfun.verification.refresh", params)  # IPC replay
            self.wait_idle()
        self.assertEqual(fake.calls.count("getKSCloudToken"), 1)
        self.assertEqual(fake.calls.count("createDouga"), 2)
        self.assertEqual(len(self.uploads), 1)

    def test_refresh_never_reposts_unknown_submission(self):
        self.bind()
        fake = FakeWeb("createDouga")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "unknown-challenge")
            self.wait_idle()
            with self.assertRaises(Yt2BiliError):
                self.service.refresh_acfun_verification(pub["publication_id"], "refresh-unknown")
        self.assertEqual(fake.calls.count("createDouga"), 1)

    def test_changed_media_cannot_reuse_rejected_upload(self):
        self.bind()
        fake = FakeWeb("business")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            pub = self.ready(task_id)
            self.service.submit(task_id, "media-first")
            self.wait_idle()
            Path(self.service.task(task_id).video_path).write_bytes(b"replacement media content")
            fake.fail = ""
            self.service.retry_publication(pub["publication_id"], "media-retry")
            self.service.submit(task_id, "media-confirm")
            self.wait_idle()
        self.assertEqual(fake.calls.count("getKSCloudToken"), 2)
        self.assertEqual(fake.calls.count("getQiniuToken"), 2)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")

    def test_verification_urls_and_one_use_cookies(self):
        # Real createDouga 400011 uses the mobile identity host as well as passport/captcha.
        actual_host = verification_page("https://app.m.kuaishou.com/account/verify?unionToken=fixture&kpn=ACFUN_APP")
        self.assertTrue(actual_host.startswith("https://app.m.kuaishou.com/"))
        self.assertEqual(parse_qs(actual_host.split("?", 1)[1])["unionToken"], ["fixture"])
        self.assertEqual(verification_page("https://app.m.kuaishou.com.evil.example/account/verify"), "")
        for url in ("http://passport.kuaishou.com/", "https://passport.kuaishou.com.evil.com/",
                    "https://user@passport.kuaishou.com/", "https://passport.kuaishou.com:444/",
                    "https://evil.com/", "https://passport.kuaishou.com/#fragment", None):
            self.assertEqual(verification_page(url), "")
        page = verification_page("https://passport.kuaishou.com/identity?needQrcode=1&needQrType=identity-verify-auto")
        self.assertTrue(page.startswith("https://passport.kuaishou.com/pc/identity/qrcode?"))
        self.assertEqual(parse_qs(page.split("?", 1)[1])["kpn"], ["ACFUN_APP"])
        client = WebClient()
        client.set_verification(("captcha", "proof"))
        self.assertEqual({x["name"] for x in client.cookies()}, {"ztIdentityVerificationType", "ztIdentityVerificationCheckToken"})
        for cookie in client.jar:
            self.assertTrue(cookie.secure)
            self.assertEqual(cookie.domain, ".acfun.cn")
        client.set_verification()
        self.assertEqual(client.cookies(), [])

    def test_bilibili_description_edit_updates_acfun_without_overwriting_title(self):
        self.bind()
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()):
            task_id = self.create()
            self.wait_idle()
            self.ready(task_id)
            self.service.update_metadata(task_id, "Bilibili 编辑标题", "两平台共用的新简介")
        task = self.service.task(task_id)
        acfun = json.loads(publications.for_platform(self.service.store, task_id, "acfun")["snapshot"])
        self.assertEqual(acfun["title"], "转载测试")
        self.assertEqual(acfun["description"], task.desc_zh)
        self.assertIn("两平台共用的新简介", acfun["description"])

    def test_fragment_failure_never_creates_work(self):
        self.bind()
        fake = FakeWeb("fragment")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            self.ready(task_id)
            self.service.submit(task_id, "submit-failure")
            self.wait_idle()
        self.assertNotIn("createDouga", fake.calls)
        self.assertEqual(publications.for_platform(self.service.store, task_id, "acfun")["status"], "failed")

    def test_three_targets_share_preparation_and_finish_independently(self):
        self.bind()
        fake = FakeWeb()
        def broker(method, path, data=None, file=None):
            if path == "/v1/account":
                return {"account": {"client_key": "client", "open_id": "dy-user", "nickname": "抖音"}, "capabilities": {"auto_publish": False}}
            if path.endswith("/submit"): return {"status": "submitted", "remote_id": "dy-receipt"}
            return {"status": "ready"}
        with self.mocks(), patch.object(self.service.douyin, "request", side_effect=broker), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], sync_douyin=True, sync_acfun=True)["task_id"]
            self.wait_idle()
            self.ready(task_id)
            self.service.submit(task_id, "submit-three")
            self.wait_idle()
        pubs = publications.items(self.service.store, task_id)
        self.assertEqual({p["platform"]: p["status"] for p in pubs}, {"bilibili": "submitted", "douyin": "submitted", "acfun": "submitted"})
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(fake.calls.count("createDouga"), 1)

    def test_lost_create_response_requires_manual_resolution(self):
        self.bind()
        fake = FakeWeb("createDouga")
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()), patch.object(self.service.acfun, "_client", return_value=fake):
            task_id = self.create()
            self.wait_idle()
            self.ready(task_id)
            self.service.submit(task_id, "submit-unknown")
            self.wait_idle()
        pub = publications.for_platform(self.service.store, task_id, "acfun")
        self.assertEqual(pub["status"], "submission_unknown")
        self.assertEqual(pub["retain_assets"], 1)
        self.assertEqual(fake.calls.count("createDouga"), 1)
        with self.assertRaises(Yt2BiliError): self.service.retry_publication(pub["publication_id"], "retry-unknown")
        self.service.resolve_publication(pub["publication_id"], not_submitted=True)
        self.assertTrue(self.service.retry_publication(pub["publication_id"], "retry-after-check")["ready"])

    def test_same_source_acfun_account_is_unique(self):
        self.bind()
        with patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()):
            self.create()
            other = self.service.accounts.bind(credentials(456), verify=False)
            # A second Bilibili account is distinct but the AcFun target is not.
            with self.assertRaises(Yt2BiliError):
                self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), other["account_id"], sync_acfun=True)

    def test_reject_html_and_unapproved_cookie_domain(self):
        self.assertEqual(WebClient([{"name": "secret", "value": "x", "domain": ".evil.com"}]).cookies(), [])
        with self.assertRaises(Yt2BiliError):
            WebClient().request("GET", "https://evil.example/test")
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return self.body
        client = WebClient()
        with patch.object(client.opener, "open", return_value=Response(b"<html>login</html>")):
            with self.assertRaises(UnknownResult): client.request("POST", "https://member.acfun.cn/video/api/createDouga", {})
        with patch.object(client.opener, "open", return_value=Response(b'{"result":10}')):
            with self.assertRaisesRegex(Yt2BiliError, "createDouga 业务响应失败（代码 10）"):
                client.request("POST", "https://member.acfun.cn/video/api/createDouga", {})

    def test_web_contract_parses_channel_arrays_and_nested_rejections(self):
        class Response:
            status = 200
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        client = WebClient([{"name": "auth_key", "value": "private-cookie", "domain": ".acfun.cn"}])
        tree = [{"channelId": "1", "name": "动画", "channelType": 2}]
        with patch.object(client.opener, "open", return_value=Response(tree)):
            self.assertEqual(client.request("POST", "https://member.acfun.cn/common/api/getAllChannels", {}, expect_list=True), tree)
        rejected = {"isError": True, "errMsg": {"result": 400, "error_msg": "请选择子分区 private-cookie"}}
        with patch.object(client.opener, "open", return_value=Response(rejected)):
            with self.assertRaises(BusinessError) as caught:
                client.request("POST", "https://member.acfun.cn/video/api/createDouga", {})
            self.assertIn("代码 400", str(caught.exception))
            self.assertIn("请选择子分区", str(caught.exception))
            self.assertNotIn("private-cookie", str(caught.exception))
        for code in (400001, 400011, 410999):
            for payload in ({"result": code, "error_url": "https://passport.kuaishou.com/identity?secret=hidden"},
                            {"isError": True, "errMsg": {"result": code, "error_url": "https://evil.com/secret"}}):
                with patch.object(client.opener, "open", return_value=Response(payload)):
                    with self.assertRaises(VerificationRequired) as caught:
                        client.request("POST", "https://member.acfun.cn/video/api/createDouga", {})
                    self.assertNotIn("secret", str(caught.exception))
                    self.assertNotIn("hidden", str(caught.exception))
        with patch.object(client.opener, "open", return_value=Response({"unexpected": True})):
            with self.assertRaises(UnknownResult):
                client.request("POST", "https://member.acfun.cn/video/api/createDouga", {})

    def test_submission_form_transmits_metadata_as_utf8(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_): return b'{"result":0,"dougaId":789}'
        client = WebClient()
        fields = {"title": "建筑师设计现代农舍", "description": "简介 & 作者=测试\n原链接：https://youtube.com/watch?v=abcdefghijk",
                  "creationType": 1, "channelId": 196, "tagNames": json.dumps(["纪录", "生活"], ensure_ascii=False)}
        with patch.object(client.opener, "open", return_value=Response()) as sent:
            client.request("POST", "https://member.acfun.cn/video/api/createDouga", fields)
        request = sent.call_args.args[0]
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(parse_qs(request.data.decode("utf-8")), {k: [str(v)] for k, v in fields.items()})

    def test_v4_upgrade_preserves_douyin_receipt_and_creates_backup(self):
        path = Path(self.tmp.name) / "legacy-v4.sqlite"
        old = TaskStore(path)
        task = Task("abcdefghijk", "https://youtu.be/abcdefghijk", "submitted", task_id=str(uuid.uuid4()))
        old.upsert(task)
        publications.ensure_bili(old, task)
        with old.transaction() as db:
            db.execute("INSERT INTO douyin_accounts(account_id,client_key,open_id,nickname) VALUES('dy','client','user','抖音')")
            db.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id,status,remote_id) VALUES(?,?,'douyin','dy',?,'submitted','dy-receipt')", (str(uuid.uuid4()), task.task_id, task.video_id))
        rows = [tuple(r) for r in old._conn.execute("SELECT * FROM task_publications")]
        old.close()
        with closing(sqlite3.connect(path)) as db:
            db.execute("DROP TABLE acfun_attempts")
            db.execute("DROP TABLE acfun_accounts")
            db.execute("DROP TABLE task_publications")
            db.execute("""CREATE TABLE task_publications(
                publication_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                platform TEXT NOT NULL CHECK(platform IN ('bilibili','douyin')), account_id TEXT,
                source_video_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_assets',
                revision INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
                remote_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                retain_assets INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}',
                UNIQUE(task_id,platform))""")
            db.executemany("INSERT INTO task_publications VALUES(" + ",".join("?" for _ in range(12)) + ")", rows)
            db.execute("PRAGMA user_version=4")
            db.commit()
        migrated = TaskStore(path)
        try:
            self.assertEqual(migrated._conn.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual([tuple(r) for r in migrated._conn.execute("SELECT * FROM task_publications")], rows)
            self.assertTrue(Path(str(path) + ".pre-v5.bak").is_file())
            self.assertEqual(migrated._conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally: migrated.close()

    def test_restart_during_video_creation_requires_review(self):
        self.bind()
        with self.mocks(), patch.object(self.service.acfun, "check", return_value=self.service.acfun.account()):
            task_id = self.create()
            self.wait_idle()
        pub = publications.for_platform(self.service.store, task_id, "acfun")
        with self.service.store.transaction() as db:
            publications.change(self.service.store, pub["publication_id"], status="uploading_media")
            db.execute("INSERT INTO acfun_attempts(attempt_id,publication_id,phase,started_at) VALUES(?,?,'creating_video','2026-09-26T00:00:00Z')",
                       (str(uuid.uuid4()), pub["publication_id"]))
        self.service.close()
        reopened = DesktopService(self.paths, lambda *_: None, desktop_tests.MemoryVault())
        self.addCleanup(reopened.close)
        state = publications.for_platform(reopened.store, task_id, "acfun")
        self.assertEqual(state["status"], "submission_unknown")
        self.assertEqual(reopened.task(task_id).status, "submission_unknown")
