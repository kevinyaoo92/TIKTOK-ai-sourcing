# -*- coding: utf-8 -*-
"""数据源架构测试：一级/二级 × 周/月 四类源统一导入 + 幂等 + 隔离 + V1/V1.1 衔接。

覆盖需求:
  1 一级周导入   2 一级月导入   3 二级周导入   4 二级月导入
  5 同文件重复导入幂等   6 不同周期并存   7 一级/二级不混淆
  8 原始字段不被修改   9 V1 screening 从新数据层运行
  10 AI 语义缓存不重复调用   11 product opportunity 正常生成   12 全套旧测试回归(pytest -q)
"""
from __future__ import annotations

import json
import sqlite3

from app.analysis.opportunity_v11 import build_opportunities
from app.analysis.screening import V1Screener
from app.database import KeywordRepo, connect, init_db
from app.tiktok.collector import import_export_file

L1 = "时尚配件"
L2 = "平价饰品"
KT = "热门搜索关键词"

# 周期样本（与真实导出同构的合成数据；用于测试，不是伪造正式数据）
M1 = ("2026-07-01", "2026-07-31")   # 一级·月(已有真实同构)
M2 = ("2026-08-01", "2026-08-31")   # 二级·月
W1 = ("2026-08-17", "2026-08-23")   # 一级·周
W2 = ("2026-08-24", "2026-08-30")   # 二级·周

# (keyword, 搜索量, 点击数, SKU, 在售, 均价, CTR, CTOR)
def _rows(*words):
    out = []
    for i, w in enumerate(words, 1):
        sv = w[1]
        out.append((w[0], sv, round(sv * 0.6, 2), round(sv * 0.25, 2),
                    max(50, int(sv * 0.4)), 2.5, 28.0, w[2]))
    return out


HEADER = "关键词,搜索量,商品点击数,SKU销售指数,在售商品,平均价格(฿),CTR指数,CTOR评分"


def _write_csv(path, ps, pe, meta_cat, rows):
    lines = [f"[日期范围]: {ps} ~ {pe}", f"[类目]: {meta_cat}", "", HEADER]
    for r in rows:
        lines.append(",".join(str(x) for x in r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_files(tmp_path):
    """生成 4 份源文件（一级/二级 × 周/月），关键词互不相同便于隔离断言。"""
    p1m = tmp_path / "l1_month.csv"; _write_csv(p1m, *M1, "时尚配件",
                                                _rows(("ค1", 900, 9.0), ("ค2", 700, 8.0), ("ค3", 500, 7.0)))
    p2m = tmp_path / "l2_month.csv"; _write_csv(p2m, *M2, "平价饰品",
                                                _rows(("j1", 880, 9.5), ("j2", 660, 8.5)))
    p1w = tmp_path / "l1_week.csv";  _write_csv(p1w, *W1, "时尚配件",
                                                _rows(("w1", 300, 6.0), ("w2", 260, 5.5)))
    p2w = tmp_path / "l2_week.csv";  _write_csv(p2w, *W2, "平价饰品",
                                                _rows(("wj1", 320, 6.5), ("wj2", 240, 5.0)))
    return p1m, p2m, p1w, p2w


def _imp(file, db, *, category=None, level_2=None, level_1=L1):
    return import_export_file(file, db, category=category or (level_2 or L1),
                              level_1_category=level_1, level_2_category=level_2)


def _fresh(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# 1~4. 四类源导入
# ---------------------------------------------------------------------------
def test_four_sources_import(tmp_path):
    db = tmp_path / "t.db"
    p1m, p2m, p1w, p2w = _make_files(tmp_path)
    r1 = _imp(p1m, db)                      # 一级/月
    r2 = _imp(p2m, db, category="平价饰品", level_2=L2)   # 二级/月
    r3 = _imp(p1w, db)                      # 一级/周
    r4 = _imp(p2w, db, category="平价饰品", level_2=L2)   # 二级/周
    assert (r1["inserted"], r2["inserted"], r3["inserted"], r4["inserted"]) == (3, 2, 2, 2)
    conn = _fresh(tmp_path)
    total = conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0]
    assert total == 9
    rows = conn.execute("SELECT level_1_category, level_2_category, period_type, "
                        "COUNT(*) n FROM keyword_data GROUP BY 1,2,3").fetchall()
    expect = {("时尚配件", None, "month"): 3, ("时尚配件", "平价饰品", "month"): 2,
              ("时尚配件", None, "week"): 2, ("时尚配件", "平价饰品", "week"): 2}
    got = {(r[0], r[1], r[2]): r[3] for r in rows}
    assert got == expect, got
    # 元数据完整性
    row = conn.execute("SELECT * FROM keyword_data LIMIT 1").fetchone()
    assert row["source_file"] and row["file_hash"] and row["imported_at"]
    assert row["level_1_category"] == "时尚配件"
    conn.close()


# ---------------------------------------------------------------------------
# 5. 同文件重复导入 = 幂等
# ---------------------------------------------------------------------------
def test_duplicate_import_idempotent(tmp_path):
    db = tmp_path / "t.db"
    p1m = tmp_path / "l1_month.csv"
    _write_csv(p1m, *M1, "时尚配件", _rows(("ค1", 900, 9.0), ("ค2", 700, 8.0)))
    r1 = _imp(p1m, db)
    r2 = _imp(p1m, db)          # 再导一次
    assert r1["inserted"] == 2 and r2["inserted"] == 0 and r2["skipped_duplicates"] == 2
    conn = _fresh(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 2
    conn.close()


# ---------------------------------------------------------------------------
# 6. 不同周期独立并存（同层同词不同周期 → 两条历史快照）
# ---------------------------------------------------------------------------
def test_periods_coexist(tmp_path):
    db = tmp_path / "t.db"
    p_m = tmp_path / "m.csv";  _write_csv(p_m, *M1, "时尚配件", _rows(("ค1", 900, 9.0)))
    p_w = tmp_path / "w.csv";  _write_csv(p_w, *W1, "时尚配件", _rows(("ค1", 300, 6.0)))
    _imp(p_m, db)
    _imp(p_w, db)
    conn = _fresh(tmp_path)
    kw = conn.execute("SELECT period_start, period_type FROM keyword_data "
                      "WHERE keyword='ค1' ORDER BY period_type").fetchall()
    assert {(r["period_type"]) for r in kw} == {"month", "week"}       # 周月独立保留
    assert conn.execute("SELECT COUNT(*) FROM keyword_data WHERE keyword='ค1'").fetchone()[0] == 2
    conn.close()


# ---------------------------------------------------------------------------
# 7. 一级与二级不混淆（同周期同时存在，按 level 查询互不串）
# ---------------------------------------------------------------------------
def test_level1_level2_isolated(tmp_path):
    db = tmp_path / "t.db"
    p1m = tmp_path / "l1.csv"; _write_csv(p1m, *M1, "时尚配件", _rows(("ค1", 900, 9.0)))
    p2m = tmp_path / "l2.csv"; _write_csv(p2m, *M1, "平价饰品", _rows(("ค1", 700, 8.0)))
    _imp(p1m, db)
    _imp(p2m, db, category="平价饰品", level_2=L2)   # 同关键词同周期 但层级不同
    conn = _fresh(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM keyword_data").fetchone()[0] == 2   # 不覆盖
    repo = KeywordRepo(conn)
    l1_rows = repo.snapshot_rows_by_level("TH", L1, None, KT, "month", "2026-07-01")
    l2_rows = repo.snapshot_rows_by_level("TH", L1, L2, KT, "month", "2026-07-01")
    assert len(l1_rows) == 1 and l1_rows[0]["search_volume"] == 900
    assert len(l2_rows) == 1 and l2_rows[0]["search_volume"] == 700
    assert l1_rows[0]["level_2_category"] is None
    assert l2_rows[0]["level_2_category"] == L2
    conn.close()


# ---------------------------------------------------------------------------
# 8. 原始字段不被修改（幂等重导不改已存值；入库值=源文件值）
# ---------------------------------------------------------------------------
def test_raw_fields_untouched(tmp_path):
    db = tmp_path / "t.db"
    p1m = tmp_path / "l1.csv"
    _write_csv(p1m, *M1, "时尚配件", [("ค1", 7465.41, 4995.71, 1421.54, 13039, 1.61, 22.87, 5.87)])
    _imp(p1m, db)
    _imp(p1m, db)                                     # 重复导入
    conn = _fresh(tmp_path)
    row = conn.execute("SELECT * FROM keyword_data WHERE keyword='ค1'").fetchone()
    assert row["search_volume"] == 7465.41
    assert row["product_clicks"] == 4995.71
    assert row["sku_sales_index"] == 1421.54
    assert row["on_sale_products"] == 13039
    assert row["avg_price"] == 1.61
    assert row["ctr_index"] == 22.87 and row["ctor_score"] == 5.87
    conn.close()


# ---------------------------------------------------------------------------
# 9. V1 screening 从新标准化层运行（level_1 入口；二级词不混入一级 cohort）
# ---------------------------------------------------------------------------
def test_screening_reads_level_layer(tmp_path):
    db = tmp_path / "t.db"
    p1m = tmp_path / "l1.csv"
    _write_csv(p1m, *M1, "时尚配件", _rows(
        ("kwA", 900, 9.0), ("kwB", 700, 8.0), ("kwC", 500, 7.0), ("kwD", 300, 9.5)))
    p2m = tmp_path / "l2.csv"
    _write_csv(p2m, *M1, "平价饰品", _rows(
        ("hjH", 5000, 9.9), ("hjI", 4800, 9.8)))       # 二级超高需求词
    _imp(p1m, db)
    _imp(p2m, db, category="平价饰品", level_2=L2)
    conn = _fresh(tmp_path)
    res = V1Screener(conn, level_1_category=L1, level_2_category=None,
                     store_results=False, top_n=10).run()
    kws = {r.keyword for r in res["records"]}
    cand_kws = {d.keyword for d in res["candidates"]}
    assert "kwA" in kws and "kwB" in kws and "kwC" in kws
    assert not (kws & {"hjH", "hjI"})                 # 二级词绝不混入一级筛选
    assert len(cand_kws) >= 2                          # 有候选产出
    conn.close()


# ---------------------------------------------------------------------------
# 10. AI 语义缓存不重复调用（已成功词在新周期再次出现时不进候选任务）
# ---------------------------------------------------------------------------
def test_semantic_cache_not_recalled(tmp_path, monkeypatch):
    from app.ai import deepseek
    from app.ai.semantics import SemanticsPipeline
    from app import config
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-t")
    db = tmp_path / "t.db"
    p1m = tmp_path / "m1.csv"
    p1m2 = tmp_path / "m2.csv"   # 新周期: ค1(旧词再现) + kwNEW(新词)
    _write_csv(p1m, *M1, "时尚配件", _rows(("ค1", 900, 9.0)))
    _write_csv(p1m2, *M2, "时尚配件", _rows(("ค1", 950, 9.2), ("kwNEW", 600, 7.5)))
    _imp(p1m, db)
    _imp(p1m2, db)
    conn = connect(db)
    init_db(conn)
    # 预置 ค1 的语义缓存(success) —— 模拟旧周期已分析，新周期不得重复调用
    conn.execute("INSERT INTO ai_semantic_results (original_keyword, market, status, "
                 "intent_status, canonical_product_name, canonical_product_key, confidence) "
                 "VALUES ('ค1','TH','success','PRODUCT','测试项链','necklace',0.9)")
    conn.commit()
    # 跑 V1 screening（新标准化层，最新周期=2026-08 月），候选含 ค1 与 kwNEW
    res = V1Screener(conn, level_1_category=L1, store_results=True, top_n=80).run()
    assert {d.keyword for d in res["candidates"]} >= {"ค1", "kwNEW"}
    calls = {"n": 0, "words": []}

    def spy(messages, **kwargs):
        calls["n"] += 1
        words = json.loads(messages[-1]["content"])["words"]
        calls["words"] = words
        return json.dumps([{"index": i, "original_keyword": w, "language": "th",
                            "meaning_zh": "含义", "product_category": "饰品",
                            "product_subcategory": "项链", "canonical_product_name": "项链",
                            "canonical_product_key": "necklace",
                            "search_terms_1688": ["a", "b"], "intent_status": "PRODUCT",
                            "is_product": 1, "confidence": 0.9} for i, w in enumerate(words)],
                           ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", spy)
    pipe = SemanticsPipeline(conn, market="TH")
    cands = pipe.screening_candidates(top_rank_max=80)
    assert "ค1" in {c["keyword"] for c in cands}
    eligible = pipe.filter_eligible(cands)
    assert all(c["keyword"] != "ค1" for c in eligible)   # 缓存词被剔除
    pipe.process(eligible)                                # 只处理未缓存新词
    assert calls["n"] == 1
    assert "ค1" not in calls["words"] and "kwNEW" in calls["words"]   # 不重复调用已成功词
    conn.close()


# ---------------------------------------------------------------------------
# 11. product opportunity 从新数据层链路正常生成（market→screening→语义→机会）
# ---------------------------------------------------------------------------
def test_opportunity_pipeline_on_level_data(tmp_path):
    db = tmp_path / "t.db"
    p1m = tmp_path / "l1.csv"
    _write_csv(p1m, *M1, "时尚配件", _rows(
        ("kwA", 900, 9.0), ("kwB", 700, 8.0), ("kwC", 500, 7.0), ("kwZ", 100, 3.0)))  # kwZ 垫底
    _imp(p1m, db)
    conn = _fresh(tmp_path)
    res = V1Screener(conn, level_1_category=L1, store_results=True, top_n=10).run()
    passed = [d.keyword for d in res["candidates"]]
    assert passed
    # 模拟语义成功（不调 AI）：两词归同机会，一词独立
    seed = {"kwA": ("项链", "necklace"), "kwB": ("项链", "necklace"), "kwC": ("耳环", "earring")}
    for kw, (name, key) in seed.items():
        conn.execute("INSERT INTO ai_semantic_results (original_keyword, market, status, "
                     "intent_status, canonical_product_name, canonical_product_key, confidence) "
                     "VALUES (?, 'TH','success','PRODUCT',?,?,0.9)", (kw, name, key))
    conn.commit()
    r = build_opportunities(conn, market="TH", store=False)
    assert r["stats"]["groups"] >= 2                     # 同义词合并 + 独立
    assert len(r["final"]) <= 9 and r["final"]
    by_key = {o.canonical_product_key: o for o in r["opportunities"]}
    assert by_key["necklace"].keyword_count == 2         # kwA+kwB 合并为一个机会
    assert by_key["earring"].keyword_count == 1
    conn.close()
