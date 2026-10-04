# -*- coding: utf-8 -*-
"""采集管理模块测试：验证采集闭环的核心逻辑（不依赖真实浏览器）。

覆盖：
- SUCCESS          : 首次采集成功入库
- DUPLICATE        : 同周期重复采集不重复入库
- VALIDATION_FAILED: 粒度不符（热门搜索关键词要求月度，喂周数据）
- PARSE_FAILED     : 文件损坏 / 表头后无数据行
- EXPORT_FAILED    : 浏览器导出抛异常
- 血缘完整性       : collection_log 记录全部关键字段
- 历史数据保护     : 新周期不覆盖旧周期
- latest_valid 查询: 读取最近一次成功采集
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.database import connect, init_db
from app.tiktok.collection_manager import collect, latest_valid


# ---------------------------------------------------------------------------
# 辅助：构造与真实导出同构的 xlsx
# ---------------------------------------------------------------------------

def _write_xlsx(path: Path, rows: list[list]):
    """rows 为原始单元格矩阵（含空单元格），无表头写出。"""
    df = pd.DataFrame(rows)
    df.to_excel(path, index=False, header=False)


def _export_rows(month_range: str, category: str = "家居收纳") -> list[list]:
    """2 行元信息 + 空行 + 表头(第4行) + 3 词数据（与真实导出同构）。"""
    return [
        [f"[日期范围]: {month_range}\n"],
        [f"[类目]: {category}"],
        [None],
        ["关键词", "搜索量", "商品点击数", "SKU 销售指数", "在售商品",
         "平均价格 (฿)", "CTR 指数", "CTOR 评分"],
        ["พวงกุญแจ", 7465.41, 4995.71, 1421.54, 13039, 1.61, 22.87, 5.87],
        ["6767", 6481.53, 4384.30, 118.75, 13, 74.38, 22.95, 4.38],
        ["สร้อยคอ", 5699.31, 4243.11, 1304.09, 10146, 2.24, 25.83, 6.25],
    ]


def _mock_browser_result(
    xlsx_path: Path,
    period_start: str = "2026-07-01",
    period_end: str = "2026-07-31",
) -> dict:
    """模拟 run_browser_export 返回值（指向已构造好的 xlsx）。"""
    return {
        "ok": True,
        "final_file": str(xlsx_path),
        "latest_period": {
            "period_start": period_start,
            "period_end": period_end,
            "ui_month_cn": "2026年7月",
            "file_hash": "deadbeefdeadbeef",
            "n_records": 3,
        },
        "mapping_table": [{"ui_month": "2026年7月", "valid": True}],
        "available_ui_months": ["2026年7月"],
        "probed_count": 1,
    }


# ---------------------------------------------------------------------------
# SUCCESS: 首次采集成功
# ---------------------------------------------------------------------------

def test_success_first_collection(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xlsx = raw_dir / "Top_Search_Term_Rank_List_20260910000000.xlsx"
    _write_xlsx(xlsx, _export_rows("2026-07-01 ~ 2026-07-31"))

    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx),
    ):
        result = collect(
            level_1="家居用品",
            level_2="家居收纳",
            ranking_type="热门搜索关键词",
            db_path=db,
        )

    assert result["status"] == "SUCCESS"
    assert result["inserted_rows"] == 3
    assert result["period_start"] == "2026-07-01"
    assert result["period_end"] == "2026-07-31"
    assert result["source_file"] == xlsx.name
    assert result["file_hash"] is not None
    assert result["file_size_bytes"] > 0
    assert result["imported_at"] is not None

    conn = connect(db)
    init_db(conn)
    # 数据入库 3 条
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 3
    # collection_log 有一条 SUCCESS 记录
    log = conn.execute(
        "SELECT status, period_start, period_end, source_file, file_hash, "
        "n_records, inserted_rows, imported_at "
        "FROM collection_log WHERE status='SUCCESS' LIMIT 1"
    ).fetchone()
    assert log is not None
    assert log[0] == "SUCCESS"
    assert log[1] == "2026-07-01"
    assert log[2] == "2026-07-31"
    assert log[3] == xlsx.name
    assert log[4] is not None  # file_hash
    assert log[5] == 3          # n_records
    assert log[6] == 3          # inserted_rows
    assert log[7] is not None   # imported_at
    conn.close()


# ---------------------------------------------------------------------------
# DUPLICATE: 同周期重复采集
# ---------------------------------------------------------------------------

def test_duplicate_second_collection(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xlsx = raw_dir / "Top_Search_Term_Rank_List_20260910000000.xlsx"
    _write_xlsx(xlsx, _export_rows("2026-07-01 ~ 2026-07-31"))

    mock = _mock_browser_result(xlsx)
    with patch("app.tiktok.collection_manager.run_browser_export", return_value=mock):
        r1 = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )
    assert r1["status"] == "SUCCESS"

    # 第二次：同周期 → DUPLICATE，不重复入库
    with patch("app.tiktok.collection_manager.run_browser_export", return_value=mock):
        r2 = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )
    assert r2["status"] == "DUPLICATE"
    assert r2["inserted_rows"] is None  # 未执行导入

    conn = connect(db)
    init_db(conn)
    # keyword_data 仍然只有 3 条（无重复）
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 3
    # collection_log: 1 SUCCESS + 1 DUPLICATE
    logs = conn.execute("SELECT status FROM collection_log ORDER BY id").fetchall()
    assert len(logs) == 2
    assert logs[0][0] == "SUCCESS"
    assert logs[1][0] == "DUPLICATE"
    conn.close()


# ---------------------------------------------------------------------------
# VALIDATION_FAILED: 粒度不符（热门搜索关键词要求月度，喂周数据）
# ---------------------------------------------------------------------------

def test_validation_failed_wrong_granularity(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xlsx = raw_dir / "Top_Search_Term_Rank_List_20260910000000.xlsx"
    _write_xlsx(xlsx, _export_rows("2026-07-01 ~ 2026-07-07"))  # 7 天 = week

    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx, "2026-07-01", "2026-07-07"),
    ):
        result = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )

    assert result["status"] == "VALIDATION_FAILED"
    assert "粒度" in (result["error"] or "")
    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# PARSE_FAILED: 文件损坏（存在但非 xlsx）
# ---------------------------------------------------------------------------

def test_parse_failed_corrupt_file(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    bad = raw_dir / "corrupt.xlsx"
    bad.write_text("not an excel file")  # 存在但 pandas 无法读取

    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(bad),
    ):
        result = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )

    assert result["status"] == "PARSE_FAILED"
    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# PARSE_FAILED: 表头后无数据行
# ---------------------------------------------------------------------------

def test_parse_failed_no_data_rows(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xlsx = raw_dir / "empty.xlsx"
    _write_xlsx(xlsx, [
        ["[日期范围]: 2026-07-01 ~ 2026-07-31\n"],
        ["[类目]: 家居收纳"],
        [None],
        ["关键词", "搜索量", "商品点击数", "SKU 销售指数", "在售商品",
         "平均价格 (฿)", "CTR 指数", "CTOR 评分"],
        # 无数据行
    ])

    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx),
    ):
        result = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )

    assert result["status"] == "PARSE_FAILED"
    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# EXPORT_FAILED: 浏览器导出抛异常
# ---------------------------------------------------------------------------

def test_export_failed_exception(tmp_path):
    db = tmp_path / "t.db"

    def _boom(**kwargs):
        raise RuntimeError("browser crashed")

    with patch("app.tiktok.collection_manager.run_browser_export", side_effect=_boom):
        result = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )

    assert result["status"] == "EXPORT_FAILED"
    assert "browser crashed" in (result["error"] or "")
    conn = connect(db)
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 0
    # 即使导出失败，collection_log 也应记录失败原因
    log = conn.execute(
        "SELECT status, error FROM collection_log WHERE status='EXPORT_FAILED' LIMIT 1"
    ).fetchone()
    assert log is not None
    assert "browser crashed" in log[1]
    conn.close()


# ---------------------------------------------------------------------------
# 血缘完整性：collection_log 必须能追溯到全部关键字段
# ---------------------------------------------------------------------------

def test_lineage_complete(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xlsx = raw_dir / "Top_Search_Term_Rank_List_20260910000000.xlsx"
    _write_xlsx(xlsx, _export_rows("2026-07-01 ~ 2026-07-31"))

    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx),
    ):
        collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )

    conn = connect(db)
    init_db(conn)
    row = conn.execute(
        "SELECT market, level_1_category, level_2_category, ranking_type, "
        "period_granularity, period_start, period_end, source_file, "
        "file_hash, imported_at, status "
        "FROM collection_log WHERE status='SUCCESS' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == "TH"                       # market
    assert row[1] == "家居用品"                 # level_1_category
    assert row[2] == "家居收纳"                # level_2_category
    assert row[3] == "热门搜索关键词"           # ranking_type
    assert row[4] == "month"                    # period_granularity
    assert row[5] == "2026-07-01"               # period_start
    assert row[6] == "2026-07-31"               # period_end
    assert row[7] == xlsx.name                  # source_file
    assert row[8] is not None and len(row[8]) == 16  # file_hash (sha256[:16])
    assert row[9] is not None                   # imported_at
    assert row[10] == "SUCCESS"
    conn.close()


# ---------------------------------------------------------------------------
# 历史数据保护：新周期不覆盖旧周期
# ---------------------------------------------------------------------------

def test_history_preserved(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # 第一周期：2026-07
    xlsx_jul = raw_dir / "Top_Search_Term_Rank_List_202607.xlsx"
    _write_xlsx(xlsx_jul, _export_rows("2026-07-01 ~ 2026-07-31"))
    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx_jul, "2026-07-01", "2026-07-31"),
    ):
        r1 = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )
    assert r1["status"] == "SUCCESS"

    # 第二周期：2026-06（不同周期，不应覆盖）
    xlsx_jun = raw_dir / "Top_Search_Term_Rank_List_202606.xlsx"
    _write_xlsx(xlsx_jun, _export_rows("2026-06-01 ~ 2026-06-30"))
    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx_jun, "2026-06-01", "2026-06-30"),
    ):
        r2 = collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )
    assert r2["status"] == "SUCCESS"

    conn = connect(db)
    init_db(conn)
    # 两周期数据共存：3 + 3 = 6
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 6
    periods = conn.execute(
        "SELECT DISTINCT period_start FROM keyword_data ORDER BY period_start"
    ).fetchall()
    assert len(periods) == 2
    assert periods[0][0] == "2026-06-01"
    assert periods[1][0] == "2026-07-01"
    conn.close()


# ---------------------------------------------------------------------------
# latest_valid: 查询最近一次成功采集
# ---------------------------------------------------------------------------

def test_latest_valid_returns_success(tmp_path):
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xlsx = raw_dir / "Top_Search_Term_Rank_List_202607.xlsx"
    _write_xlsx(xlsx, _export_rows("2026-07-01 ~ 2026-07-31"))

    with patch(
        "app.tiktok.collection_manager.run_browser_export",
        return_value=_mock_browser_result(xlsx),
    ):
        collect(
            level_1="家居用品", level_2="家居收纳",
            ranking_type="热门搜索关键词", db_path=db,
        )

    lv = latest_valid("TH", "家居用品", "家居收纳", "热门搜索关键词", db_path=db)
    assert lv is not None
    assert lv["status"] == "SUCCESS"
    assert lv["period_start"] == "2026-07-01"
    assert lv["period_end"] == "2026-07-31"


def test_latest_valid_returns_none_when_no_success(tmp_path):
    db = tmp_path / "t.db"
    # 未采集过 → None
    lv = latest_valid("TH", "家居用品", "家居收纳", "热门搜索关键词", db_path=db)
    assert lv is None
