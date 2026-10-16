"""Daily Bilibili submissions from local and imported database history."""
from datetime import datetime, time, timedelta, timezone


def submission_trend(store, today):
    first_day = today - timedelta(days=29)
    start = datetime.combine(first_day, time.min).astimezone(timezone.utc).isoformat()
    end = datetime.combine(today + timedelta(days=1), time.min).astimezone(timezone.utc).isoformat()
    dates = [(first_day + timedelta(days=index)).isoformat() for index in range(30)]
    counts, labels = {}, {}
    with store._lock:
        accounts = {account["uid"]: account for account in store.accounts(archived=True)}
        rows = store._conn.execute("""
            WITH submissions AS (
                SELECT p.account_id, t.account_uid_snapshot AS uid,
                       t.account_name_snapshot AS name,
                       COALESCE(receipt.submitted_at, t.updated_at) AS submitted_at
                FROM task_publications p JOIN tasks t ON t.task_id=p.task_id
                LEFT JOIN (
                    SELECT task_id, MAX(ended_at) AS submitted_at
                    FROM upload_attempts WHERE outcome='submitted' GROUP BY task_id
                ) receipt ON receipt.task_id=p.task_id
                WHERE p.platform='bilibili' AND p.status='submitted'
                UNION ALL
                SELECT NULL, json_extract(h.payload,'$.account_uid_snapshot'),
                       json_extract(h.payload,'$.account_name_snapshot'),
                       COALESCE(NULLIF(json_extract(h.payload,'$.submitted_at'),''),
                                json_extract(h.payload,'$.updated_at'))
                FROM imported_publishing_history h
                WHERE EXISTS (
                    SELECT 1 FROM json_each(h.payload,'$.publications') p
                    WHERE json_extract(p.value,'$.platform')='bilibili'
                      AND json_extract(p.value,'$.status')='submitted'
                ) OR (
                    json_array_length(h.payload,'$.publications')=0
                    AND json_extract(h.payload,'$.status')='submitted'
                    AND COALESCE(json_extract(h.payload,'$.bv_id'),'')!=''
                )
            )
            SELECT * FROM submissions
            WHERE julianday(submitted_at)>=julianday(?)
              AND julianday(submitted_at)<julianday(?)
        """, (start, end))
        for row in rows:
            try:
                day = datetime.fromisoformat(row["submitted_at"]).astimezone().date()
            except (ValueError, TypeError):
                continue
            index = (day - first_day).days
            if not 0 <= index < 30:
                continue
            account = accounts.get(row["uid"])
            identity = row["account_id"] or (account["account_id"] if account else
                       "history:" + (row["uid"] or "unknown"))
            counts.setdefault(identity, [0] * 30)[index] += 1
            labels[identity] = {"uid": row["uid"] or "", "name": row["name"] or ""}
    return {"dates": dates, "by_account": counts, "account_labels": labels}
