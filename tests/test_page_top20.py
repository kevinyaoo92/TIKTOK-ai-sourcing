"""Page TOP20 批次管理 - 单元测试

测试目标：
1. 六个 ranking_field 都成功 → batch SUCCESS
2. 任意一个 ranking_field 失败 → batch FAILED
3. batch FAILED → 旧 latest_valid 不受影响
4. 同一个关键词出现在两个 ranking_field → 数据库保存两行
5. 同一个关键词只出现在一个 ranking_field → 不生成其他指标记录
6. display_value 保持原始格式
7. numeric_value 只用于校验（存在但不替代 display_value）
8. 同周期同条件重复采集 → DUPLICATE，不产生重复 batch
9. ranking_field 不能串数据
10. level_1 / level_2 / ranking_type / period 不能串数据
"""
import os
import sys
import tempfile
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import PageTop20Record
from app.tiktok.collection_manager import (
    collect_page_top20_batch,
    get_latest_valid_top20,
    TOP20_RANKING_FIELDS,
)
from app.database import connect, init_db, PageTop20Repo, CollectionLogRepo


# ---------------------------------------------------------------------------
# 辅助：构造 mock 采集函数
# ---------------------------------------------------------------------------

def _make_mock_collector(field_results: dict, period_start="2026-08-01", period_end="2026-08-31"):
    """构造一个 mock collect_fn。

    field_results: {ranking_field: [(keyword, display_value, numeric_value), ...]}
    如果某个 ranking_field 不在 dict 中，视为该字段采集失败。
    """

    def _collector(market, level_1, level_2, ranking_type, period_granularity):
        records = []
        fields_result = {}
        all_success = True

        for field in TOP20_RANKING_FIELDS:
            if field in field_results:
                rows = field_results[field]
                field_result = {"success": True, "count": len(rows),
                                "error": None, "display_values": []}
                for rank_idx, (kw, disp, num) in enumerate(rows, start=1):
                    rec = PageTop20Record(
                        market=market,
                        level_1_category=level_1,
                        level_2_category=level_2,
                        ranking_type=ranking_type,
                        period_granularity=period_granularity,
                        period_start=period_start,
                        period_end=period_end,
                        ranking_field=field,
                        rank=rank_idx,
                        keyword=kw,
                        display_value=disp,
                        numeric_value=num,
                    )
                    records.append(rec)
                    field_result["display_values"].append(disp)
                fields_result[field] = field_result
            else:
                fields_result[field] = {"success": False, "count": 0,
                                        "error": f"{field} 采集失败", "display_values": []}
                all_success = False

        return {
            "success": all_success,
            "error": None if all_success else "部分字段失败",
            "period_start": period_start,
            "period_end": period_end,
            "records": records,
            "fields_result": fields_result,
        }

    return _collector


def _make_successful_collector():
    """构造一个 6 字段全部成功的 mock 采集器。"""
    return _make_mock_collector({
        "search_volume": [
            ("关键词A", "7.47K", 7470.0),
            ("关键词B", "5.23K", 5230.0),
            ("关键词C", "3.10K", 3100.0),
        ],
        "product_clicks": [
            ("关键词D", "1.2K", 1200.0),
            ("关键词A", "980", 980.0),
            ("关键词E", "756.5", 756.5),
        ],
        "sku_sales_index": [
            ("关键词F", "962.75", 962.75),
            ("关键词B", "850.3", 850.3),
            ("关键词G", "720.0", 720.0),
        ],
        "on_sale_products": [
            ("关键词H", "12.3K", 12300.0),
            ("关键词A", "8.5K", 8500.0),
            ("关键词I", "6.7K", 6700.0),
        ],
        "ctr_index": [
            ("关键词J", "45.2", 45.2),
            ("关键词K", "38.7", 38.7),
            ("关键词A", "30.1", 30.1),
        ],
        "ctor_score": [
            ("关键词A", "3.45", 3.45),
            ("关键词L", "3.20", 3.20),
            ("关键词M", "2.98", 2.98),
        ],
    })


def _tmp_db():
    """创建一个临时数据库连接。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    init_db(conn)
    return conn, path


# =============================================================================
# 测试 1: 六个 ranking_field 都成功 → batch SUCCESS
# =============================================================================
def test_all_fields_success():
    conn, db_path = _tmp_db()
    try:
        collector = _make_successful_collector()
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "SUCCESS", f"状态应为 SUCCESS，实际: {result['status']} {result.get('error')}"
        assert result["n_records"] == 18, f"应 18 条记录 (3×6)，实际: {result['n_records']}"
        assert result["batch_id"], "batch_id 不应为空"
        assert result["period_start"] == "2026-08-01"
        assert result["period_end"] == "2026-08-31"

        # 验证 latest_valid 能查到
        latest = get_latest_valid_top20("TH", "家居用品", "家居收纳",
                                        "热门搜索关键词", "month", db_path=db_path)
        assert latest["status"] == "SUCCESS"
        assert latest["batch_id"] == result["batch_id"]
        assert latest["n_records"] == 18
        print("  ✓ 测试1: 六字段全部成功 → batch SUCCESS")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 2: 任意一个 ranking_field 失败 → batch FAILED
# =============================================================================
def test_one_field_failed():
    conn, db_path = _tmp_db()
    try:
        # 缺少 ctor_score 字段
        field_data = {
            "search_volume": [("A", "1K", 1000)],
            "product_clicks": [("B", "500", 500)],
            "sku_sales_index": [("C", "300", 300)],
            "on_sale_products": [("D", "200", 200)],
            "ctr_index": [("E", "50", 50)],
            # ctor_score 缺失 → 失败
        }
        collector = _make_mock_collector(field_data)
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "VALIDATION_FAILED", \
            f"状态应为 VALIDATION_FAILED，实际: {result['status']} {result.get('error')}"
        assert result["error"] is not None, "失败时 error 不应为空"

        # 验证数据库中没有该批次的数据
        repo = PageTop20Repo(conn)
        count = repo.count_by_batch(result["batch_id"])
        assert count == 0, f"失败批次数据应被清理，实际还有 {count} 条"

        # 验证 latest_valid 为 NO_DATA
        latest = get_latest_valid_top20("TH", "家居用品", "家居收纳",
                                        "热门搜索关键词", "month", db_path=db_path)
        assert latest["status"] == "NO_DATA", f"latest_valid 应为 NO_DATA，实际: {latest['status']}"
        print("  ✓ 测试2: 单字段失败 → batch FAILED，数据被清理")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 3: batch FAILED → 旧 latest_valid 不受影响
# =============================================================================
def test_failed_does_not_affect_old_valid():
    conn, db_path = _tmp_db()
    try:
        # 先成功一个批次（旧周期）
        old_collector = _make_mock_collector(
            {f: [(f"old_{f}_{i}", f"{i}.0K", i*1000.0) for i, f in enumerate(TOP20_RANKING_FIELDS, 1)]
             for f in TOP20_RANKING_FIELDS},
            period_start="2026-07-01", period_end="2026-07-31",
        )
        # 上面的写法太绕，直接构造简单的
        old_data = {f: [(f"old_{f}_{i}", f"{i}K", i*1000.0) for i in range(1, 4)]
                    for f in TOP20_RANKING_FIELDS}
        old_collector = _make_mock_collector(old_data, "2026-07-01", "2026-07-31")

        old_result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            old_collector, db_path=db_path,
        )
        assert old_result["status"] == "SUCCESS"
        old_batch_id = old_result["batch_id"]

        # 验证 latest_valid 是旧批次
        latest = get_latest_valid_top20("TH", "家居用品", "家居收纳",
                                        "热门搜索关键词", "month", db_path=db_path)
        assert latest["batch_id"] == old_batch_id

        # 再跑一个失败的新批次（新周期）
        failed_data = {f: [(f"new_{f}_1", "1K", 1000.0)] for f in TOP20_RANKING_FIELDS[:5]}
        # 缺一个字段
        failed_collector = _make_mock_collector(failed_data, "2026-08-01", "2026-08-31")
        new_result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            failed_collector, db_path=db_path,
        )
        assert new_result["status"] == "VALIDATION_FAILED"

        # 验证 latest_valid 仍然是旧批次
        latest2 = get_latest_valid_top20("TH", "家居用品", "家居收纳",
                                         "热门搜索关键词", "month", db_path=db_path)
        assert latest2["batch_id"] == old_batch_id, \
            f"新批次失败后 latest_valid 不应改变，仍应为旧批次 {old_batch_id}，实际: {latest2['batch_id']}"
        print("  ✓ 测试3: 新 batch 失败 → 旧 latest_valid 不受影响")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 4: 同一个关键词出现在两个 ranking_field → 数据库保存两行
# =============================================================================
def test_same_keyword_multiple_fields():
    conn, db_path = _tmp_db()
    try:
        # 关键词A 出现在 search_volume 和 ctor_score 两个字段
        data = {}
        for f in TOP20_RANKING_FIELDS:
            data[f] = [(f"unique_{f}", "100", 100.0)]
        # 在 search_volume 和 ctor_score 中都加 "共享词"
        data["search_volume"].append(("共享词", "500", 500.0))
        data["ctor_score"].append(("共享词", "3.5", 3.5))

        collector = _make_mock_collector(data)
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "SUCCESS"

        # 查询共享词的行数
        repo = PageTop20Repo(conn)
        rows = conn.execute(
            "SELECT ranking_field, display_value FROM page_top20_data "
            "WHERE keyword=? AND batch_id=? ORDER BY ranking_field",
            ("共享词", result["batch_id"]),
        ).fetchall()

        assert len(rows) == 2, f"共享词应保存 2 行，实际: {len(rows)}"
        fields = {r["ranking_field"] for r in rows}
        assert "search_volume" in fields
        assert "ctor_score" in fields
        # display_value 各自独立
        disp_map = {r["ranking_field"]: r["display_value"] for r in rows}
        assert disp_map["search_volume"] == "500"
        assert disp_map["ctor_score"] == "3.5"
        print("  ✓ 测试4: 同一关键词跨字段 → 各行独立保存")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 5: 关键词只出现在一个 ranking_field → 不生成其他指标记录
# =============================================================================
def test_keyword_only_in_one_field():
    conn, db_path = _tmp_db()
    try:
        data = {}
        for f in TOP20_RANKING_FIELDS:
            data[f] = [(f"only_{f}", "100", 100.0)]

        collector = _make_mock_collector(data)
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "SUCCESS"

        # 只在 search_volume 中出现的词
        rows = conn.execute(
            "SELECT COUNT(*) as n FROM page_top20_data WHERE keyword=? AND batch_id=?",
            ("only_search_volume", result["batch_id"]),
        ).fetchone()
        assert rows["n"] == 1, f"only_search_volume 应只有 1 行，实际: {rows['n']}"

        # 总共 6 行（每个字段一个唯一词）
        total = conn.execute(
            "SELECT COUNT(*) as n FROM page_top20_data WHERE batch_id=?",
            (result["batch_id"],),
        ).fetchone()["n"]
        assert total == 6, f"总记录数应为 6，实际: {total}"
        print("  ✓ 测试5: 只出现在一个字段的词 → 只有 1 行记录")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 6: display_value 保持原始格式
# =============================================================================
def test_display_value_preserved():
    conn, db_path = _tmp_db()
    try:
        data = {}
        test_values = [
            ("搜索量词", "7.47K", 7470.0),
            ("点击词", "962.75", 962.75),
            ("SKU词", "12.3K", 12300.0),
            ("在售词", "1,234", 1234.0),  # 带逗号
            ("CTR词", "45.2", 45.2),
            ("CTOR词", "3.45", 3.45),
        ]
        for i, f in enumerate(TOP20_RANKING_FIELDS):
            kw, disp, num = test_values[i]
            data[f] = [(kw, disp, num)]

        collector = _make_mock_collector(data)
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "SUCCESS"

        for i, f in enumerate(TOP20_RANKING_FIELDS):
            kw, expected_disp, expected_num = test_values[i]
            row = conn.execute(
                "SELECT display_value, numeric_value FROM page_top20_data "
                "WHERE batch_id=? AND ranking_field=? AND keyword=?",
                (result["batch_id"], f, kw),
            ).fetchone()
            assert row, f"未找到 {f} / {kw}"
            assert row["display_value"] == expected_disp, \
                f"{f}: display_value 应为 '{expected_disp}'，实际: '{row['display_value']}'"
            assert abs(row["numeric_value"] - expected_num) < 0.01, \
                f"{f}: numeric_value 应为 {expected_num}，实际: {row['numeric_value']}"
        print("  ✓ 测试6: display_value 保持原始格式，numeric_value 同步保存")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 7: numeric_value 只用于校验（存在但不替代 display_value）
# =============================================================================
def test_numeric_value_not_replaces_display():
    conn, db_path = _tmp_db()
    try:
        # 同一个数值有不同的 display 形式
        data = {
            "search_volume": [("词1", "1.0K", 1000.0)],
            "product_clicks": [("词2", "1000", 1000.0)],
            "sku_sales_index": [("词3", "1,000", 1000.0)],
            "on_sale_products": [("词4", "1000.0", 1000.0)],
            "ctr_index": [("词5", "1000.00", 1000.0)],
            "ctor_score": [("词6", "1000", 1000.0)],
        }
        collector = _make_mock_collector(data)
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "SUCCESS"

        # 验证 display_value 各不相同（即使 numeric 相同）
        rows = conn.execute(
            "SELECT ranking_field, keyword, display_value, numeric_value "
            "FROM page_top20_data WHERE batch_id=? ORDER BY ranking_field",
            (result["batch_id"],),
        ).fetchall()

        display_values = {r["ranking_field"]: r["display_value"] for r in rows}
        assert display_values["search_volume"] == "1.0K"
        assert display_values["product_clicks"] == "1000"
        assert display_values["sku_sales_index"] == "1,000"
        assert display_values["on_sale_products"] == "1000.0"
        assert display_values["ctr_index"] == "1000.00"

        # 所有 numeric_value 都是 1000.0
        for r in rows:
            assert r["numeric_value"] == 1000.0, f"{r['ranking_field']} numeric_value 不对"
        print("  ✓ 测试7: numeric_value 存在但不替代 display_value（各自独立）")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 8: 同周期同条件重复采集 → DUPLICATE
# =============================================================================
def test_duplicate_detection():
    conn, db_path = _tmp_db()
    try:
        collector = _make_successful_collector()

        # 第一次
        result1 = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result1["status"] == "SUCCESS"

        # 第二次（同一周期）
        result2 = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result2["status"] == "DUPLICATE", \
            f"重复采集应为 DUPLICATE，实际: {result2['status']}"

        # 验证数据总数没有翻倍
        repo = PageTop20Repo(conn)
        total = conn.execute(
            "SELECT COUNT(*) as n FROM page_top20_data"
        ).fetchone()["n"]
        assert total == 18, f"重复采集不应增加数据，应 18 条，实际: {total}"

        # 验证 collection_log 有两条记录（一条 SUCCESS，一条 DUPLICATE）
        logs = conn.execute(
            "SELECT status FROM collection_log WHERE source='page_top20' ORDER BY id"
        ).fetchall()
        statuses = [l["status"] for l in logs]
        assert statuses == ["SUCCESS", "DUPLICATE"], f"日志状态: {statuses}"
        print("  ✓ 测试8: 同周期重复采集 → DUPLICATE，不重复入库")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 9: ranking_field 不能串数据
# =============================================================================
def test_ranking_fields_not_mixed():
    conn, db_path = _tmp_db()
    try:
        # 每个字段都有自己独特的关键词
        data = {}
        for i, f in enumerate(TOP20_RANKING_FIELDS):
            data[f] = [(f"kw_{f}_only", f"val_{f}_disp", float(i * 100))]

        collector = _make_mock_collector(data)
        result = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector, db_path=db_path,
        )
        assert result["status"] == "SUCCESS"

        # 逐个字段验证：关键词和 display_value 必须正确匹配
        for f in TOP20_RANKING_FIELDS:
            row = conn.execute(
                "SELECT keyword, display_value FROM page_top20_data "
                "WHERE batch_id=? AND ranking_field=?",
                (result["batch_id"], f),
            ).fetchone()
            assert row, f"字段 {f} 无数据"
            assert row["keyword"] == f"kw_{f}_only", \
                f"{f}: 关键词串了，应为 kw_{f}_only，实际: {row['keyword']}"
            assert row["display_value"] == f"val_{f}_disp", \
                f"{f}: display_value 串了"
        print("  ✓ 测试9: 各 ranking_field 数据不串")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 测试 10: level_1 / level_2 / ranking_type / period 不能串数据
# =============================================================================
def test_dimensions_not_mixed():
    conn, db_path = _tmp_db()
    try:
        # 采集两个不同口径
        data1 = {f: [("k1", "100", 100.0)] for f in TOP20_RANKING_FIELDS}
        data2 = {f: [("k2", "200", 200.0)] for f in TOP20_RANKING_FIELDS}

        collector1 = _make_mock_collector(data1, "2026-08-01", "2026-08-31")
        collector2 = _make_mock_collector(data2, "2026-09-01", "2026-09-30")

        # 批次1: TH / 家居用品 / 家居收纳 / 热门搜索关键词 / 月度 / 8月
        r1 = collect_page_top20_batch(
            "TH", "家居用品", "家居收纳", "热门搜索关键词", "month",
            collector1, db_path=db_path,
        )
        assert r1["status"] == "SUCCESS"

        # 批次2: TH / 厨具 / 锅具 / 飙升关键词 / 月度 / 9月
        r2 = collect_page_top20_batch(
            "TH", "厨具", "锅具", "飙升关键词", "month",
            collector2, db_path=db_path,
        )
        assert r2["status"] == "SUCCESS"

        # 验证 latest_valid 各自独立
        latest1 = get_latest_valid_top20("TH", "家居用品", "家居收纳",
                                         "热门搜索关键词", "month", db_path=db_path)
        assert latest1["batch_id"] == r1["batch_id"]
        assert latest1["period_start"] == "2026-08-01"

        latest2 = get_latest_valid_top20("TH", "厨具", "锅具",
                                         "飙升关键词", "month", db_path=db_path)
        assert latest2["batch_id"] == r2["batch_id"]
        assert latest2["period_start"] == "2026-09-01"

        # 交叉验证：不同口径互不干扰
        # 家居用品的 latest 里不应该有 k2
        kw1_keywords = {r["keyword"] for r in latest1["rows"]}
        assert "k1" in kw1_keywords
        assert "k2" not in kw1_keywords

        kw2_keywords = {r["keyword"] for r in latest2["rows"]}
        assert "k2" in kw2_keywords
        assert "k1" not in kw2_keywords

        # collection_log source 都是 page_top20
        logs = conn.execute(
            "SELECT source, level_1_category, level_2_category, ranking_type, status "
            "FROM collection_log ORDER BY id"
        ).fetchall()
        assert len(logs) == 2
        for l in logs:
            assert l["source"] == "page_top20", f"source 应为 page_top20，实际: {l['source']}"
        assert logs[0]["level_1_category"] == "家居用品"
        assert logs[0]["level_2_category"] == "家居收纳"
        assert logs[0]["ranking_type"] == "热门搜索关键词"
        assert logs[1]["level_1_category"] == "厨具"
        assert logs[1]["level_2_category"] == "锅具"
        assert logs[1]["ranking_type"] == "飙升关键词"

        print("  ✓ 测试10: level_1 / level_2 / ranking_type / period 不串数据")
    finally:
        conn.close()
        os.unlink(db_path)


# =============================================================================
# 主入口
# =============================================================================
def main():
    tests = [
        test_all_fields_success,
        test_one_field_failed,
        test_failed_does_not_affect_old_valid,
        test_same_keyword_multiple_fields,
        test_keyword_only_in_one_field,
        test_display_value_preserved,
        test_numeric_value_not_replaces_display,
        test_duplicate_detection,
        test_ranking_fields_not_mixed,
        test_dimensions_not_mixed,
    ]
    print(f"\n运行 {len(tests)} 个 Page TOP20 单元测试...\n")
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n=== 结果: {passed} 通过, {failed} 失败 ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
