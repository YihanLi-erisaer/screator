import json
import sqlite3
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_desktop as desktop_tests
from screator import publications
from screator.db import SCHEMA_VERSION, TaskStore
from screator.exceptions import AppError
from screator.social_upload import AuthRequired, RateLimited, SocialClient, UnknownResult


class FakeSocial:
    def __init__(self):
        self.calls = []
        self.fail = ""
        self.subject = "12345"
        self.page_published = False
        self.status = "PUBLISH_COMPLETE"
        self.creator = {"creator_nickname": "TikTok 测试创作者", "creator_username": "creator",
            "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"], "comment_disabled": True,
            "duet_disabled": False, "stitch_disabled": False, "max_video_post_duration_sec": 300}
        self.size = 0

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/users/me"): return {"data": {"id": self.subject, "name": "X 测试账号"}}
        if "/user/info/" in url: return {"data": {"user": {"open_id": "open-" + self.subject, "display_name": "TikTok 测试创作者"}}}
        if "/creator_info/" in url: return {"data": dict(self.creator)}
        if "/me?" in url: return {"id": self.subject, "name": "测试公共主页", "category": "Video Creator"}
        if "/videos" in url:
            phase = kwargs["form"]["upload_phase"]
            if self.fail == phase: raise UnknownResult("response lost")
            if phase == "start":
                self.size = int(kwargs["form"]["file_size"])
                return {"upload_session_id": "100", "video_id": "8888", "start_offset": "0", "end_offset": str(self.size)}
            if phase == "transfer": return {"start_offset": str(self.size), "end_offset": str(self.size)}
            self.page_published = True
            return {"success": True}
        if "fields=id,published" in url: return {"id": "8888", "published": self.page_published}
        if "/initialize" in url: return {"data": {"id": "1111"}}
        if "/append" in url: return {}
        if "/finalize" in url: return {"data": {"id": "1111", "processing_info": {"state": "succeeded"}}}
        if url.endswith("/tweets"):
            if self.fail == "post": raise UnknownResult("create response lost")
            return {"data": {"id": "9999"}}
        if "/video/init/" in url:
            return {"data": {"publish_id": "v_pub_file~v2-1.123456", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_token=private-upload-token"}}
        if method == "PUT":
            if self.fail == "upload": raise UnknownResult("final upload response lost")
            return {}
        if "/status/fetch/" in url:
            if self.fail == "status_quota": raise RateLimited("status query quota")
            return {"data": {"status": self.status, "publicaly_available_post_id": []}}
        raise AssertionError(url)


class SocialTests(unittest.TestCase):
    setUp = desktop_tests.DesktopTests.setUp
    wait_idle = desktop_tests.DesktopTests.wait_idle
    wait_until = desktop_tests.DesktopTests.wait_until
    mocks = desktop_tests.DesktopTests.mocks
    meta = desktop_tests.DesktopTests.meta
    download = desktop_tests.DesktopTests.download
    prepare = desktop_tests.DesktopTests.prepare
    upload = desktop_tests.DesktopTests.upload

    def bind(self, platform):
        fake = FakeSocial()
        self.service.social[platform].client_factory = lambda: fake
        result = self.service.bind_social(platform, "private-access-token", "12345")
        return fake, result["account"]

    def ready(self, platform, auto=False):
        fake, account = self.bind(platform)
        with self.mocks(), self.media_mock():
            task = self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], mode="auto" if auto else "preview",
                sync_social=[{"platform": platform, "account_id": account["account_id"], "binding_revision": account["binding_revision"]}])
            self.wait_idle()
        self.task_id = task["task_id"]
        return fake, publications.for_platform(self.service.store, self.task_id, platform)

    def media_mock(self):
        return patch("screator.social_upload.media.probe_brief", return_value={"vcodec": "h264", "acodec": "aac", "pix_fmt": "yuv420p", "duration": 10})

    def save_tiktok(self, pub, **extra):
        metadata = {"title": "我的原创视频", "privacy_level": "SELF_ONLY", "allow_comment": False, "allow_duet": False, "allow_stitch": False,
            "brand_organic": False, "brand_content": False, "is_aigc": False, "consent": True, "original_content": True, "cover_timestamp_ms": 0, **extra}
        return self.service.update_publication(pub["publication_id"], revision=pub["revision"], social_metadata=metadata)

    def submit(self, targets=None):
        with self.mocks(), self.media_mock():
            self.service.submit(self.task_id, str(uuid.uuid4()), targets=targets)
            self.wait_idle()

    def test_x_auto_publishes_only_after_media_processing(self):
        fake, pub = self.ready("twitter", auto=True)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["remote_id"], "9999")
        calls = [url for _, url, _ in fake.calls]
        self.assertLess(next(i for i, u in enumerate(calls) if u.endswith("/finalize")), next(i for i, u in enumerate(calls) if u.endswith("/tweets")))
        self.assertEqual(len(self.uploads), 1)

    def test_facebook_page_upload_has_separate_video_receipt(self):
        fake, pub = self.ready("facebook")
        self.service.update_publication(pub["publication_id"], revision=pub["revision"], social_metadata={"title": "公共主页独立标题"})
        self.submit()
        outcome = publications.get(self.service.store, pub["publication_id"])
        self.assertEqual(outcome["status"], "submitted")
        self.assertEqual(outcome["remote_id"], "8888")
        phases = [kwargs["form"] for _, url, kwargs in fake.calls if "/videos" in url]
        self.assertEqual([value["upload_phase"] for value in phases], ["start", "transfer", "finish"])
        self.assertEqual(phases[-1]["title"], "公共主页独立标题")
        self.assertEqual(phases[-1]["description"], "简介")

    def test_tiktok_requires_preview_privacy_and_consent_before_upload(self):
        fake, pub = self.ready("tiktok")
        self.assertEqual(json.loads(pub["snapshot"])["privacy_level"], "")
        with self.mocks(), self.media_mock(), self.assertRaises(AppError): self.service.submit(self.task_id, str(uuid.uuid4()))
        self.assertFalse(any("/video/init/" in url for _, url, _ in fake.calls))
        pub = self.save_tiktok(pub, consent=False)
        with self.mocks(), self.media_mock(), self.assertRaises(AppError): self.service.submit(self.task_id, str(uuid.uuid4()))
        self.save_tiktok(pub)
        self.submit()
        outcome = publications.get(self.service.store, pub["publication_id"])
        self.assertEqual(outcome["status"], "submitted")
        self.assertEqual(outcome["remote_id"], "v_pub_file~v2-1.123456")
        request = next(kwargs["data"] for _, url, kwargs in fake.calls if "/video/init/" in url)
        self.assertEqual(request["post_info"]["privacy_level"], "SELF_ONLY")
        self.assertTrue(request["post_info"]["disable_comment"])
        self.assertEqual(request["source_info"]["total_chunk_count"], 1)

    def test_tiktok_auto_is_rejected_without_creating_a_task(self):
        self.bind("tiktok")
        with self.assertRaises(AppError):
            self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], mode="auto", sync_social=[{"platform": "tiktok"}])
        self.assertEqual(self.service.store.list_all(), [])

    def test_live_tiktok_permissions_and_duration_are_enforced(self):
        fake, pub = self.ready("tiktok")
        pub = self.save_tiktok(pub, allow_comment=True)
        with self.media_mock(), self.assertRaises(AppError): self.service.submit(self.task_id, str(uuid.uuid4()))
        pub = self.save_tiktok(pub)
        fake.creator["max_video_post_duration_sec"] = 5
        with self.media_mock(), self.assertRaises(AppError): self.service.submit(self.task_id, str(uuid.uuid4()))
        self.assertFalse(any("/video/init/" in url for _, url, _ in fake.calls))

    def test_tiktok_paid_partnership_requires_disclosure_and_non_private_audience(self):
        fake, pub = self.ready("tiktok")
        with self.assertRaises(AppError): self.save_tiktok(pub, content_disclosure=True)
        with self.assertRaises(AppError): self.save_tiktok(pub, content_disclosure=True, brand_content=True)
        self.save_tiktok(pub, content_disclosure=True, brand_content=True, privacy_level="PUBLIC_TO_EVERYONE")
        self.submit()
        body = next(kwargs["data"] for _, url, kwargs in fake.calls if "/video/init/" in url)
        self.assertTrue(body["post_info"]["brand_content_toggle"])
        self.assertEqual(body["post_info"]["privacy_level"], "PUBLIC_TO_EVERYONE")

    def test_facebook_transfer_failure_retries_only_unfinished_platform(self):
        fake, pub = self.ready("facebook")
        fake.fail = "transfer"
        self.submit()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "failed")
        self.assertEqual(len(self.uploads), 1)
        self.assertTrue(Path(self.service.store.require(self.task_id).video_path).is_file())
        fake.fail = ""
        self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))
        self.submit([pub["publication_id"]])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")

    def test_x_lost_create_response_is_unknown_and_never_reposted(self):
        fake, pub = self.ready("twitter")
        fake.fail = "post"
        self.submit()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submission_unknown")
        with self.assertRaises(AppError): self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))
        self.service.resolve_publication(pub["publication_id"], remote_id="9999")
        self.assertEqual(sum(url.endswith("/tweets") for _, url, _ in fake.calls), 1)

    def test_facebook_lost_finish_can_be_checked_using_existing_video(self):
        fake, pub = self.ready("facebook")
        fake.fail = "finish"
        self.submit()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submission_unknown")
        fake.page_published = True
        result = self.service.check_social_publication(pub["publication_id"])
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["remote_id"], "8888")

    def test_tiktok_final_upload_lost_response_uses_publish_id_for_check(self):
        fake, pub = self.ready("tiktok")
        self.save_tiktok(pub)
        fake.fail = "upload"
        self.submit()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submission_unknown")
        fake.fail = ""
        self.assertEqual(self.service.check_social_publication(pub["publication_id"])["status"], "submitted")
        self.assertEqual(sum("/video/init/" in url for _, url, _ in fake.calls), 1)

    def test_tiktok_status_quota_failure_does_not_make_upload_retryable(self):
        fake, pub = self.ready("tiktok")
        self.save_tiktok(pub)
        fake.fail = "status_quota"
        self.submit()
        outcome = publications.get(self.service.store, pub["publication_id"])
        self.assertEqual(outcome["status"], "submission_unknown")
        self.assertEqual(self.service.social["tiktok"].attempt(pub["publication_id"])["phase"], "unknown")
        with self.assertRaises(AppError): self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))

    def test_tiktok_definite_failure_allows_manual_retry(self):
        fake, pub = self.ready("tiktok")
        self.save_tiktok(pub)
        fake.status = "FAILED"
        self.submit()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "failed")
        self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))
        fake.status = "PUBLISH_COMPLETE"
        self.submit([pub["publication_id"]])
        self.assertEqual(len(self.uploads), 1)

    def test_tiktok_chunk_trailing_bytes_merge_into_final_chunk(self):
        fake, pub = self.ready("tiktok")
        self.save_tiktok(pub)
        Path(self.service.store.require(self.task_id).video_path).write_bytes(b"v" * 20_000_123)
        self.submit()
        request = next(kwargs["data"] for _, url, kwargs in fake.calls if "/video/init/" in url)
        self.assertEqual(request["source_info"]["total_chunk_count"], 2)
        chunks = [kwargs for method, _, kwargs in fake.calls if method == "PUT"]
        self.assertEqual([len(kwargs["raw"]) for kwargs in chunks], [10_000_000, 10_000_123])
        self.assertTrue(all("token" not in kwargs for kwargs in chunks))

    def test_credentials_and_private_upload_urls_do_not_enter_task_exports(self):
        fake, pub = self.ready("tiktok")
        self.save_tiktok(pub)
        self.submit()
        contents = json.dumps(self.service.get_task(self.task_id))
        path = self.paths.root / "history.json"
        self.service.export_history(str(path))
        for secret in ("private-access-token", "private-upload-token"):
            self.assertNotIn(secret, contents)
            self.assertNotIn(secret, path.read_text())

    def test_account_rebinding_and_archive_protect_original_identity(self):
        fake, pub = self.ready("facebook")
        fake.subject = "66666"
        with self.assertRaises(AppError): self.service.bind_social("facebook", "replacement-token", "66666")
        self.assertEqual(self.service.social["facebook"].account()["subject_id"], "12345")
        with self.assertRaises(AppError): self.service.archive_social("facebook")

    def test_social_targets_validate_duplicates_and_binding_revision(self):
        self.bind("twitter")
        for targets in ([{"platform": "other"}], [{"platform": "twitter"}, {"platform": "twitter"}], [{"platform": "twitter", "binding_revision": 999}]):
            with self.assertRaises(AppError): self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], sync_social=targets)
        self.assertEqual(self.service.store.list_all(), [])

    def test_recovery_keeps_social_create_intent_unknown(self):
        _, pub = self.ready("twitter")
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO social_upload_attempts(publication_id,phase,media_sha256,request_sha256,updated_at) VALUES(?,'creating','hash','request','now')", (pub["publication_id"],))
            publications.change(self.service.store, pub["publication_id"], status="creating")
            publications.project(self.service.store, self.task_id)
        self.service.scheduler._recover()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submission_unknown")

    def test_v9_migration_preserves_youtube_and_acfun_attempt_references(self):
        self.ready("twitter")
        path = self.paths.root / "v9.sqlite"
        with sqlite3.connect(path) as db: self.service.store._conn.backup(db)
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM task_publications WHERE platform='twitter'")
            db.execute("DROP TABLE social_upload_attempts")
            db.execute("DROP TABLE social_accounts")
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='task_publications'").fetchone()[0]
            trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='immutable_publication_identity'").fetchone()[0]
            db.execute("DROP TRIGGER immutable_publication_identity")
            db.execute(schema.replace('"task_publications"', 'old_publications').replace(",'twitter','facebook','tiktok'", ""))
            db.execute("INSERT INTO old_publications SELECT * FROM task_publications")
            db.execute("DROP TABLE task_publications")
            db.execute("ALTER TABLE old_publications RENAME TO task_publications")
            # Reinstall the v9 identity trigger.
            db.execute(trigger)
            for platform in ("youtube", "acfun"):
                db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES(?,?,?,'source','submitted')", (platform + '-old', self.task_id, platform))
            db.execute("INSERT INTO youtube_attempts(publication_id,phase,media_sha256,request_sha256,updated_at) VALUES('youtube-old','submitted','hash','request','now')")
            db.execute("INSERT INTO acfun_attempts(attempt_id,publication_id,phase,started_at) VALUES('acfun-attempt','acfun-old','submitted','now')")
            db.execute("PRAGMA user_version=9")
        migrated = TaskStore(path)
        try:
            self.assertEqual(migrated._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(migrated._conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertIsNotNone(migrated._conn.execute("SELECT 1 FROM youtube_attempts WHERE publication_id='youtube-old'").fetchone())
            self.assertIsNotNone(migrated._conn.execute("SELECT 1 FROM acfun_attempts WHERE attempt_id='acfun-attempt'").fetchone())
            self.assertTrue(Path(str(path) + ".pre-v10.bak").is_file())
        finally: migrated.close()


class DestinationTests(unittest.TestCase):
    def test_upload_destinations_do_not_receive_account_tokens(self):
        client = SocialClient()
        for url in ("http://api.x.com/", "https://api.x.com.evil/", "https://api.x.com:443/", "https://evil@api.x.com/", "https://open-upload.tiktokapis.com/video/?private=token"):
            with self.assertRaises(AppError): client.request("POST", url, token="private-access")


if __name__ == "__main__": unittest.main()
