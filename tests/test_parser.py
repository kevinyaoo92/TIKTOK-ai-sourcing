# -*- coding: utf-8 -*-
"""解析器测试：用与真实导出同构的临时文件（含元信息行、第4行表头、泰语关键词）。

覆盖：元信息/周期识别、表头映射、数值清洗、自然键去重(重复导入跳过)、历史多周期保留。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.database import KeywordRepo, connect, init_db
from app.tiktok.collector import import_export_file
from app.tiktok.parser import parse_export_file


def _write_xlsx(path: Path, rows: list[list]):
    """rows 为原始单元格矩阵（含空单元格），无表头写出。"""
    df = pd.DataFrame(rows)
    df.to_excel(path, index=False, header=False)


def _export_rows(month_range: str) -> list[list]:
    """构造与真实导出同构的矩阵：2 行元信息 + 空行 + 表头(第4行) + 3 词数据。"""
    return [
        [f"[日期范围]: {month_range}\n"],
        ["[类目]: 时尚配件"],
        [None],
        ["关键词", "搜索量", "商品点击数", "SKU 销售指数", "在售商品",
         "平均价格 (฿)", "CTR 指数", "CTOR 评分"],
        ["พวงกุญแจ", 7465.41, 4995.71, 1421.54, 13039, 1.61, 22.87, 5.87],
        ["6767", 6481.53, 4384.30, 118.75, 13, 74.38, 22.95, 4.38],
        ["สร้อยคอ", 5699.31, 4243.11, 1304.09, 10146, 2.24, 25.83, 6.25],
    ]


def test_parse_real_shaped_export(tmp_path):
    f = tmp_path / "Top_Search_Term_Rank_List_20260907023548.xlsx"
    _write_xlsx(f, _export_rows("2026-07-01 ~ 2026-07-31"))

    parsed = parse_export_file(f, market="TH")

    assert parsed["period_start"] == "2026-07-01"
    assert parsed["period_end"] == "2026-07-31"
    assert parsed["period_type"] == "month"          # 整月 → month
    assert parsed["category"] == "时尚配件"
    assert parsed["keyword_type"] == "热门搜索关键词"  # 文件名前缀识别
    assert parsed["n_records"] == 3
    assert parsed["source_file"] == f.name

    kw = parsed["records"][0]
    assert kw.keyword == "พวงกุญแจ"
    assert kw.search_volume == 7465.41
    assert kw.on_sale_products == 13039             # 整数列
    assert kw.avg_price == 1.61                     # 泰国站 ฿ 原值
    assert kw.rank == 1
    # 数字型关键词原样转字符串
    assert parsed["records"][1].keyword == "6767"
    # 原始文件未被改动
    assert f.read_bytes().startswith(b"PK")


def test_import_idempotent_and_history_preserved(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    init_db(conn)
    repo = KeywordRepo(conn)

    f1 = tmp_path / "Top_Search_Term_Rank_List_20260907000000.xlsx"
    _write_xlsx(f1, _export_rows("2026-06-01 ~ 2026-06-30"))  # 上月快照
    f2 = tmp_path / "Top_Search_Term_Rank_List_20260907010000.xlsx"
    _write_xlsx(f2, _export_rows("2026-07-01 ~ 2026-07-31"))  # 本月快照

    r1 = import_export_file(f1, db)
    assert r1["inserted"] == 3 and r1["skipped_duplicates"] == 0

    r1b = import_export_file(f1, db)  # 同一文件重复导入 → 全跳过
    assert r1b["inserted"] == 0 and r1b["skipped_duplicates"] == 3

    r2 = import_export_file(f2, db)
    assert r2["inserted"] == 3

    # 两个周期都保留（历史不覆盖）
    periods = repo.snapshot_periods("TH", "时尚配件", "热门搜索关键词", "month")
    assert periods == ["2026-06-01", "2026-07-01"]

    # 环比前一期可定位
    prev = repo.previous_period("TH", "时尚配件", "热门搜索关键词", "month", "2026-07-01")
    assert prev == "2026-06-01"
    conn.close()


def test_week_range_detected(tmp_path):
    f = tmp_path / "x.xlsx"
    _write_xlsx(f, _export_rows("2026-07-06 ~ 2026-07-12"))  # 周快照
    parsed = parse_export_file(f)
    assert parsed["period_type"] == "week"
    assert parsed["period_start"] == "2026-07-06"


def test_csv_supported(tmp_path):
    f = tmp_path / "export.csv"
    lines = ["[日期范围]: 2026-07-01 ~ 2026-07-31",
             "[类目]: 时尚配件", "",
             "关键词,搜索量,商品点击数,SKU 销售指数,在售商品,平均价格 (฿),CTR 指数,CTOR 评分",
             "พวงกุญแจ,100,80,50,200,2.5,20,5"]
    f.write_text("\n".join(lines), encoding="utf-8-sig")
    parsed = parse_export_file(f)
    assert parsed["n_records"] == 1
    assert parsed["records"][0].search_volume == 100
