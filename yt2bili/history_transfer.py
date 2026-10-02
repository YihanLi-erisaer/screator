"""Portable, read-only publishing history without assets or credentials."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from yt2bili.db import _now
from yt2bili.exceptions import Yt2BiliError
from yt2bili import publications
from yt2bili.locking import work_lock
from yt2bili.task_paths import validate_task_paths

FORMAT = "yt2bili-publishing-history"
VERSION = 1
HISTORY_STATUSES = {"submitted", "submission_unknown", "partial_success", "completed_with_abandon"}
DELETEABLE_STATUSES = HISTORY_STATUSES | {"ready", "failed", "cancelled", "interrupted"}
PLATFORMS = {"bilibili", "douyin", "acfun"}
MAX_BYTES = 100 * 1024 * 1024
MAX_RECORDS = 100_000
TEXT_FIELDS = ("task_id", "video_id", "url", "status", "title_orig", "title_zh", "desc_orig",
               "desc_zh", "uploader", "bv_id", "created_at", "updated_at",
               "account_uid_snapshot", "account_name_snapshot")
PUBLICATION_FIELDS = ("platform", "status", "remote_id", "account_label", "text")
ID = re.compile(r"^[0-9a-fA-F-]{36}$")
BV = re.compile(r"^BV[0-9A-Za-z]{10}$")


def initialize(store):
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS imported_publishing_history(
            task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, imported_at TEXT NOT NULL)""")


def _local_record(store, task):
    values = asdict(task)
    record = {key: values.get(key) or "" for key in TEXT_FIELDS}
    record["publications"] = [
        {key: item.get(key) or "" for key in PUBLICATION_FIELDS}
        for item in publications.items(store, task.task_id)
    ]
    return record


def all_records(store):
    with store._lock:
        local = [_local_record(store, task) for task in store.list_all() if task.status in HISTORY_STATUSES]
        imported = [json.loads(row[0]) for row in store._conn.execute(
            "SELECT payload FROM imported_publishing_history ORDER BY imported_at,task_id")]
    return local + imported


def view(record):
    """A task-shaped, inert row for the existing history list and detail view."""
    return {**record, "account_id": None, "revision": 1, "error": "", "work_dir": "", "work_root": "",
            "video_path": "", "cover_path": "", "file_exists": False, "imported_history": True,
            "publications": [{**pub, "publication_id": "", "account_id": "", "revision": 0, "error": ""}
                             for pub in record["publications"]]}


def list_imported(store):
    with store._lock:
        return [view(json.loads(row[0])) for row in store._conn.execute(
            "SELECT payload FROM imported_publishing_history ORDER BY imported_at DESC,task_id")]


def get_imported(store, task_id):
    with store._lock:
        row = store._conn.execute(
            "SELECT payload FROM imported_publishing_history WHERE task_id=?", (task_id,)).fetchone()
    return view(json.loads(row[0])) if row else None


def export_file(store, path):
    target = Path(path)
    if target.suffix.lower() != ".json":
        raise Yt2BiliError("投稿记录请保存为 .json 文件。")
    records = all_records(store)
    if len(records) > MAX_RECORDS:
        raise Yt2BiliError("投稿记录超过单次导出上限，请联系维护者处理。")
    payload = {"format": FORMAT, "version": VERSION, "exported_at": _now(), "records": records}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise Yt2BiliError("投稿记录文件超过 100 MB，无法一次导出。")
    # Write beside the destination, then replace it only after the whole JSON is durable.
    import os
    import tempfile
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".history-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"exported": len(records), "path": str(target)}


def _string(value, name, limit=20_000):
    if not isinstance(value, str) or len(value) > limit:
        raise Yt2BiliError(f"投稿记录中的 {name} 格式无效。")
    return value


def _validated_record(value):
    if not isinstance(value, dict) or not isinstance(value.get("publications"), list):
        raise Yt2BiliError("投稿记录结构无效。")
    record = {key: _string(value.get(key), key) for key in TEXT_FIELDS}
    if not ID.fullmatch(record["task_id"]) or record["status"] not in HISTORY_STATUSES:
        raise Yt2BiliError("投稿记录的任务身份或状态无效。")
    if record["bv_id"] and not BV.fullmatch(record["bv_id"]):
        raise Yt2BiliError("投稿记录中的 BV 号无效。")
    items = value["publications"]
    if len(items) > 3:
        raise Yt2BiliError("单条投稿记录的平台数量无效。")
    record["publications"] = []
    for item in items:
        if not isinstance(item, dict):
            raise Yt2BiliError("平台投稿记录结构无效。")
        pub = {key: _string(item.get(key), key) for key in PUBLICATION_FIELDS}
        if pub["platform"] not in PLATFORMS:
            raise Yt2BiliError("包含不支持的投稿平台。")
        if any(p["platform"] == pub["platform"] for p in record["publications"]):
            raise Yt2BiliError("同一任务的平台投稿记录重复。")
        record["publications"].append(pub)
    return record


def _receipts(record):
    keys = {(p["platform"], p["remote_id"]) for p in record["publications"] if p["remote_id"]}
    if record["bv_id"]:
        keys.add(("bilibili", record["bv_id"]))
    return keys


def import_file(store, path):
    source = Path(path)
    if source.suffix.lower() != ".json" or not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise Yt2BiliError("请选择不超过 100 MB 的投稿记录 JSON 文件。")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Yt2BiliError("投稿记录文件不是有效的 UTF-8 JSON。") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise Yt2BiliError("不支持的投稿记录格式或版本。")
    rows = payload.get("records")
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise Yt2BiliError("投稿记录数量无效。")
    records = [_validated_record(row) for row in rows]
    identities = [row["task_id"] for row in records]
    if len(identities) != len(set(identities)):
        raise Yt2BiliError("文件中存在重复的任务身份。")
    imported = skipped = 0
    with store.transaction() as db:
        existing_ids = {row[0] for row in db.execute("SELECT task_id FROM tasks")}
        existing_ids.update(row[0] for row in db.execute("SELECT task_id FROM imported_publishing_history"))
        receipts = set()
        for row in db.execute("SELECT platform,remote_id FROM task_publications WHERE remote_id!=''"):
            receipts.add((row[0], row[1]))
        for row in db.execute("SELECT bv_id FROM tasks WHERE bv_id!=''"):
            receipts.add(("bilibili", row[0]))
        for row in db.execute("SELECT payload FROM imported_publishing_history"):
            receipts.update(_receipts(json.loads(row[0])))
        for record in records:
            if record["task_id"] in existing_ids or receipts & _receipts(record):
                skipped += 1
                continue
            db.execute("INSERT INTO imported_publishing_history VALUES(?,?,?)",
                       (record["task_id"], json.dumps(record, ensure_ascii=False), _now()))
            existing_ids.add(record["task_id"])
            receipts.update(_receipts(record))
            imported += 1
    return {"imported": imported, "skipped": skipped, "total": len(records)}


def delete_record(store, task_id, expected_revision):
    """Remove an inactive local task or imported record and its unsubmitted assets."""
    if not isinstance(task_id, str) or not task_id or type(expected_revision) is not int or expected_revision < 1:
        raise Yt2BiliError("投稿记录删除参数无效。")
    with store.transaction() as db:
        imported = db.execute("SELECT 1 FROM imported_publishing_history WHERE task_id=?", (task_id,)).fetchone()
        if imported:
            if expected_revision != 1:
                raise Yt2BiliError("投稿记录已变化，请刷新后重试。")
            db.execute("DELETE FROM imported_publishing_history WHERE task_id=?", (task_id,))
            return {"deleted": True, "kind": "imported"}
        task = store.get(task_id)
        if not task:
            raise Yt2BiliError("投稿记录不存在，请刷新列表。")
        if task.status not in DELETEABLE_STATUSES:
            raise Yt2BiliError("任务仍在处理中，请先取消并等待任务停止。")
        if task.revision != expected_revision:
            raise Yt2BiliError("投稿记录已变化，请刷新后重试。")
        job = db.execute("SELECT payload FROM desktop_jobs WHERE task_id=?", (task_id,)).fetchone()
        if job and json.loads(job[0]).get("execution_state") in {"queued", "running", "waiting"}:
            raise Yt2BiliError("任务仍在队列中，不能删除投稿记录。")
        if db.execute("SELECT 1 FROM task_publications WHERE task_id=? AND status IN ('queued','waiting','uploading_media','creating')", (task_id,)).fetchone():
            raise Yt2BiliError("仍有平台投稿正在等待或执行，不能删除记录。")
        receipts = db.execute("SELECT status,remote_id FROM task_publications WHERE task_id=?", (task_id,)).fetchall()
        unsubmitted = not task.bv_id and task.status not in {"submitted", "partial_success", "submission_unknown"} and all(
            row["status"] not in {"submitted", "submission_unknown"} and not row["remote_id"] for row in receipts)
        asset_dir = None
        if unsubmitted and task.work_dir:
            asset_dir = validate_task_paths(task)
            resolved = asset_dir.resolve()
            for other in store.list_all():
                if other.task_id != task_id and other.work_dir and Path(other.work_dir).resolve() == resolved:
                    raise Yt2BiliError("素材目录还被其他任务使用，不能删除该记录。")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "acfun_attempts" in tables:
            db.execute("DELETE FROM acfun_attempts WHERE publication_id IN (SELECT publication_id FROM task_publications WHERE task_id=?)", (task_id,))
        db.execute("DELETE FROM task_publications WHERE task_id=?", (task_id,))
        for table in ("desktop_jobs", "task_translation", "translation_attempts", "upload_attempts", "legacy_task_map"):
            if table in tables:
                db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
        if "import_conflicts" in tables:
            db.execute("DELETE FROM import_conflicts WHERE existing_task_id=?", (task_id,))
        db.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise Yt2BiliError("投稿记录引用检查失败，删除已回滚。")
        assets_deleted = bool(asset_dir and asset_dir.exists())
        if assets_deleted:
            with work_lock(asset_dir):
                try:
                    shutil.rmtree(asset_dir)
                except OSError as exc:
                    raise Yt2BiliError(f"任务素材删除失败，投稿记录已保留：{exc}") from exc
    return {"deleted": True, "kind": "local", "assets_deleted": assets_deleted}
