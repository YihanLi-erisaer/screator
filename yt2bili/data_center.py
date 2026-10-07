"""On-demand, bounded analytics for locally recorded platform submissions."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from yt2bili import history_transfer
from yt2bili.exceptions import Yt2BiliError

TTL_SECONDS = 300
PLATFORMS = {"bilibili", "acfun", "douyin"}
BV_ID = re.compile(r"^BV[0-9A-Za-z]{10}$")
EMPTY_METRICS = {"review_status": None, "views": None, "likes": None, "comments": None, "favorites": None}


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _get(url, *, cookies=None, params=None):
    response = requests.get(url, cookies=cookies, params=params, timeout=8,
                            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), dict):
        return None
    return body["data"]


class DataCenter:
    def __init__(self, store, accounts, get=_get, clock=time.monotonic):
        self.store, self.accounts, self.get, self.clock = store, accounts, get, clock
        self._cache = {}
        self._lock = threading.RLock()
        self._metric_readers = {"bilibili": self._bili_submission}

    def _cached(self, key, fetch):
        now = self.clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry and now - entry[0] < TTL_SECONDS:
                return entry[1]
        try:
            value = fetch()
        except (requests.RequestException, ValueError, KeyError, TypeError, Yt2BiliError):
            value = None
        with self._lock:
            self._cache[key] = (self.clock(), value)
            if len(self._cache) > 300:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                del self._cache[oldest]
        return value

    def _credentials(self, account):
        if account["auth_state"] not in ("valid", "unverified"):
            return None
        _, info = self.accounts.read(account["account_id"])
        return ({item["name"]: item["value"] for item in info["cookie_info"]["cookies"]},
                info["token_info"]["access_token"])

    def _bili_account(self, account):
        result = {"account_id": account["account_id"], "uid": account["uid"],
                  "name": account["remark"] or account["nickname"] or account["uid"],
                  "followers": None, "views": None, "publications": None}
        try:
            credentials = self._credentials(account)
        except Yt2BiliError:
            return result
        if not credentials:
            return result
        cookies, _ = credentials
        uid = account["uid"]
        def load(path, params):
            return self._cached(("account-field", account["account_id"], account["credential_revision"], path),
                                lambda: self.get("https://api.bilibili.com" + path, cookies=cookies, params=params)
                                if path != "/x/web/archives" else self.get("https://member.bilibili.com/x/web/archives", cookies=cookies, params=params))
        relation = load("/x/relation/stat", {"vmid": uid})
        upstat = load("/x/space/upstat", {"mid": uid})
        archives = load("/x/web/archives", {"status": "all", "pn": 1})
        result["followers"] = _number((relation or {}).get("follower"))
        result["views"] = _number(((upstat or {}).get("archive") or {}).get("view"))
        result["publications"] = _number(((archives or {}).get("page") or {}).get("count"))
        return result

    def _bili_submission(self, row, account_by_id):
        result = dict(EMPTY_METRICS)
        bvid = row["remote_id"]
        if not BV_ID.fullmatch(bvid):
            return result
        account = account_by_id.get(row["account_id"])
        credentials = None
        if account:
            try:
                credentials = self._credentials(account)
            except Yt2BiliError:
                pass
        if credentials:
            cookies, access_token = credentials
            owner = self._cached(("owner", row["account_id"], account["credential_revision"], bvid),
                                 lambda: self.get("https://member.bilibili.com/x/client/archive/view",
                                                  cookies=cookies, params={"access_key": access_token, "bvid": bvid}))
            archive = (owner or {}).get("archive") or {}
            if isinstance(archive, dict) and archive.get("bvid") == bvid:
                description = archive.get("state_desc")
                if isinstance(description, str) and description.strip():
                    result["review_status"] = description.strip()[:60]
                elif archive.get("state") == 0:
                    result["review_status"] = "审核通过"
                elif archive.get("state") == -2:
                    result["review_status"] = "审核未通过"
        public = self._cached(("public", bvid), lambda: self.get(
            "https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}))
        if public and public.get("bvid") == bvid and str((public.get("owner") or {}).get("mid", "")) == row["account_uid"]:
            stat = public.get("stat") or {}
            result.update(views=_number(stat.get("view")), likes=_number(stat.get("like")),
                          comments=_number(stat.get("reply")), favorites=_number(stat.get("favorite")))
        return result

    def _rows(self):
        with self.store._lock:
            rows = [dict(item) for item in self.store._conn.execute("""
                SELECT p.publication_id, p.platform, p.account_id, p.remote_id, p.status,
                       t.task_id, t.title_zh, t.title_orig, t.updated_at,
                       t.account_uid_snapshot AS account_uid,
                       CASE p.platform WHEN 'acfun' THEN COALESCE(a.nickname, '')
                           WHEN 'douyin' THEN COALESCE(d.nickname, '')
                           ELSE t.account_name_snapshot END AS account_name
                FROM task_publications p JOIN tasks t ON t.task_id=p.task_id
                LEFT JOIN acfun_accounts a ON p.platform='acfun' AND a.account_id=p.account_id
                LEFT JOIN douyin_accounts d ON p.platform='douyin' AND d.account_id=p.account_id
                WHERE p.status IN ('submitted','submission_unknown')
                ORDER BY t.updated_at DESC, p.publication_id
            """)]
            imported = history_transfer.list_imported(self.store)
        for task in imported:
            for index, pub in enumerate(task["publications"]):
                if pub["status"] not in ("submitted", "submission_unknown"):
                    continue
                rows.append({"publication_id": f"imported:{task['task_id']}:{index}",
                             "platform": pub["platform"], "account_id": "", "remote_id": pub["remote_id"],
                             "status": pub["status"], "task_id": task["task_id"],
                             "title_zh": task["title_zh"], "title_orig": task["title_orig"],
                             "updated_at": task["updated_at"], "account_uid": task["account_uid_snapshot"],
                             "account_name": pub.get("account_label") or task["account_name_snapshot"]})
        rows.sort(key=lambda item: (item["updated_at"], item["publication_id"]), reverse=True)
        return rows

    def list(self, offset=0, limit=20, platform="", account_id=""):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise Yt2BiliError("分页参数无效。")
        if platform and platform not in PLATFORMS:
            raise Yt2BiliError("平台参数无效。")
        if not isinstance(account_id, str) or len(account_id) > 100:
            raise Yt2BiliError("账号参数无效。")
        accounts = self.store.accounts()
        account_by_id = {a["account_id"]: a for a in accounts}
        rows = [row for row in self._rows() if (not platform or row["platform"] == platform)
                and (not account_id or row["account_id"] == account_id)]
        page = rows[offset:offset + limit]
        def enrich(row):
            reader = self._metric_readers.get(row["platform"])
            metrics = reader(row, account_by_id) if reader else EMPTY_METRICS
            return {**row, **metrics}
        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(enrich, page))
            summaries = list(pool.map(self._bili_account, accounts))
        return {"items": items, "total": len(rows), "accounts": summaries,
                "updated_at": time.time(), "refresh_seconds": TTL_SECONDS}
