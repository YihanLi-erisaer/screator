from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from screator import history_transfer, publications
from screator.data_center import DataCenter
from screator.db import Task, TaskStore, _now
from screator.exceptions import AppError


def credentials():
    return {"cookie_info": {"cookies": [{"name": name, "value": "123" if name == "DedeUserID" else "fixture"}
                                        for name in ("SESSDATA", "bili_jct", "DedeUserID")]},
            "sso": [], "token_info": {"access_token": "fixture", "refresh_token": "fixture", "expires_in": 3600, "mid": 123}}


class DataCenterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.store = TaskStore(root / "tasks.sqlite")
        self.addCleanup(self.store.close)
        history_transfer.initialize(self.store)
        self.account = {"account_id": "account-1", "uid": "123", "nickname": "测试账号",
                        "remark": "", "auth_state": "valid", "credential_revision": 1}
        self.store._conn.execute("""INSERT INTO bilibili_accounts
            (account_id, uid, nickname, slot, auth_state, credential_revision, created_at, updated_at)
            VALUES (?, ?, ?, 1, 'valid', 1, ?, ?)""",
            (self.account["account_id"], self.account["uid"], self.account["nickname"], _now(), _now()))
        class Accounts:
            def read(self, account_id):
                return self.account, credentials()
        self.accounts = Accounts()
        self.accounts.account = self.account
        self.now = 0
        self.calls = []

        def get(url, *, cookies=None, params=None):
            self.calls.append((url, params))
            if url.endswith("/x/relation/stat"):
                return {"follower": 0}
            if url.endswith("/x/space/upstat"):
                return {"archive": {"view": 456}}
            if url.endswith("/x/web/archives"):
                return {"page": {"count": 25}}
            if url.endswith("/x/client/archive/view"):
                return {"archive": {"bvid": params["bvid"], "state": 0, "state_desc": "审核通过"}}
            if url.endswith("/x/web-interface/view"):
                return {"bvid": params["bvid"], "owner": {"mid": 123},
                        "stat": {"view": 0, "like": 5, "reply": 3, "favorite": 2}}
            self.fail(f"Unexpected URL: {url}")

        self.center = DataCenter(self.store, self.accounts, get=get, clock=lambda: self.now)

    def record(self, index):
        task = Task(video_id=f"abcdefgh{index:03d}", url=f"https://youtu.be/abcdefgh{index:03d}",
                    status="submitted", title_zh=f"稿件 {index}", account_id=self.account["account_id"],
                    account_uid_snapshot=self.account["uid"], account_name_snapshot="测试账号")
        self.store.upsert(task)
        publications.ensure_bili(self.store, task)
        pub = publications.for_platform(self.store, task.task_id, "bilibili")
        publications.change(self.store, pub["publication_id"], remote_id=f"BV{index:010d}")

    def test_current_page_only_zero_metrics_and_five_minute_cache(self):
        for index in range(25):
            self.record(index)
        first = self.center.list()
        self.assertEqual((len(first["items"]), first["total"]), (20, 25))
        self.assertEqual(first["accounts"][0]["followers"], 0)
        self.assertEqual(first["accounts"][0]["views"], 456)
        self.assertEqual(first["accounts"][0]["publications"], 25)
        self.assertEqual(first["items"][0]["review_status"], "审核通过")
        self.assertEqual(first["items"][0]["views"], 0)
        initial_calls = len(self.calls)
        self.now = 299
        self.center.list()
        self.assertEqual(len(self.calls), initial_calls)
        second = self.center.list(offset=20)
        self.assertEqual(len(second["items"]), 5)
        self.assertEqual(len(self.calls) - initial_calls, 10)
        self.now = 300
        self.center.list(offset=20)
        self.assertEqual(len(self.calls) - initial_calls, 13)
        self.now = 599
        self.center.list(offset=20)
        self.assertEqual(len(self.calls) - initial_calls, 23)

    def test_unknown_review_and_unavailable_metrics_are_null(self):
        self.record(1)
        self.center.get = lambda url, **kwargs: None
        row = self.center.list()["items"][0]
        self.assertIsNone(row["review_status"])
        self.assertIsNone(row["likes"])

    def test_acfun_record_is_listed_without_invented_remote_metrics(self):
        self.record(1)
        task = self.store.list_all()[0]
        self.store._conn.execute("""INSERT INTO task_publications
            (publication_id, task_id, platform, account_id, source_video_id, status, remote_id)
            VALUES ('acfun-1', ?, 'acfun', 'acfun-account', ?, 'submitted', 'AC123456')""",
            (task.task_id, task.video_id))
        result = self.center.list(platform="acfun")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["remote_id"], "AC123456")
        self.assertIsNone(result["items"][0]["review_status"])
        self.assertIsNone(result["items"][0]["views"])
        self.assertFalse(any("AC123456" in str(call) for call in self.calls))

    def test_rejects_unbounded_pages_and_unknown_platform(self):
        for kwargs in ({"limit": 21}, {"offset": -1}, {"platform": "other"}):
            with self.assertRaises(AppError):
                self.center.list(**kwargs)


if __name__ == "__main__":
    unittest.main()
