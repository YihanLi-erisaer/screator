import tempfile
import unittest
import uuid
from contextlib import closing

from screator import publications
from screator.db import Task
from screator.desktop_service import DesktopService
from screator.exceptions import AppError
from screator.paths import AppPaths
from test_history_transfer import Vault


class TaskFilterTests(unittest.TestCase):
    def test_all_platforms_combine_with_status_search_and_pagination(self):
        with tempfile.TemporaryDirectory() as folder, closing(DesktopService(AppPaths.default(folder, folder), lambda *_: None, Vault())) as service:
            task_ids = {}
            for platform in sorted(publications.PLATFORMS):
                for index, status in enumerate(("ready", "downloaded", "failed")):
                    task = Task(task_id=str(uuid.uuid4()), video_id=f"video{len(task_ids):06d}",
                                url="", status=status, title_zh=f"{platform} task {index}")
                    service.store.upsert(task)
                    task_ids[platform, status] = task.task_id
                    with service.store.transaction() as db:
                        db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES(?,?,?,?,?)",
                                   (str(uuid.uuid4()), task.task_id, platform, task.video_id, status))
            for platform in publications.PLATFORMS:
                with self.subTest(platform=platform):
                    result = service.list_tasks(platform=platform)
                    self.assertEqual(result["total"], 3)
                    self.assertEqual(service.list_tasks(platform=platform, status="failed")["items"][0]["task_id"],
                                     task_ids[platform, "failed"])
                    self.assertEqual(service.list_tasks(platform=platform, search=f"{platform.upper()} TASK 1")["total"], 1)
                    video_id = service.store.require(task_ids[platform, "ready"]).video_id
                    self.assertEqual(service.list_tasks(platform=platform, status="ready", search=video_id)["total"], 1)
            # A shared task belongs to both platform tabs and appears once in each.
            shared = service.store.require(task_ids["acfun", "ready"])
            with service.store.transaction() as db:
                db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES(?,?,?,?,?)",
                           (str(uuid.uuid4()), shared.task_id, "youtube", shared.video_id, "ready"))
            self.assertEqual(service.list_tasks(platform="youtube")["total"], 4)
            self.assertEqual(service.list_tasks(platform="acfun")["total"], 3)
            for index in range(22):
                task = Task(task_id=str(uuid.uuid4()), video_id=f"extra{index:06d}", url="", status="ready")
                service.store.upsert(task)
                with service.store.transaction() as db:
                    db.execute("INSERT INTO task_publications(publication_id,task_id,platform,source_video_id,status) VALUES(?,?,?,?,?)",
                               (str(uuid.uuid4()), task.task_id, "acfun", task.video_id, "ready"))
            first = service.list_tasks(platform="acfun")
            second = service.list_tasks(platform="acfun", offset=20)
            self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (25, 20, 5))
            self.assertFalse({row["task_id"] for row in first["items"]} & {row["task_id"] for row in second["items"]})
            self.assertEqual(service.list_tasks(platform="acfun", status="ready")["total"], 23)
            with self.assertRaises(AppError):
                service.list_tasks(platform="invalid")
