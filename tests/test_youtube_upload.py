import hashlib
import json
import sqlite3
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import test_desktop as desktop_tests
from screator import publications
from screator.db import SCHEMA_VERSION, TaskStore
from screator.exceptions import AppError
from screator.youtube_upload import (CHUNK_SIZE, SCOPES, AuthRequired, GoogleClient,
                                    RateLimited, UnknownResult, validate_metadata)


CHANNEL = "UC" + "a" * 22
REMOTE = "AbCdEfGhI12"
SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private-session"


class FakeGoogle:
    def __init__(self):
        self.calls = []
        self.offset = 0
        self.complete = False
        self.fail = ""
        self.channel = CHANNEL
        self.metadata = None
        self.refreshes = 0

    def token(self, form):
        self.refreshes += 1
        self.form = form
        return {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600, "scope": " ".join(SCOPES)}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "/channels?" in url:
            return 200, {}, {"items": [{"id": self.channel, "snippet": {"title": "测试频道"}}]}
        if "/videoCategories?" in url:
            return 200, {}, {"items": [{"id": "22", "snippet": {"title": "人物与博客", "assignable": True}}]}
        if "/thumbnails/" in url:
            if self.fail == "cover": raise AppError("no custom thumbnail permission")
            return 200, {}, {"items": [{}]}
        if method == "POST":
            self.metadata = kwargs["data"]
            return 200, {"location": SESSION}, {}
        content_range = kwargs["headers"]["Content-Range"]
        if content_range.startswith("bytes */"):
            if self.fail == "unknown" and self.complete: raise UnknownResult("response lost")
            if self.complete: return 200, {}, {"id": REMOTE}
            return 308, ({"range": f"bytes=0-{self.offset - 1}"} if self.offset else {}), {}
        if self.fail == "quota": raise RateLimited("quota")
        if self.fail == "auth": raise AuthRequired("expired")
        if self.fail == "after_partial" and self.offset: raise UnknownResult("connection lost after a partial upload")
        if self.fail == "early": raise UnknownResult("connection lost before final chunk")
        self.offset += len(kwargs["raw"])
        size = int(content_range.split("/")[-1])
        if self.offset == size:
            self.complete = True
            if self.fail in ("unknown", "lost_final"): raise UnknownResult("final response lost")
            return 200, {}, {"id": REMOTE}
        return 308, {"range": f"bytes=0-{self.offset - 1}"}, {}


class YoutubeTests(unittest.TestCase):
    setUp = desktop_tests.DesktopTests.setUp
    wait_idle = desktop_tests.DesktopTests.wait_idle
    wait_until = desktop_tests.DesktopTests.wait_until
    mocks = desktop_tests.DesktopTests.mocks
    meta = desktop_tests.DesktopTests.meta
    download = desktop_tests.DesktopTests.download
    prepare = desktop_tests.DesktopTests.prepare
    upload = desktop_tests.DesktopTests.upload

    def bind(self):
        self.google = FakeGoogle()
        service = self.service.youtube_upload
        service.client_factory = lambda: self.google
        service._vault("youtube_oauth_client", {"client_id": "test.apps.googleusercontent.com", "client_secret": "private-client-secret"})
        service._bind((CHANNEL, "测试频道"), {"access_token": "private-access", "refresh_token": "private-refresh", "expires_at": time.time() + 3600})
        return service.account()

    def create_ready(self, video_id="abcdefghijk"):
        account = self.bind()
        with self.mocks():
            result = self.service.create("https://youtu.be/" + video_id, str(uuid.uuid4()), self.account["account_id"], sync_youtube=True,
                                         youtube_account_id=account["account_id"], youtube_binding_revision=account["binding_revision"])
            self.wait_idle()
        self.task_id = result["task_id"]
        return publications.for_platform(self.service.store, self.task_id, "youtube")

    def test_preview_and_submission_use_shared_assets_and_independent_receipts(self):
        pub = self.create_ready()
        snapshot = json.loads(pub["snapshot"])
        self.assertEqual(snapshot["description"], "简介")
        self.assertEqual(snapshot["tags"], ["转载"])
        self.assertEqual(snapshot["privacy_status"], "private")
        self.assertEqual(pub["status"], "ready")
        with self.mocks():
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
        task = self.service.get_task(self.task_id)
        self.assertEqual(task["status"], "submitted")
        self.assertEqual(task["bv_id"], "BV1234567890")
        self.assertEqual(publications.for_platform(self.service.store, self.task_id, "youtube")["remote_id"], REMOTE)
        self.assertEqual(self.google.metadata["status"]["privacyStatus"], "private")
        self.assertEqual(len(self.uploads), 1)
        serialized = json.dumps(task)
        for secret in ("private-access", "private-refresh", "private-client-secret", "private-session"):
            self.assertNotIn(secret, serialized)

    def test_auto_submission_and_queue_capability(self):
        self.bind()
        with self.mocks():
            result = self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], mode="auto", sync_youtube=True)
            self.wait_idle()
        self.assertEqual(self.service.store.require(result["task_id"]).status, "submitted")
        self.assertIn("youtube_sync_v1", self.service.health()["capabilities"])
        self.assertIn("youtube", self.service.scheduler.snapshot())

    def test_metadata_title_is_independent_and_survives_freeze(self):
        pub = self.create_ready()
        result = self.service.update_publication(pub["publication_id"], revision=pub["revision"], title="独立标题", category_id="22", privacy_status="unlisted", made_for_kids=True)
        self.assertEqual(json.loads(result["snapshot"])["title_source"], "manual")
        self.service.update_metadata(self.task_id, "B站标题", "共用简介")
        publications.freeze(self.service.store, self.task_id)
        snapshot = json.loads(publications.get(self.service.store, pub["publication_id"])["snapshot"])
        self.assertEqual(snapshot["title"], "独立标题")
        self.assertEqual(snapshot["description"], self.service.store.require(self.task_id).desc_zh)
        self.assertTrue(snapshot["made_for_kids"])

    def test_mismatched_channel_cannot_publish(self):
        pub = self.create_ready()
        self.google.channel = "UC" + "b" * 22
        with self.assertRaises(AuthRequired):
            self.service.youtube_upload.publish(SimpleNamespace(task_id=self.task_id))
        self.assertFalse(any(method == "POST" for method, _, _ in self.google.calls))

    def test_duplicate_source_and_stale_binding_are_rejected(self):
        account = self.bind()
        with self.assertRaises(AppError):
            self.service.create("https://youtu.be/abcdefghijk", str(uuid.uuid4()), self.account["account_id"], sync_youtube=True, youtube_binding_revision=999)
        self.assertEqual(self.service.store.list_all(), [])

    def test_thumbnail_failure_keeps_success_receipt(self):
        self.create_ready()
        self.google.fail = "cover"
        with self.mocks():
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
        pub = publications.for_platform(self.service.store, self.task_id, "youtube")
        self.assertEqual(pub["status"], "submitted")
        self.assertEqual(pub["remote_id"], REMOTE)
        self.assertEqual(pub["retain_assets"], 1)
        self.assertIn("封面", pub["error"])
        self.assertTrue(Path(self.service.store.require(self.task_id).video_path).is_file())
        with self.assertRaises(AppError): self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))

    def test_lost_final_response_queries_same_session(self):
        self.create_ready()
        self.google.fail = "lost_final"
        with self.mocks():
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
        pub = publications.for_platform(self.service.store, self.task_id, "youtube")
        self.assertEqual(pub["status"], "submitted")
        self.assertEqual(sum(method == "POST" and "/videos?" in url for method, url, _ in self.google.calls), 1)

    def test_uncertain_final_upload_requires_manual_resolution(self):
        pub = self.create_ready()
        self.google.fail = "unknown"
        with self.mocks():
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
        pub = publications.get(self.service.store, pub["publication_id"])
        self.assertEqual(pub["status"], "submission_unknown")
        with self.assertRaises(AppError): self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))
        self.service.resolve_publication(pub["publication_id"], REMOTE)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")

    def test_partial_failure_retries_only_youtube_using_original_session(self):
        pub = self.create_ready()
        # Ensure the failure happens before any finalizing request.
        self.service.store.require(self.task_id)
        self.google.fail = "early"
        with self.mocks(), patch("screator.youtube_upload.CHUNK_SIZE", 4):
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
            self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "failed")
            self.assertEqual(len(self.uploads), 1)
            self.google.fail = ""
            self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))
            self.service.submit(self.task_id, str(uuid.uuid4()), targets=[pub["publication_id"]])
            self.wait_idle()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")
        self.assertEqual(sum(method == "POST" and "/videos?" in url for method, url, _ in self.google.calls), 1)

    def test_expired_access_token_refreshes_without_exposing_credentials(self):
        self.bind()
        service = self.service.youtube_upload
        saved = service._vault("youtube_tokens")
        saved["expires_at"] = 0
        service._vault("youtube_tokens", saved)
        self.assertTrue(service.status(True)["can_sync"])
        self.assertEqual(self.google.refreshes, 1)
        self.assertNotIn("refresh_token", json.dumps(service.status()))

    def test_resuming_partial_upload_starts_at_server_acknowledged_offset(self):
        pub = self.create_ready()
        self.google.fail = "after_partial"
        with self.mocks(), patch("screator.youtube_upload.CHUNK_SIZE", 4):
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
            self.assertEqual(self.google.offset, 4)
            self.google.fail = ""
            self.service.retry_publication(pub["publication_id"], str(uuid.uuid4()))
            self.service.submit(self.task_id, str(uuid.uuid4()), targets=[pub["publication_id"]])
            self.wait_idle()
        ranges = [kwargs["headers"].get("Content-Range", "") for method, _, kwargs in self.google.calls if method == "PUT"]
        self.assertEqual(ranges.count("bytes 0-3/14"), 1)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["remote_id"], REMOTE)

    def test_rate_limited_queue_waits_for_explicit_resume(self):
        pub = self.create_ready()
        self.google.fail = "quota"
        with self.mocks(), patch("screator.youtube_upload.CHUNK_SIZE", 4):
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_until(lambda: self.service.scheduler.snapshot()["youtube"]["wait_reason"] == "rate_limited", "YouTube quota wait")
            self.google.fail = ""
            self.service.emit("youtube.auth.changed", {})
            self.assertEqual(self.service.scheduler.snapshot()["youtube"]["wait_reason"], "rate_limited")
            self.service.youtube_upload.resume()
            self.wait_idle()
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submitted")

    def test_final_auth_failure_is_unknown_instead_of_waiting_or_reposting(self):
        pub = self.create_ready()
        self.google.fail = "auth"
        with self.mocks():
            self.service.submit(self.task_id, str(uuid.uuid4()))
            self.wait_idle()
        self.assertEqual(publications.get(self.service.store, pub["publication_id"])["status"], "submission_unknown")
        self.assertEqual(self.service.youtube_upload.account()["auth_state"], "expired")
        self.assertFalse(self.service.scheduler.snapshot()["active"])

    def test_recovery_preserves_final_intent_and_success_receipts(self):
        pub = self.create_ready()
        store = self.service.store
        with store.transaction() as db:
            db.execute("INSERT INTO youtube_attempts(publication_id,phase,media_sha256,request_sha256,updated_at) VALUES(?,'finalizing','hash','request','now')", (pub["publication_id"],))
            publications.change(store, pub["publication_id"], status="creating")
            publications.project(store, self.task_id)
        self.service.scheduler._recover()
        self.assertEqual(publications.get(store, pub["publication_id"])["status"], "submission_unknown")
        self.service.youtube_upload._phase(pub["publication_id"], "submitted", REMOTE)
        self.service.scheduler._recover()
        recovered = publications.get(store, pub["publication_id"])
        self.assertEqual(recovered["status"], "submitted")
        self.assertEqual(recovered["remote_id"], REMOTE)
        self.assertEqual(recovered["retain_assets"], 1)

    def test_history_supports_all_four_platforms_without_secrets(self):
        pub = self.create_ready()
        store = self.service.store
        with store.transaction() as db:
            for platform in ("acfun", "douyin"):
                db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES(?,?,?,'source','abandoned')", (str(uuid.uuid4()), self.task_id, platform))
            for p in publications.items(store, self.task_id):
                publications.change(store, p["publication_id"], status="submitted", remote_id=REMOTE if p["platform"] == "youtube" else "fixture")
            store.update(self.task_id, status="submitted", bv_id="BV1234567890")
        path = self.paths.root / "history.json"
        self.service.export_history(str(path))
        contents = path.read_text()
        self.assertNotIn("private-access", contents)
        self.assertNotIn("private-session", contents)
        # A new profile imports this as read-only history, including all targets.
        from screator import history_transfer
        imported = TaskStore(self.paths.root / "import.sqlite")
        try:
            history_transfer.initialize(imported)
            result = history_transfer.import_file(imported, str(path))
            self.assertEqual(result["imported"], 1)
            record = history_transfer.get_imported(imported, self.task_id)
            self.assertEqual({p["platform"] for p in record["publications"]}, {"bilibili", "douyin", "acfun", "youtube"})
        finally: imported.close()

    def test_archive_and_logout_protect_pending_and_inflight_targets(self):
        pub = self.create_ready()
        with self.assertRaises(AppError): self.service.youtube_upload.archive()
        publications.change(self.service.store, pub["publication_id"], status="uploading_media")
        with self.assertRaises(AppError): self.service.youtube_upload.clear()
        publications.change(self.service.store, pub["publication_id"], status="abandoned")
        self.service.youtube_upload.archive()
        self.assertIsNone(self.service.youtube_upload.account())

    def test_oauth_loopback_validates_state_and_pkce(self):
        self.bind()
        service = self.service.youtube_upload
        with patch("screator.youtube_upload.webbrowser.open", return_value=True) as browser:
            self.assertEqual(service.start()["status"], "waiting")
        query = parse_qs(urlsplit(browser.call_args.args[0]).query)
        challenge = query["code_challenge"][0]
        import base64
        self.assertEqual(challenge, base64.urlsafe_b64encode(hashlib.sha256(service.auth["verifier"].encode()).digest()).rstrip(b"=").decode())
        with self.assertRaises(Exception): urlopen(service.auth["redirect"] + "?state=wrong&code=test", timeout=3)
        self.assertEqual(service.poll()["status"], "waiting")
        url = service.auth["redirect"] + "?state=" + query["state"][0] + "&code=test-code"
        with urlopen(url, timeout=3) as response: self.assertEqual(response.status, 200)
        self.assertEqual(service.poll()["status"], "done")
        self.assertEqual(self.google.form["grant_type"], "authorization_code")

    def test_metadata_limits_are_utf8_and_boolean_strict(self):
        self.create_ready()
        snapshot = json.loads(publications.for_platform(self.service.store, self.task_id, "youtube")["snapshot"])
        for update in ({"title": "x" * 101}, {"description": "字" * 1667}, {"made_for_kids": 1}, {"title": "<bad>"}):
            with self.assertRaises(AppError): validate_metadata({**snapshot, **update})

    def test_v8_migration_preserves_acfun_attempts_and_backups(self):
        self.create_ready()
        store = self.service.store
        path = self.paths.root / "migration.sqlite"
        with sqlite3.connect(path) as db: store._conn.backup(db)
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM task_publications WHERE platform='youtube'")
            db.execute("DROP TABLE youtube_attempts")
            db.execute("DROP TABLE youtube_accounts")
            # Restore the actual v8 publication constraint.
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='task_publications'").fetchone()[0]
            db.execute("DROP TRIGGER immutable_publication_identity")
            db.execute(schema.replace('"task_publications"', 'old_publications').replace("'acfun','youtube'", "'acfun'"))
            db.execute("INSERT INTO old_publications SELECT * FROM task_publications")
            db.execute("DROP TABLE task_publications")
            db.execute("ALTER TABLE old_publications RENAME TO task_publications")
            task = store.require(self.task_id)
            db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id) VALUES('ac-pub',?,'acfun',?)", (self.task_id, task.video_id))
            db.execute("INSERT INTO acfun_attempts(attempt_id,publication_id,phase,started_at) VALUES('ac-attempt','ac-pub','submitted','now')")
            db.execute("PRAGMA user_version=8")
        migrated = TaskStore(path)
        try:
            self.assertEqual(migrated._conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertIsNotNone(migrated._conn.execute("SELECT 1 FROM acfun_attempts WHERE attempt_id='ac-attempt'").fetchone())
            self.assertEqual(migrated._conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertTrue(Path(str(path) + ".pre-v9.bak").is_file())
        finally: migrated.close()


class TransportTests(unittest.TestCase):
    def test_unsafe_destinations_never_receive_credentials(self):
        client = GoogleClient()
        for url in ("http://www.googleapis.com/", "https://www.googleapis.com.evil/", "https://evil@www.googleapis.com/", "https://www.googleapis.com:443/"):
            with self.assertRaises(AppError): client.request("PUT", url, token="private-access")


if __name__ == "__main__": unittest.main()
