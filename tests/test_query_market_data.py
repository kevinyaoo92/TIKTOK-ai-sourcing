# -*- coding: utf-8 -*-
"""query_market_data 查询接口测试。

覆盖：
1. top20 查询（指定类目+月份+指标）
2. keyword 查询（关键词关联六指标）
3. history 查询（类目历史月份变化）
4. market 别名（泰国 -> TH）
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "collect"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "query"))

import normalize_top20_data as norm  # noqa: E402
import query_market_data as qmd  # noqa: E402

TXT_A = """TikTok 泰国｜家居用品｜卫浴用品｜热门搜索关键词
日期：2026-08-01 - 2026-08-31
==================================================

【搜索量 TOP3｜高→低】

1. ผ้าขนหนูเช็ดตัว — 3.03K
2. แปรงขัดห้องน้ำ — 2.94K
3. ผ้าขนหนู — 2.31K

【CTOR评分 TOP3｜高→低】

1. ผ้าขนหนูเช็ดตัว — 9.5
2. ผ้าขนหนู — 8.8
3. แปรงขัดห้องน้ำ — 8.1
"""

TXT_B = """TikTok 泰国｜家居用品｜卫浴用品｜热门搜索关键词
日期：2026-09-01 - 2026-09-30
==================================================

【搜索量 TOP2｜高→低】

1. ผ้าขนหนูเช็ดตัว — 3.50K
2. ผ้าขนหนู — 2.10K

【CTOR评分 TOP2｜高→低】

1. ผ้าขนหนูเช็ดตัว — 10.1
2. ผ้าขนหนู — 9.0
"""


def _build_db(tmp_path) -> Path:
    """构造小数据库：家居用品/卫浴用品 2026-08、2026-09 两个月份。"""
    txt_root = tmp_path / "TH"
    for name, txt in [("A", TXT_A), ("B", TXT_B)]:
        monthly = txt_root / "家居用品" / "卫浴用品" / "monthly"
        monthly.mkdir(parents=True, exist_ok=True)
        suffix = "2026-08-01_2026-08-31" if name == "A" else "2026-09-01_2026-09-30"
        (monthly / f"TH_家居用品_卫浴用品_月度_热门搜索关键词_{suffix}.txt").write_text(txt, encoding="utf-8")

    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"results": [
        {"official_level1": "家居用品", "official_level2": "卫浴用品",
         "page_level1": "家居用品", "page_level2": "卫浴用品", "match_status": "MATCH"}]},
        ensure_ascii=False), encoding="utf-8")

    db = tmp_path / "tiktok_market.db"
    norm.normalize_all(txt_root, mapping, tmp_path / "processed", db)
    return db


# =============================================================================
# 测试 1: top20 查询
# =============================================================================
def test_query_top20(tmp_path):
    db = _build_db(tmp_path)
    data = qmd.query_top20(db, "家居用品", "卫浴用品", "2026-08", "搜索量")
    assert len(data) == 3
    assert data[0]["rank"] == 1
    assert data[0]["keyword"] == "ผ้าขนหนูเช็ดตัว"
    assert data[0]["original_value"] == "3.03K"
    assert data[0]["numeric_value"] == 3030.0
    assert data[2]["rank"] == 3

    # market 别名
    data2 = qmd.query_top20(db, "家居用品", "卫浴用品", "2026-08", "搜索量", market="泰国")
    assert len(data2) == 3
    print("  ✓ 测试1: top20 查询正确（含 market 别名）")


# =============================================================================
# 测试 2: keyword 查询（关联六指标）
# =============================================================================
def test_query_keyword(tmp_path):
    db = _build_db(tmp_path)
    data = qmd.query_keyword(db, "ผ้าขนหนูเช็ดตัว")
    # 该关键词在 2 个月 × 2 指标 = 4 条
    assert len(data) == 4
    metrics = sorted({d["metric"] for d in data})
    assert metrics == ["CTOR评分", "搜索量"]
    periods = sorted({d["period"] for d in data})
    assert periods == ["2026-08", "2026-09"]

    # 指定月份过滤
    data2 = qmd.query_keyword(db, "ผ้าขนหนูเช็ดตัว", period="2026-08")
    assert len(data2) == 2
    assert all(d["period"] == "2026-08" for d in data2)
    print("  ✓ 测试2: keyword 查询正确（六指标关联 + 月份过滤）")


# =============================================================================
# 测试 3: history 查询（类目历史月份变化）
# =============================================================================
def test_query_history(tmp_path):
    db = _build_db(tmp_path)
    data = qmd.query_history(db, "家居用品", "卫浴用品")
    # 2 个月 × 2 指标 = 4 行汇总
    assert len(data) == 4
    rows = {(d["period"], d["metric"]): d for d in data}
    assert ("2026-08", "搜索量") in rows
    assert rows[("2026-08", "搜索量")]["cnt"] == 3
    assert rows[("2026-08", "搜索量")]["top1_keyword"] == "ผ้าขนหนูเช็ดตัว"

    # 指定指标
    data2 = qmd.query_history(db, "家居用品", "卫浴用品", metric="CTOR评分")
    assert len(data2) == 2
    assert all(d["metric"] == "CTOR评分" for d in data2)
    print("  ✓ 测试3: history 查询正确（历史月份变化汇总 + 指标过滤）")


# =============================================================================
# 测试 4: CLI --json 输出可解析
# =============================================================================
def test_cli_json(tmp_path, capsys):
    db = _build_db(tmp_path)
    rc = qmd.main(["top20", "--db", str(db), "--json", "--level1", "家居用品",
                   "--level2", "卫浴用品", "--period", "2026-08", "--metric", "搜索量"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert len(parsed) == 3
    assert parsed[0]["keyword"] == "ผ้าขนหนูเช็ดตัว"
    print("  ✓ 测试4: CLI --json 输出正确")
