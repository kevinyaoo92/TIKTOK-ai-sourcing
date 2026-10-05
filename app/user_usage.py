# -*- coding: utf-8 -*-
"""用户免费额度：匿名 UUID 计数 + 扣减。

额度定义：
- find: 找货 10 次
- collect: 采集 5 次
两者独立计数。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

QUOTA = {
    "find": 10,
    "collect": 5,
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS user_usage (
            uuid TEXT NOT NULL,
            action TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (uuid, action)
        )
        """)
        con.commit()
    finally:
        con.close()


def check_and_decrement(db_path: Path, uuid: str, action: str) -> dict:
    """检查并扣减一次额度。返回 {ok, used, limit, remaining} 或 {ok: False, reason}。"""
    if not uuid:
        return {"ok": False, "reason": "missing_uuid"}
    if action not in QUOTA:
        return {"ok": False, "reason": "unknown_action"}

    limit = QUOTA[action]
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        row = cur.execute(
            "SELECT count FROM user_usage WHERE uuid=? AND action=?",
            (uuid, action),
        ).fetchone()
        used = int(row[0]) if row else 0

        if used >= limit:
            return {"ok": False, "reason": "quota_exceeded",
                    "used": used, "limit": limit, "remaining": 0}

        now = _now()
        if row:
            cur.execute(
                "UPDATE user_usage SET count=count+1, updated_at=? WHERE uuid=? AND action=?",
                (now, uuid, action),
            )
        else:
            cur.execute(
                "INSERT INTO user_usage (uuid, action, count, created_at, updated_at) VALUES (?,?,?,?,?)",
                (uuid, action, 1, now, now),
            )
        con.commit()

        used_new = used + 1
        return {"ok": True, "used": used_new, "limit": limit,
                "remaining": limit - used_new}
    finally:
        con.close()