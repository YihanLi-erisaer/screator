import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from screator import history_transfer, publications
from screator.db import Task
from screator.desktop_service import DesktopService
from screator.paths import AppPaths


class Vault:
    def get_password(self, *_):
        return "test-key"


class StartupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = AppPaths.default(folder.name, folder.name)
        self.service = DesktopService(self.paths, lambda *_: None, vault=Vault())
        self.addCleanup(self.service.close)

    def task(self, identity, status="submitted", **values):
        task = Task(identity, "https://youtu.be/" + identity, status,
                    task_id=identity, bv_id="BV1234567890", **values)
        self.service.store.upsert(task)
        publications.ensure_bili(self.service.store, task)
        return task

    def test_base_settings_and_health_do_not_wait_for_service_detection(self):
        with patch.object(self.service.config.vault, "get_password", side_effect=AssertionError("vault accessed")), \
             patch("screator.translation.runtime.status", side_effect=AssertionError("runtime probed")), \
             patch.object(self.service.scheduler, "snapshot", side_effect=AssertionError("queue scanned")):
            config = self.service.dispatch("settings.get", {"check_services": False})
            self.assertTrue(config["readiness_pending"])
            self.assertEqual(self.service.health()["protocol_version"], 2)
        with patch("screator.translation.runtime.status", return_value={"state": "ready"}):
            status = self.service.dispatch("settings.status", {})
            self.assertTrue(status["translation_ready"])
            self.assertTrue(status["has_deepl_key"])
            self.assertFalse(status["readiness_pending"])

    def test_sql_pages_keep_unicode_search_literal_wildcards_and_global_counts(self):
        matched = self.task("matched", title_orig="Ä VIDEO %_", title_zh="中文")
        self.task("other", "ready", title_orig="Different title")
        with patch.object(self.service.store, "list_all", side_effect=AssertionError("full history loaded")):
            result = self.service.list_tasks(search="ä video %_")
            self.assertEqual([item["task_id"] for item in result["items"]], [matched.task_id])
            self.assertEqual((result["total"], result["all_total"]), (1, 2))
            self.assertEqual((result["counts"]["ready"], result["counts"]["submitted"]), (1, 1))
            self.assertEqual(self.service.list_tasks(offset=20)["items"], [])

    def test_local_and_imported_history_share_order_filter_and_pagination(self):
        local = self.task("local", title_orig="Ä title")
        stamp = "2026-01-01T00:00:00+00:00"
        record = {**history_transfer._local_record(self.service.store, local),
                  "task_id": "imported", "updated_at": stamp, "account_uid_snapshot": "123"}
        with self.service.store.transaction() as db:
            db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (stamp, local.task_id))
            db.execute("INSERT INTO imported_publishing_history VALUES(?,?,?)", ("imported", json.dumps(record), stamp))
            db.execute("INSERT INTO bilibili_accounts(account_id,uid,slot,created_at,updated_at) VALUES('account','123',1,?,?)", (stamp, stamp))
        with patch.object(self.service.store, "list_all", side_effect=AssertionError("full history loaded")), \
             patch("screator.history_transfer.list_imported", side_effect=AssertionError("all imports decoded")):
            first = self.service.list_tasks(history=True, limit=1, search="ä title")
            second = self.service.list_tasks(history=True, limit=1, offset=1)
            self.assertEqual(first["items"][0]["task_id"], "local")
            self.assertEqual(second["items"][0]["task_id"], "imported")
            self.assertTrue(second["items"][0]["imported_history"])
            self.assertEqual((first["total"], first["all_total"]), (2, 2))
            filtered = self.service.list_tasks(history=True, account_id="account")
            self.assertEqual([row["task_id"] for row in filtered["items"]], ["imported"])

    def test_recovery_preserves_settled_history_and_freezes_uncertain_uploads(self):
        settled = self.task("settled")
        active = self.task("active", "uploading")
        missing = self.task("missing")
        self.service.store.update(missing.task_id, bv_id="")
        multi = self.task("multi")
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES('dy','multi','douyin','multi','creating')")
        self.service.store.save_job(settled.task_id, {"execution_state": "complete"})
        before = self.service.store.require(settled.task_id)
        self.service.close()
        with patch("screator.db.TaskStore.list_all", side_effect=AssertionError("full history loaded")):
            service = DesktopService(self.paths, lambda *_: None, vault=Vault())
        self.addCleanup(service.close)
        after = service.store.require(settled.task_id)
        self.assertEqual((after.revision, after.updated_at), (before.revision, before.updated_at))
        self.assertEqual(service.store.get_job(settled.task_id)["execution_state"], "complete")
        for task in (active, missing, multi):
            self.assertEqual(service.store.require(task.task_id).status, "submission_unknown")
        self.assertEqual(service.scheduler.snapshot()["active"], [])

    def test_queue_snapshot_reports_queued_session_work_without_loading_history(self):
        task = self.task("queued", "queued_download")
        # Keep the dispatcher from consuming this queued fixture during sampling.
        with self.service.scheduler.guard, self.service.store.transaction() as db:
            self.service.store.save_job(task.task_id, {"owner_session_id": self.service.scheduler.session_id,
                "execution_state": "queued", "stage": "download", "mode": "preview", "run_id": "current"})
            with patch.object(self.service.store, "list_all", side_effect=AssertionError("full history loaded")):
                snapshot = self.service.scheduler.snapshot()
            db.execute("DELETE FROM desktop_jobs WHERE task_id=?", (task.task_id,))
        self.assertEqual(snapshot["download"]["queued_count"], 1)
        self.assertEqual(snapshot["active"][0]["task_id"], task.task_id)

    def test_desktop_import_defers_downloader_deepl_and_image_modules(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, "-c",
            "import sys; import screator.desktop_service; "
            "assert not any(name in sys.modules for name in ('yt_dlp','deepl','PIL.Image'))"],
            cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
