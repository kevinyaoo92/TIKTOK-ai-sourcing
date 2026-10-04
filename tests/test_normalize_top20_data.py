# -*- coding: utf-8 -*-
"""normalize_top20_data 标准化 + 入库测试。

覆盖：
1. TXT 解析为 JSON 正确（泰文原文保留、原始值保留、numeric 转换）
2. 数值转换（K/M/B、无后缀、无法转换）
3. 类目映射正确（ALIAS 页面名）
4. 数据库写入正确（category / keyword_metric / collection_batch）
5. 不影响已有测试（由全量回归保证）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "collect"))

import normalize_top20_data as norm  # noqa: E402

SAMPLE_TXT = """TikTok 泰国｜厨具｜餐具｜热门搜索关键词
日期：2026-08-01 - 2026-08-31
==================================================

【搜索量 TOP3｜高→低】

1. กล่องข้าว — 3.03K
2. ช้อน — 14.22
3. ตะเกียบ — N/A

【CTR指数 TOP3｜高→低】

1. ช้อน — 8.5
2. กล่องข้าว — 7.2
3. ตะเกียบ — 6.1
"""


def _write_txt(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# =============================================================================
# 测试 1: TXT 解析为 JSON 正确
# =============================================================================
def test_parse_txt_json(tmp_path):
    p = _write_txt(tmp_path, "TH_厨具_餐具_月度_热门搜索关键词_2026-08-01_2026-08-31.txt", SAMPLE_TXT)
    item = norm.parse_txt(p)

    assert item["market"] == "TH"
    assert item["level1_official"] == "厨具"
    assert item["level2_official"] == "餐具"
    assert item["period_start"] == "2026-08-01"
    assert item["period_end"] == "2026-08-31"
    assert item["period"] == "2026-08"
    assert item["ranking_type"] == "热门搜索关键词"
    assert item["source_file"] == p.name
    assert item["collect_time"]

    # 两个指标块
    assert set(item["metrics"].keys()) == {"搜索量", "CTR指数"}

    # 泰文原文保留 + 原始值保留 + numeric 转换
    rec = item["metrics"]["搜索量"][0]
    assert rec["rank"] == 1
    assert rec["keyword"] == "กล่องข้าว"          # 泰文原文
    assert rec["original_value"] == "3.03K"      # 页面原始格式
    assert rec["numeric_value"] == 3030.0        # K -> 千
    assert rec["metric"] == "搜索量"

    # 无后缀数值
    rec2 = item["metrics"]["搜索量"][1]
    assert rec2["original_value"] == "14.22"
    assert rec2["numeric_value"] == 14.22

    # 无法转换 -> None（original_value 保留）
    rec3 = item["metrics"]["搜索量"][2]
    assert rec3["original_value"] == "N/A"
    assert rec3["numeric_value"] is None
    print("  ✓ 测试1: TXT 解析 JSON 正确，泰文原文/原始值/数值转换符合规则")


# =============================================================================
# 测试 2: 数值转换
# =============================================================================
def test_parse_numeric():
    cases = {
        "3.03K": 3030.0,
        "14.22": 14.22,
        "1.2M": 1200000.0,
        "5B": 5000000000.0,
        "1,234": 1234.0,
        "0": 0.0,
        "N/A": None,
        "--": None,
        "abc": None,
        "": None,
    }
    for raw, expect in cases.items():
        got = norm.parse_numeric(raw)
        if expect is None:
            assert got is None, f"{raw!r} 应为 None，实际 {got}"
        else:
            assert abs(got - expect) < 1e-6, f"{raw!r} -> {got} != {expect}"
    print("  ✓ 测试2: 数值转换正确（K/M/B、逗号、无法转换）")


# =============================================================================
# 测试 3: 类目映射正确（ALIAS 页面名）
# =============================================================================
def test_apply_mapping(tmp_path):
    mapping = {
        "results": [
            {"official_level1": "厨具", "official_level2": "餐具",
             "page_level1": "厨具", "page_level2": "餐具与器皿", "match_status": "ALIAS"},
            {"official_level1": "家居用品", "official_level2": "家居收纳用品",
             "page_level1": "家居用品", "page_level2": "家居收纳用品", "match_status": "MATCH"},
        ]
    }
    mp = tmp_path / "mapping.json"
    mp.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    idx = norm.load_mapping_index(mp)
    assert len(idx) == 2

    item = {"level1_official": "厨具", "level2_official": "餐具",
            "level1_page": "厨具", "level2_page": "餐具", "mapping_status": ""}
    norm.apply_mapping(item, idx)
    assert item["level2_page"] == "餐具与器皿"
    assert item["mapping_status"] == "ALIAS"
    print("  ✓ 测试3: 类目映射正确（ALIAS 页面名覆盖）")


# =============================================================================
# 测试 4: 数据库写入正确
# =============================================================================
def test_write_to_db(tmp_path):
    db = tmp_path / "tiktok_market.db"
    item = norm.parse_txt(_write_txt(
        tmp_path, "TH_厨具_餐具_月度_热门搜索关键词_2026-08-01_2026-08-31.txt", SAMPLE_TXT))
    norm.apply_mapping(item, {("厨具", "餐具"): {"page_level1": "厨具", "page_level2": "餐具与器皿", "match_status": "ALIAS"}})

    written = norm.write_to_db(db, [item], "norm_test", "2026-09-15T00:00:00",
                               "2026-09-15T00:00:01", success_count=1, failed_count=0)
    # 2 指标 × 3 条 = 6 行
    assert written == 6

    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        cats = conn.execute("SELECT COUNT(*) FROM category").fetchone()[0]
        assert cats == 1
        cat = conn.execute("SELECT * FROM category").fetchone()
        assert cat[2] == "厨具" and cat[3] == "餐具"
        assert cat[5] == "餐具与器皿" and cat[6] == "ALIAS"  # page_level2 / mapping_status

        rows = conn.execute("SELECT COUNT(*) FROM keyword_metric").fetchone()[0]
        assert rows == 6
        km = conn.execute("SELECT period, metric, rank, keyword, original_value, numeric_value "
                          "FROM keyword_metric WHERE rank=1 AND metric='搜索量'").fetchone()
        assert km[0] == "2026-08" and km[3] == "กล่องข้าว" and km[4] == "3.03K" and km[5] == 3030.0

        batch = conn.execute("SELECT success_count, failed_count, status FROM collection_batch").fetchone()
        assert batch[0] == 1 and batch[1] == 0 and batch[2] == "done"
    finally:
        conn.close()
    print("  ✓ 测试4: 数据库写入正确（category/keyword_metric/collection_batch）")


# =============================================================================
# 测试 5: normalize_all 全流程（JSON 文件 + 入库）
# =============================================================================
def test_normalize_all(tmp_path):
    txt_root = tmp_path / "TH"
    monthly = txt_root / "厨具" / "餐具" / "monthly"
    monthly.mkdir(parents=True)
    (monthly / "TH_厨具_餐具_月度_热门搜索关键词_2026-08-01_2026-08-31.txt").write_text(SAMPLE_TXT, encoding="utf-8")

    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"results": [
        {"official_level1": "厨具", "official_level2": "餐具",
         "page_level1": "厨具", "page_level2": "餐具与器皿", "match_status": "ALIAS"}]},
        ensure_ascii=False), encoding="utf-8")

    json_root = tmp_path / "processed" / "TH"
    db = tmp_path / "tiktok_market.db"

    stats = norm.normalize_all(txt_root, mapping, json_root, db)
    assert stats["txt_total"] == 1 and stats["success"] == 1 and stats["failed"] == 0
    assert stats["db_rows_written"] == 6

    # JSON 文件生成
    jsons = list(json_root.glob("厨具/餐具/monthly/*.json"))
    assert len(jsons) == 1
    out = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert out["level2_page"] == "餐具与器皿"
    assert out["metrics"]["搜索量"][0]["keyword"] == "กล่องข้าว"
    print("  ✓ 测试5: normalize_all 全流程正确（JSON 生成 + 入库）")
