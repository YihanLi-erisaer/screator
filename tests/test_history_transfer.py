from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from yt2bili import publications
from yt2bili.db import Task
from yt2bili.desktop_service import DesktopService
from yt2bili.exceptions import Yt2BiliError
from yt2bili.paths import AppPaths


class Vault:
    def get_password(self, *_): return None
    def set_password(self, *_): pass
    def delete_password(self, *_): pass


class HistoryTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def service(self, name):
        folder = self.root / name
        service = DesktopService(AppPaths.default(str(folder), str(folder)), lambda *_: None, Vault())
        self.addCleanup(service.close)
        return service

    def task(self, service, video_id, status="submitted", bv_id="BV1234567890"):
        account_id = str(uuid.uuid4())
        with service.store.transaction() as db:
            db.execute("INSERT INTO bilibili_accounts(account_id,uid,nickname,slot,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (account_id, "23941395", "StarDazz", 1, "2026-01-01", "2026-01-01"))
        task = Task(task_id=str(uuid.uuid4()), video_id=video_id, url=f"https://youtu.be/{video_id}",
                    status=status, title_orig="Original", title_zh="中文标题", desc_zh="中文简介",
                    account_id=account_id, account_uid_snapshot="23941395", account_name_snapshot="StarDazz",
                    bv_id=bv_id, error="C:/private/diagnostic", work_dir="C:/private/video", video_path="C:/private/video/source.mp4")
        service.store.upsert(task)
        publications.ensure_bili(service.store, task)
        return task

    def test_export_import_round_trip_is_read_only_and_idempotent(self):
        source = self.service("source")
        task = self.task(source, "abcdefghijk")
        with source.store.transaction() as db:
            db.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id,status,remote_id,error,snapshot) VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), task.task_id, "acfun", "acfun-source", task.video_id,
                        "submitted", "123456", "C:/private/acfun-error", json.dumps({"nickname": "AcFun 用户", "secret": "never-export"})))
        file = self.root / "history.json"
        self.assertEqual(source.dispatch("history.export", {"path": str(file)})["exported"], 1)
        raw = file.read_text(encoding="utf-8")
        self.assertNotIn("C:/private", raw)
        self.assertNotIn("never-export", raw)
        self.assertNotIn("credential_ref", raw)
        target = self.service("target")
        result = target.dispatch("history.import", {"path": str(file)})
        self.assertEqual((result["imported"], result["skipped"]), (1, 0))
        self.assertEqual(target.dispatch("history.import", {"path": str(file)})["imported"], 0)
        self.assertEqual(target.store.list_all(), [])
        row = target.dispatch("tasks.list", {"history": True})["items"][0]
        self.assertTrue(row["imported_history"])
        self.assertEqual(row["account_uid_snapshot"], "23941395")
        self.assertEqual({p["platform"]: p["remote_id"] for p in row["publications"]},
                         {"bilibili": "BV1234567890", "acfun": "123456"})
        self.assertEqual(target.dispatch("tasks.get", {"task_id": task.task_id})["desc_zh"], "中文简介")
        with self.assertRaises(Yt2BiliError):
            target.dispatch("tasks.retry", {"task_id": task.task_id, "operation_id": str(uuid.uuid4())})
        second = self.root / "second.json"
        target.dispatch("history.export", {"path": str(second)})
        self.assertEqual(self.service("third").dispatch("history.import", {"path": str(second)})["imported"], 1)

    def test_invalid_file_does_not_import_any_record(self):
        source = self.service("source")
        self.task(source, "abcdefghijk")
        file = self.root / "history.json"
        source.dispatch("history.export", {"path": str(file)})
        data = json.loads(file.read_text(encoding="utf-8"))
        bad = dict(data["records"][0], task_id=str(uuid.uuid4()), status="queued_upload")
        data["records"].append(bad)
        file.write_text(json.dumps(data), encoding="utf-8")
        target = self.service("target")
        with self.assertRaises(Yt2BiliError):
            target.dispatch("history.import", {"path": str(file)})
        self.assertEqual(target.dispatch("tasks.list", {"history": True})["total"], 0)

    def test_unknown_result_stays_unknown_and_existing_receipt_is_skipped(self):
        source = self.service("source")
        task = self.task(source, "abcdefghijk", status="submission_unknown", bv_id="")
        file = self.root / "unknown.json"
        source.dispatch("history.export", {"path": str(file)})
        target = self.service("target")
        self.assertEqual(target.dispatch("history.import", {"path": str(file)})["imported"], 1)
        row = target.dispatch("tasks.get", {"task_id": task.task_id})
        self.assertEqual(row["status"], "submission_unknown")
        self.assertTrue(row["imported_history"])

        other = self.service("other")
        local = self.task(other, "lmnopqrstuv")
        source_file = self.root / "submitted.json"
        other.dispatch("history.export", {"path": str(source_file)})
        self.assertEqual(source.dispatch("history.import", {"path": str(source_file)})["imported"], 1)
        # The same BV is already present locally on the next device under a different task ID.
        second = self.service("second")
        self.task(second, "zyxwvutsrqpo", bv_id=local.bv_id)
        result = second.dispatch("history.import", {"path": str(source_file)})
        self.assertEqual((result["imported"], result["skipped"]), (0, 1))

    def test_delete_local_record_removes_related_rows_but_keeps_assets_and_account(self):
        service = self.service("local")
        task = self.task(service, "abcdefghijk")
        asset = self.root / "source.mp4"
        asset.write_bytes(b"video")
        service.store.update(task.task_id, video_path=str(asset))
        service.store.save_job(task.task_id, {"execution_state": "finished"})
        service.store.save_translation(service.store.require(task.task_id), {"state": "complete"})
        service.store.translation_attempt(task.task_id, [{"provider": "local_llm"}])
        publication_id = str(uuid.uuid4())
        with service.store.transaction() as db:
            db.execute("INSERT INTO task_publications(publication_id,task_id,platform,account_id,source_video_id,status) VALUES(?,?,?,?,?,?)",
                       (publication_id, task.task_id, "acfun", "acfun-source", task.video_id, "submitted"))
            db.execute("INSERT INTO acfun_attempts(attempt_id,publication_id,phase,started_at) VALUES(?,?,?,?)",
                       (str(uuid.uuid4()), publication_id, "complete", "2026-01-01"))
            db.execute("INSERT INTO upload_attempts(attempt_id,task_id,account_id,uid,run_id,prepared_at,outcome) VALUES(?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), task.task_id, task.account_id, "23941395", "run", "2026-01-01", "submitted"))
            db.execute("INSERT INTO legacy_task_map VALUES(?,?,?)", ("source", "old-id", task.task_id))
            db.execute("INSERT INTO import_conflicts VALUES(?,?,?,?)", ("source", "conflict", "{}", task.task_id))
        revision = service.store.require(task.task_id).revision
        with self.assertRaises(Yt2BiliError):
            service.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": revision})
        with self.assertRaises(Yt2BiliError):
            service.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": revision - 1, "confirmed": True})
        result = service.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": revision, "confirmed": True})
        self.assertEqual(result["kind"], "local")
        self.assertTrue(asset.exists())
        self.assertEqual(len(service.store.accounts(True)), 1)
        self.assertIsNone(service.store.get(task.task_id))
        with service.store._lock:
            for table in ("task_publications", "acfun_attempts", "desktop_jobs", "task_translation", "translation_attempts", "upload_attempts", "legacy_task_map"):
                self.assertEqual(service.store._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
            self.assertIsNone(service.store._conn.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual(service.store._conn.execute("SELECT count(*) FROM import_conflicts").fetchone()[0], 0)

    def test_delete_imported_record_and_reject_unfinished_or_queued_task(self):
        source = self.service("source")
        task = self.task(source, "abcdefghijk")
        file = self.root / "history.json"
        source.dispatch("history.export", {"path": str(file)})
        target = self.service("target")
        target.dispatch("history.import", {"path": str(file)})
        self.assertEqual(target.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": 1, "confirmed": True})["kind"], "imported")
        self.assertEqual(target.dispatch("tasks.list", {"history": True})["total"], 0)
        with self.assertRaises(Yt2BiliError):
            target.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": 1, "confirmed": True})

        source.store.update(task.task_id, status="ready")
        revision = source.store.require(task.task_id).revision
        with self.assertRaises(Yt2BiliError):
            source.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": revision, "confirmed": True})
        source.store.update(task.task_id, status="submitted")
        source.store.save_job(task.task_id, {"execution_state": "queued"})
        revision = source.store.require(task.task_id).revision
        with self.assertRaises(Yt2BiliError):
            source.dispatch("history.delete", {"task_id": task.task_id, "expected_revision": revision, "confirmed": True})
        self.assertIsNotNone(source.store.get(task.task_id))


if __name__ == "__main__":
    unittest.main()
