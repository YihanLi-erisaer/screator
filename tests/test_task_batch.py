import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from screator.db import Task
from screator.desktop_service import DesktopService
from screator.exceptions import AppError
from screator.paths import AppPaths
from test_desktop import MemoryVault, login_fixture


class TaskBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {"SCREATOR_COORDINATION_DIR": str(Path(self.tmp.name) / "coordination")})
        environment.start()
        self.addCleanup(environment.stop)
        self.service = DesktopService(AppPaths.default(self.tmp.name, self.tmp.name), lambda *_: None, MemoryVault())
        self.addCleanup(self.service.close)
        self.account = self.service.accounts.bind(login_fixture(), verify=False)
        self.service.store.update_account(self.account["account_id"], auth_state="valid")

    def task(self, status, mode="preview", source="local"):
        task_id = str(uuid.uuid4())
        task = Task(task_id=task_id, video_id=task_id, url="", status=status,
                    account_id=self.account["account_id"], account_uid_snapshot=self.account["uid"], source_kind=source)
        self.service.store.upsert(task)
        self.service.store.save_job(task_id, {"mode": mode, "settings": {"bili_tid": 171}, "execution_state": "finished"})
        return task

    def publication(self, task, platform, status):
        identity = str(uuid.uuid4())
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES(?,?,?,?,?)",
                       (identity, task.task_id, platform, task.video_id, status))
        return identity

    def test_cancel_all_spans_pages_preserves_finished_and_protected_tasks(self):
        pending = [self.task("pending", source="material") for _ in range(25)]
        pending += [self.task(status) for status in ("ready", "failed", "interrupted")]
        untouched = [self.task(status) for status in ("submitted", "downloaded", "cancelled", "submission_unknown", "completed_with_abandon")]
        inflight = self.task("uploading")
        result = self.service.dispatch("tasks.cancel_all", {"operation_id": "cancel-batch-01"})
        self.assertEqual(result["processed"], 28)
        self.assertEqual(result["skipped"], 5)
        self.assertEqual([item["task_id"] for item in result["errors"]], [inflight.task_id])
        for task in pending:
            self.assertEqual(self.service.task(task.task_id).status, "cancelled")
        for task in untouched + [inflight]:
            self.assertEqual(self.service.task(task.task_id).status, task.status)
        self.assertEqual(self.service.cancel_all("cancel-batch-01"), result)

    def test_retry_all_continues_after_error_and_preserves_modes_and_settings(self):
        retry = [self.task("failed", "auto"), self.task("cancelled"), self.task("interrupted", "auto")]
        material = self.task("failed", "download", "material")
        rejected = self.task("failed")
        untouched = [self.task(status) for status in ("submitted", "ready", "downloading", "cancel_requested", "downloaded", "submission_unknown")]

        def enqueue(task, mode, settings):
            if task.task_id == rejected.task_id:
                raise AppError("任务正在执行，请先等待或取消。")
            self.service.store.update(task.task_id, status="queued_download")

        with patch.object(self.service.scheduler, "add", side_effect=enqueue) as add:
            result = self.service.retry_all("retry-batch-01")
            self.assertEqual(result["processed"], 4)
            self.assertEqual(result["skipped"], 6)
            self.assertEqual([item["task_id"] for item in result["errors"]], [rejected.task_id])
            calls = {call.args[0].task_id: call.args[1:] for call in add.call_args_list}
            for task, mode in zip(retry + [material], ("auto", "preview", "auto", "download")):
                self.assertEqual(calls[task.task_id], (mode, {"bili_tid": 171}))
                self.assertEqual(self.service.task(task.task_id).status, "queued_download")
            count = add.call_count
            self.assertEqual(self.service.retry_all("retry-batch-01"), result)
            self.assertEqual(add.call_count, count)
        for task in untouched:
            self.assertEqual(self.service.task(task.task_id).status, task.status)

    def test_partial_retry_restores_only_unfinished_targets_and_continues_after_missing_asset(self):
        partial = self.task("partial_success")
        media = Path(self.tmp.name) / "prepared.mp4"
        media.write_bytes(b"prepared")
        self.service.store.update(partial.task_id, video_path=str(media))
        success = self.publication(partial, "bilibili", "submitted")
        failed = self.publication(partial, "douyin", "failed")
        cancelled = self.publication(partial, "youtube", "cancelled")
        unknown = self.publication(partial, "acfun", "submission_unknown")
        missing = self.task("partial_success")
        self.publication(missing, "bilibili", "submitted")
        missing_target = self.publication(missing, "douyin", "failed")
        with patch.object(self.service.scheduler, "add") as add:
            result = self.service.retry_all("retry-partial-01")
            add.assert_not_called()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"][0]["task_id"], missing.task_id)
        from screator import publications
        for identity, status in ((success, "submitted"), (failed, "ready"), (cancelled, "ready"), (unknown, "submission_unknown"), (missing_target, "failed")):
            self.assertEqual(publications.get(self.service.store, identity)["status"], status)

    def test_cancel_partial_preserves_successful_and_inflight_targets(self):
        task = self.task("partial_success")
        success = self.publication(task, "bilibili", "submitted")
        pending = self.publication(task, "douyin", "ready")
        inflight = self.publication(task, "youtube", "creating")
        self.assertEqual(self.service.cancel_all("cancel-partial-01")["processed"], 1)
        from screator import publications
        self.assertEqual(publications.get(self.service.store, success)["status"], "submitted")
        self.assertEqual(publications.get(self.service.store, pending)["status"], "cancelled")
        self.assertEqual(publications.get(self.service.store, inflight)["status"], "creating")

    def test_batch_requires_operation_id_and_rejects_work_during_shutdown(self):
        with self.assertRaises(AppError):
            self.service.retry_all("short")
        self.service.scheduler.closing = True
        with self.assertRaises(AppError):
            self.service.cancel_all("cancel-closing-01")
