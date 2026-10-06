# -*- coding: utf-8 -*-
"""用户行为埋点：轻量事件记录，仅用于 Phase 2 商业验证。

事件白名单：
- category_selected    用户选择一个类目
- opportunity_view     用户进入机会列表
- opportunity_detail   用户打开某个机会详情
- search_1688_click    用户点击 1688 搜索词
- find_supplier_click  用户点击"找货"
- contact_submit       用户提交联系方式（当前无 UI，预留）

记录字段：anonymous_id / event_name / event_time / category / opportunity_id
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

ALLOWED_EVENTS = {
    "category_selected",
    "opportunity_view",
    "opportunity_detail",
    "search_1688_click",
    "find_supplier_click",
    "contact_submit",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS user_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anonymous_id TEXT NOT NULL,
            event_name TEXT NOT NULL,
            event_time TEXT NOT NULL,
            category TEXT,
            opportunity_id TEXT
        )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_events_anon ON user_events(anonymous_id)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_events_name ON user_events(event_name)"
        )
        con.commit()
    finally:
        con.close()


def record_event(db_path: Path, anonymous_id: str, event_name: str,
                 category: str = None, opportunity_id: str = None) -> dict:
    """记录一条行为事件。失败返回 {ok: False, reason}，不影响主流程。"""
    if not anonymous_id or not event_name:
        return {"ok": False, "reason": "missing_field"}
    if event_name not in ALLOWED_EVENTS:
        return {"ok": False, "reason": "unknown_event"}

    ensure_schema(db_path)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO user_events "
            "(anonymous_id, event_name, event_time, category, opportunity_id) "
            "VALUES (?,?,?,?,?)",
            (anonymous_id, event_name, _now(), category, opportunity_id),
        )
        con.commit()
        return {"ok": True}
    finally:
        con.close()