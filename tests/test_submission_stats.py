import json
import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

from screator import history_transfer, publications
from screator.db import Task
from screator.desktop_service import DesktopService
from screator.paths import AppPaths


class Vault:
    def get_password(self, *_): return None


class SubmissionStatsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.service = DesktopService(AppPaths.default(folder.name, folder.name), lambda *_: None, Vault())
        self.addCleanup(self.service.close)
        self.today = datetime.now().astimezone().date()

    def stamp(self, days=0):
        return datetime.combine(self.today - timedelta(days=days), time(12)).astimezone(timezone.utc).isoformat()

    def imported(self, identity, uid="123", stamp=None, status="submitted", platform="bilibili", **extra):
        payload = dict(task_id=identity, video_id=identity, title_zh="历史投稿", title_orig="",
                       status="partial_success", account_uid_snapshot=uid, account_name_snapshot="原账号",
                       updated_at=stamp or self.stamp(), bv_id="BV1234567890",
                       publications=[dict(platform=platform, status=status)], **extra)
        self.service.store._conn.execute("INSERT INTO imported_publishing_history VALUES(?,?,?)",
                                        (identity, json.dumps(payload), self.stamp()))

    def trend(self, **filters):
        return self.service.list_tasks(history=True, **filters)["submission_trend"]

    def test_imported_and_unbound_history_changes_with_database(self):
        task = Task("local", "https://youtu.be/local", "submitted")
        self.service.store.upsert(task)
        publications.ensure_bili(self.service.store, task)
        self.imported("first", stamp=self.stamp(29))
        self.imported("today-offset", stamp=datetime.fromisoformat(self.stamp()).astimezone(timezone(timedelta(hours=-7))).isoformat())
        self.imported("old", stamp=self.stamp(30))
        self.imported("future", stamp=self.stamp(-1))
        self.imported("uncertain", status="submission_unknown")
        self.imported("acfun-only", platform="acfun")
        self.imported("invalid-date", stamp="invalid")
        with patch.object(self.service.store, "list_all", side_effect=AssertionError("full tasks loaded")):
            trend = self.trend(limit=1, offset=1, search="no matches", platform="acfun")
        self.assertEqual(trend["by_account"]["history:123"][0], 1)
        self.assertEqual(trend["by_account"]["history:123"][-1], 1)
        self.assertEqual(sum(trend["by_account"]["history:123"]), 2)
        self.assertEqual(trend["by_account"]["history:unknown"][-1], 1)
        self.assertEqual(trend["account_labels"]["history:123"]["name"], "原账号")
        history_transfer.delete_record(self.service.store, "first", 1)
        self.assertEqual(sum(self.trend()["by_account"]["history:123"]), 1)
        history_transfer.delete_all_records(self.service.store)
        self.assertEqual(self.trend()["by_account"], {})

    def test_imports_match_bound_accounts_by_uid_and_keep_submission_date(self):
        stamp = self.stamp()
        self.service.store._conn.execute("""INSERT INTO bilibili_accounts
            (account_id,uid,nickname,slot,created_at,updated_at) VALUES('bound','123','当前账号',1,?,?)""", (stamp, stamp))
        task = Task("source", "https://youtu.be/source", "submitted", task_id="source",
                    account_id="bound", account_uid_snapshot="123", bv_id="BV1234567890")
        self.service.store.upsert(task)
        publications.ensure_bili(self.service.store, task)
        submitted_at = self.stamp(2)
        self.service.store._conn.execute("""INSERT INTO upload_attempts
            (attempt_id,task_id,account_id,uid,run_id,prepared_at,ended_at,outcome,bv_id)
            VALUES('attempt','source','bound','123','run',?,?,'submitted','BV1234567890')""", (submitted_at, submitted_at))
        record = history_transfer._local_record(self.service.store, task)
        self.assertEqual(record["submitted_at"], submitted_at)
        record["task_id"] = "imported"
        self.service.store._conn.execute("INSERT INTO imported_publishing_history VALUES(?,?,?)", ("imported", json.dumps(record), stamp))
        self.assertEqual(self.trend()["by_account"]["bound"][-3:], [2, 0, 0])
        self.assertNotIn("history:123", self.trend()["by_account"])

    def test_legacy_import_without_platforms_and_unknown_account_is_counted(self):
        self.imported("legacy", uid="")
        self.service.store._conn.execute("""UPDATE imported_publishing_history
            SET payload=json_set(payload,'$.publications',json('[]'),'$.status','submitted')""")
        self.assertEqual(self.trend()["by_account"]["history:unknown"][-1], 1)
