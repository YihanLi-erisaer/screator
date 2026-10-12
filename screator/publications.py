"""Durable per-platform outcomes. Parent status is a projection, never a receipt."""
import json
import uuid
import re

from screator.db import _now
from screator.exceptions import AppError

TERMINAL = {"submitted", "abandoned"}
INFLIGHT = {"uploading_media", "creating"}
PLATFORMS = {"bilibili", "douyin", "acfun", "youtube", "twitter", "facebook", "tiktok", "xiaohongshu"}


def migrate_xiaohongshu(store):
    """Preserve every existing platform, index, trigger and attempt reference."""
    with store._lock:
        store._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with store.transaction() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS xiaohongshu_accounts(
                    account_id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, nickname TEXT NOT NULL,
                    lifecycle TEXT NOT NULL DEFAULT 'active', auth_state TEXT NOT NULL DEFAULT 'valid',
                    binding_revision INTEGER NOT NULL DEFAULT 1)""")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_xiaohongshu_slot ON xiaohongshu_accounts(lifecycle) WHERE lifecycle='active'")
                db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_xiaohongshu_identity BEFORE UPDATE OF user_id ON xiaohongshu_accounts
                    BEGIN SELECT RAISE(ABORT,'ACCOUNT_IDENTITY_IMMUTABLE'); END""")
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name='task_publications'").fetchone()[0]
                if "'xiaohongshu'" not in schema:
                    objects = list(db.execute("SELECT type,name,sql FROM sqlite_master WHERE tbl_name='task_publications' AND type IN ('index','trigger') AND sql IS NOT NULL"))
                    replacement = re.sub(r'CREATE TABLE\s+"?task_publications"?', 'CREATE TABLE task_publications_v11', schema, count=1)
                    replacement = re.sub(r"(CHECK\s*\(\s*platform\s+IN\s*\([^)]*)(\)\s*\))", r"\1,'xiaohongshu'\2", replacement, count=1)
                    db.execute(replacement)
                    db.execute("INSERT INTO task_publications_v11 SELECT * FROM task_publications")
                    db.execute("DROP TABLE task_publications")
                    db.execute("ALTER TABLE task_publications_v11 RENAME TO task_publications")
                    for _, _, sql in objects: db.execute(sql)
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_xiaohongshu_source ON task_publications(account_id,source_video_id) WHERE platform='xiaohongshu'")
                db.execute("""CREATE TABLE IF NOT EXISTS xiaohongshu_attempts(
                    publication_id TEXT PRIMARY KEY REFERENCES task_publications(publication_id),
                    phase TEXT NOT NULL, remote_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""")
                if db.execute("PRAGMA foreign_key_check").fetchone(): raise AppError("小红书迁移引用检查失败，请恢复数据库备份。")
                db.execute("PRAGMA user_version=11")
                db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(11,?)", (_now(),))
        finally:
            store._conn.execute("PRAGMA foreign_keys=ON")


def migrate_social(store):
    """Extend the existing platform constraint without losing attempt references."""
    with store._lock:
        store._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with store.transaction() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS social_accounts(
                    account_id TEXT PRIMARY KEY, platform TEXT NOT NULL CHECK(platform IN ('twitter','facebook','tiktok')),
                    subject_id TEXT NOT NULL, nickname TEXT NOT NULL,
                    lifecycle TEXT NOT NULL DEFAULT 'active', auth_state TEXT NOT NULL DEFAULT 'valid',
                    binding_revision INTEGER NOT NULL DEFAULT 1, UNIQUE(platform,subject_id))""")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_social_slot ON social_accounts(platform) WHERE lifecycle='active'")
                db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_social_identity BEFORE UPDATE OF platform,subject_id ON social_accounts
                    BEGIN SELECT RAISE(ABORT,'ACCOUNT_IDENTITY_IMMUTABLE'); END""")
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name='task_publications'").fetchone()[0]
                if "'tiktok'" not in schema:
                    trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='immutable_publication_identity'").fetchone()[0]
                    replacement = re.sub(r'CREATE TABLE\s+"?task_publications"?', 'CREATE TABLE task_publications_v10', schema, count=1)
                    replacement = re.sub(r"CHECK\s*\(\s*platform\s+IN\s*\([^)]*\)\s*\)", "CHECK(platform IN ('bilibili','douyin','acfun','youtube','twitter','facebook','tiktok'))", replacement, count=1)
                    db.execute(replacement)
                    db.execute("INSERT INTO task_publications_v10 SELECT * FROM task_publications")
                    db.execute("DROP TRIGGER immutable_publication_identity")
                    db.execute("DROP TABLE task_publications")
                    db.execute("ALTER TABLE task_publications_v10 RENAME TO task_publications")
                    db.execute(trigger)
                    for platform in PLATFORMS - {"bilibili"}:
                        db.execute(f"CREATE UNIQUE INDEX uq_{platform}_source ON task_publications(account_id,source_video_id) WHERE platform='{platform}'")
                db.execute("""CREATE TABLE IF NOT EXISTS social_upload_attempts(
                    publication_id TEXT PRIMARY KEY REFERENCES task_publications(publication_id),
                    phase TEXT NOT NULL, media_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    media_id TEXT NOT NULL DEFAULT '', remote_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""")
                if db.execute("PRAGMA foreign_key_check").fetchone(): raise AppError("多平台迁移引用检查失败，请恢复数据库备份。")
                db.execute("PRAGMA user_version=10")
                db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(10,?)", (_now(),))
        finally:
            store._conn.execute("PRAGMA foreign_keys=ON")


def migrate(store):
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS douyin_accounts(
            account_id TEXT PRIMARY KEY, client_key TEXT NOT NULL, open_id TEXT NOT NULL,
            nickname TEXT NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'active',
            auth_state TEXT NOT NULL DEFAULT 'valid', binding_revision INTEGER NOT NULL DEFAULT 1,
            UNIQUE(client_key,open_id))""")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_douyin_slot ON douyin_accounts(lifecycle) WHERE lifecycle='active'")
        db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_douyin_identity BEFORE UPDATE OF client_key,open_id ON douyin_accounts
            BEGIN SELECT RAISE(ABORT,'ACCOUNT_IDENTITY_IMMUTABLE'); END""")
        db.execute("""CREATE TABLE IF NOT EXISTS task_publications(
            publication_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
            platform TEXT NOT NULL CHECK(platform IN ('bilibili','douyin')), account_id TEXT,
            source_video_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_assets',
            revision INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
            remote_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            retain_assets INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}',
            UNIQUE(task_id,platform))""")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_douyin_source ON task_publications(account_id,source_video_id) WHERE platform='douyin'")
        db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_publication_identity
            BEFORE UPDATE OF platform,account_id,source_video_id,task_id ON task_publications
            WHEN OLD.platform IS NOT NEW.platform OR OLD.source_video_id IS NOT NEW.source_video_id
                OR OLD.task_id IS NOT NEW.task_id OR (OLD.account_id IS NOT NULL AND OLD.account_id IS NOT NEW.account_id)
            BEGIN SELECT RAISE(ABORT,'PUBLICATION_IDENTITY_IMMUTABLE'); END""")
        for task in store.list_all():
            ensure_bili(store, task)
        db.execute("PRAGMA user_version=4")
        db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(4,?)", (_now(),))


def migrate_acfun(store):
    """Rebuild the platform CHECK without changing any existing publication identity."""
    with store.transaction() as db:
        schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='task_publications'").fetchone()
        if schema and "'acfun'" in schema[0]:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='acfun_accounts'").fetchone():
                raise AppError("AcFun 表结构不完整，请从迁移备份恢复。")
            db.execute("PRAGMA user_version=5")
            db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(5,?)", (_now(),))
            return
        db.execute("""CREATE TABLE IF NOT EXISTS acfun_accounts(
            account_id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE,
            nickname TEXT NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'active',
            auth_state TEXT NOT NULL DEFAULT 'valid', binding_revision INTEGER NOT NULL DEFAULT 1,
            adapter_version TEXT NOT NULL DEFAULT 'web-v1')""")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_acfun_slot ON acfun_accounts(lifecycle) WHERE lifecycle='active'")
        db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_acfun_identity BEFORE UPDATE OF user_id ON acfun_accounts
            BEGIN SELECT RAISE(ABORT,'ACCOUNT_IDENTITY_IMMUTABLE'); END""")
        before = db.execute("SELECT count(*) FROM task_publications").fetchone()[0]
        db.execute("DROP TRIGGER IF EXISTS immutable_publication_identity")
        db.execute("""CREATE TABLE task_publications_v5(
            publication_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
            platform TEXT NOT NULL CHECK(platform IN ('bilibili','douyin','acfun')), account_id TEXT,
            source_video_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_assets',
            revision INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
            remote_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            retain_assets INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}',
            UNIQUE(task_id,platform))""")
        db.execute("INSERT INTO task_publications_v5 SELECT * FROM task_publications")
        if db.execute("SELECT count(*) FROM task_publications_v5").fetchone()[0] != before:
            raise AppError("AcFun 迁移前后投稿记录数量不一致。")
        db.execute("DROP TABLE task_publications")
        db.execute("ALTER TABLE task_publications_v5 RENAME TO task_publications")
        db.execute("CREATE UNIQUE INDEX uq_douyin_source ON task_publications(account_id,source_video_id) WHERE platform='douyin'")
        db.execute("CREATE UNIQUE INDEX uq_acfun_source ON task_publications(account_id,source_video_id) WHERE platform='acfun'")
        db.execute("""CREATE TRIGGER immutable_publication_identity
            BEFORE UPDATE OF platform,account_id,source_video_id,task_id ON task_publications
            WHEN OLD.platform IS NOT NEW.platform OR OLD.source_video_id IS NOT NEW.source_video_id
                OR OLD.task_id IS NOT NEW.task_id OR (OLD.account_id IS NOT NULL AND OLD.account_id IS NOT NEW.account_id)
            BEGIN SELECT RAISE(ABORT,'PUBLICATION_IDENTITY_IMMUTABLE'); END""")
        db.execute("""CREATE TABLE IF NOT EXISTS acfun_attempts(
            attempt_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL REFERENCES task_publications(publication_id),
            phase TEXT NOT NULL, started_at TEXT NOT NULL, upload_task_id TEXT NOT NULL DEFAULT '',
            video_id TEXT NOT NULL DEFAULT '', create_intent_at TEXT NOT NULL DEFAULT '',
            douga_id TEXT NOT NULL DEFAULT '', media_sha256 TEXT NOT NULL DEFAULT '',
            request_sha256 TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '')""")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise AppError("AcFun 迁移引用检查失败。")
        db.execute("PRAGMA user_version=5")
        db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(5,?)", (_now(),))


def migrate_youtube(store):
    """Rebuild the CHECK while preserving AcFun attempt references."""
    with store.transaction() as db:
        schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='task_publications'").fetchone()
        if schema and "'youtube'" in schema[0]:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"youtube_accounts", "youtube_attempts"} <= tables:
                raise AppError("YouTube 表结构不完整，请从迁移备份恢复。")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise AppError("YouTube 迁移引用检查失败，请恢复数据库备份。")
            db.execute("PRAGMA user_version=9")
            db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(9,?)", (_now(),))
            return
    with store._lock:
        store._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with store.transaction() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS youtube_accounts(
                    account_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'active',
                    auth_state TEXT NOT NULL DEFAULT 'valid', binding_revision INTEGER NOT NULL DEFAULT 1)""")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_youtube_slot ON youtube_accounts(lifecycle) WHERE lifecycle='active'")
                db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_youtube_identity BEFORE UPDATE OF channel_id ON youtube_accounts
                    BEGIN SELECT RAISE(ABORT,'ACCOUNT_IDENTITY_IMMUTABLE'); END""")
                db.execute("DROP TRIGGER IF EXISTS immutable_publication_identity")
                db.execute("""CREATE TABLE task_publications_v9(
                    publication_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    platform TEXT NOT NULL CHECK(platform IN ('bilibili','douyin','acfun','youtube')), account_id TEXT,
                    source_video_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_assets',
                    revision INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
                    remote_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    retain_assets INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(task_id,platform))""")
                db.execute("INSERT INTO task_publications_v9 SELECT * FROM task_publications")
                db.execute("DROP TABLE task_publications")
                db.execute("ALTER TABLE task_publications_v9 RENAME TO task_publications")
                for platform in ("douyin", "acfun", "youtube"):
                    db.execute(f"CREATE UNIQUE INDEX uq_{platform}_source ON task_publications(account_id,source_video_id) WHERE platform='{platform}'")
                db.execute("""CREATE TRIGGER immutable_publication_identity
                    BEFORE UPDATE OF platform,account_id,source_video_id,task_id ON task_publications
                    WHEN OLD.platform IS NOT NEW.platform OR OLD.source_video_id IS NOT NEW.source_video_id
                        OR OLD.task_id IS NOT NEW.task_id OR (OLD.account_id IS NOT NULL AND OLD.account_id IS NOT NEW.account_id)
                    BEGIN SELECT RAISE(ABORT,'PUBLICATION_IDENTITY_IMMUTABLE'); END""")
                db.execute("""CREATE TABLE IF NOT EXISTS youtube_attempts(
                    publication_id TEXT PRIMARY KEY REFERENCES task_publications(publication_id),
                    phase TEXT NOT NULL, media_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    remote_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise AppError("YouTube 迁移引用检查失败，请恢复数据库备份。")
                db.execute("PRAGMA user_version=9")
                db.execute("INSERT OR REPLACE INTO schema_migrations VALUES(9,?)", (_now(),))
        finally:
            store._conn.execute("PRAGMA foreign_keys=ON")


def items(store, task_id):
    with store._lock:
        task = store.require(task_id)
        result = [dict(r) for r in store._conn.execute("SELECT * FROM task_publications WHERE task_id=? ORDER BY platform", (task.task_id,))]
        for p in result:
            snapshot = json.loads(p["snapshot"])
            p["account_label"] = task.account_name_snapshot if p["platform"] == "bilibili" else snapshot.get("nickname", p["account_id"])
        return result


def get(store, publication_id):
    with store._lock:
        row = store._conn.execute("SELECT * FROM task_publications WHERE publication_id=?", (publication_id,)).fetchone()
        if not row:
            raise AppError("投稿目标不存在。")
        return dict(row)


def ensure_bili(store, task):
    with store.transaction() as db:
        status = task.status if task.status in {"ready", "submitted", "submission_unknown", "failed", "cancelled", "interrupted"} else "pending_assets"
        if task.status == "uploading": status = "submission_unknown"
        db.execute("INSERT OR IGNORE INTO task_publications(publication_id,task_id,platform,account_id,source_video_id,status,remote_id) VALUES(?,?,'bilibili',?,?,?,?)",
                   (str(uuid.uuid4()), task.task_id, task.account_id, task.video_id, status, task.bv_id))


def dual(store, task_id):
    return multi_target(store, task_id)


def multi_target(store, task_id):
    return len(items(store, task_id)) > 1


def change(store, publication_id, **values):
    if set(values) - {"status", "text", "remote_id", "error", "snapshot", "retain_assets"}:
        raise ValueError("Unsupported publication field")
    with store.transaction() as db:
        p = get(store, publication_id)
        db.execute("UPDATE task_publications SET " + ",".join(f"{k}=?" for k in values) + ",revision=revision+1 WHERE publication_id=?", (*values.values(), publication_id))
        store._events.append(("publication.changed", {"task_id": p["task_id"], "publication_id": publication_id}))


def for_platform(store, task_id, platform):
    return next((p for p in items(store, task_id) if p["platform"] == platform), None)


def project(store, task_id):
    with store.transaction():
        pubs = items(store, task_id)
        if not pubs: return
        states = {p["status"] for p in pubs}
        if "submission_unknown" in states: status = "submission_unknown"
        elif states <= TERMINAL: status = "submitted" if states == {"submitted"} else "completed_with_abandon"
        elif "submitted" in states: status = "partial_success"
        elif states & INFLIGHT: status = "uploading"
        elif states & {"queued", "waiting"}: status = "queued_upload"
        elif states <= {"ready", "blocked_validation"} and "ready" in states: status = "ready"
        elif "failed" in states or "blocked_validation" in states: status = "failed"
        elif "interrupted" in states: status = "interrupted"
        elif "cancelled" in states: status = "cancelled"
        else: return
        task = store.require(task_id)
        error = "；".join(p["platform"] + ": " + p["error"] for p in pubs if p["error"])
        if task.status == status and task.error == error and not task.cancel_requested:
            return
        store._conn.execute("UPDATE tasks SET cancel_requested=0 WHERE task_id=?", (task.task_id,))
        store.update(task_id, status=status, error=error)


def can_cleanup(store, task_id):
    if store.require(task_id).source_kind != "youtube": return False
    pubs = items(store, task_id)
    return bool(pubs) and all(p["status"] in TERMINAL and not p["retain_assets"] for p in pubs)


def prepare(store, task_id):
    task = store.require(task_id)
    for p in items(store, task_id):
        if p["status"] in TERMINAL | {"submission_unknown"}: continue
        values = {"status": "ready", "error": "", "text": p["text"] or task.title_zh}
        if p["platform"] == "acfun":
            from screator.translate import clamp_title
            snapshot = json.loads(p["snapshot"])
            generated = (store.translation(task_id) or {}).get("acfun_title") or clamp_title(task.title_zh, 50)
            if not snapshot.get("title") or snapshot.get("title_source") == "generated":
                snapshot["title"] = generated
                snapshot["title_source"] = "generated"
            snapshot["description"] = task.desc_zh
            values["snapshot"] = json.dumps(snapshot, ensure_ascii=False)
        if p["platform"] == "xiaohongshu":
            snapshot = json.loads(p["snapshot"])
            snapshot["description"] = task.desc_zh
            values["text"] = snapshot.get("title", "")
            values["snapshot"] = json.dumps(snapshot, ensure_ascii=False)
        if p["platform"] in ("youtube", "twitter", "facebook", "tiktok"):
            snapshot = json.loads(p["snapshot"])
            if not snapshot.get("title") or snapshot.get("title_source") == "generated":
                snapshot.update(title=task.title_zh, title_source="generated")
            snapshot["description"] = task.desc_zh
            values["text"] = snapshot["title"]
            if p["platform"] == "tiktok": snapshot["consent"] = False
            values["snapshot"] = json.dumps(snapshot, ensure_ascii=False)
        change(store, p["publication_id"], **values)


def freeze(store, task_id, targets=None):
    task = store.require(task_id)
    selected = set(targets) if targets is not None else None
    with store.transaction():
        for p in items(store, task_id):
            if selected is not None and p["publication_id"] not in selected: continue
            if p["status"] != "ready": continue
            existing = json.loads(p["snapshot"])
            change(store, p["publication_id"], status="queued", snapshot=json.dumps({**existing,
                "title": existing.get("title", task.title_zh) if p["platform"] in ("acfun", "youtube", "twitter", "facebook", "tiktok", "xiaohongshu") else task.title_zh,
                "description": task.desc_zh, "text": p["text"],
                "account_id": p["account_id"], "metadata_revision": task.metadata_revision,
                "revision": p["revision"]}, ensure_ascii=False))
