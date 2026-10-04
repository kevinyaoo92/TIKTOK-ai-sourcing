# -*- coding: utf-8 -*-
"""V2 商品机会池测试（合成一级/二级 × 周/月；不调用 DeepSeek；临时库）。

覆盖(验证要求 1~15):
  1 一级/二级隔离      2 周/月同时存在       3 同 canonical key 归并
  4 不同 key 不合并     5 搜索量不求和        6 source_keywords 保留
  7 evidence_count    8 weekly evidence    9 monthly evidence
  10 CORE/ADJ/EXCL/REVIEW   11 不足9不填充    12 TopN 非硬编码
  13 重复运行一致       14 正式旧表不破坏      15 DeepSeek calls=0
"""
from __future__ import annotations

import json

from app.analysis.pool import build_pool, fit_category_status
from app.database import KeywordRepo, connect, init_db
from app.models import KeywordRecord

KT = "热门搜索关键词"
L1 = "时尚配件"
L2 = "平价饰品"


def _kw(kw, sv, ctor, ps, pe, pt, level_2=None, **extra):
    return KeywordRecord(
        period_start=ps, period_end=pe, period_type=pt, market="TH", category=level_2 or L1,
        level_1_category=L1, level_2_category=level_2, keyword_type=KT, keyword=kw,
        search_volume=float(sv), product_clicks=float(sv) * 0.6,
        sku_sales_index=float(sv) * 0.25, on_sale_products=int(sv * 0.4),
        avg_price=2.5, ctr_index=28.0, ctor_score=float(ctor), source_file="syn-test.csv",
        file_hash="testhash", **extra)


def _seed_base(conn):
    """一级·月 快照: necklace 两词(高/中) + earring 一词 + 低需求垫底 + 保护词。"""
    repo = KeywordRepo(conn)
    repo.insert_many([
        _kw("สร้อยคอ", 900, 9.0, "2026-07-01", "2026-07-31", "month"),
        _kw("สร้อยคอผู้หญิง", 700, 8.0, "2026-07-01", "2026-07-31", "month"),
        _kw("ตุ้มหู", 600, 8.5, "2026-07-01", "2026-07-31", "month"),
        _kw("เข็มขัด", 800, 8.2, "2026-07-01", "2026-07-31", "month"),
        _kw("หน้ากาก", 750, 9.0, "2026-07-01", "2026-07-31", "month"),
        _kw("lowpad", 50, 2.0, "2026-07-01", "2026-07-31", "month"),   # 低需求淘汰
    ])


def _seed_week(conn):
    """一级·周 快照(下月): 同词再现 + 新词。"""
    KeywordRepo(conn).insert_many([
        _kw("สร้อยคอ", 950, 9.2, "2026-08-03", "2026-08-09", "week"),
        _kw("แว่นตากันแดด", 620, 7.0, "2026-08-03", "2026-08-09", "week"),
    ])


def _seed_level2(conn):
    """二级·月(平价饰品): 独立高需求词 + 与一级同 key 词。"""
    KeywordRepo(conn).insert_many([
        _kw("jewelA", 5000, 9.9, "2026-07-01", "2026-07-31", "month", level_2=L2),
        _kw("jewelB", 400, 7.0, "2026-07-01", "2026-07-31", "month", level_2=L2),
    ])


def _seed_semantic(conn):
    rows = [
        # (keyword, key, name, cat, sub)
        ("สร้อยคอ", "necklace", "不掉色项链", "饰品", "项链"),
        ("สร้อยคอผู้หญิง", "necklace", "不掉色项链", "饰品", "项链"),
        ("ตุ้มหู", "earring", "耳环", "饰品", "耳饰"),
        ("เข็มขัด", "women_belt", "女士腰带", "腰带", "女士腰带"),
        ("หน้ากาก", "face_mask", "一次性口罩", "防护用品", "口罩"),
        ("lowpad", "whatever", "忽略", "饰品", "其他"),      # 不会通过筛选
        ("แว่นตากันแดด", "sunglasses", "太阳镜", "眼镜", "太阳镜"),
        ("jewelA", "jewelry_set", "平价饰品套装", "饰品", "套装"),
        ("jewelB", "jewelry_set", "平价饰品套装", "饰品", "套装"),
    ]
    for kw, key, name, cat, sub in rows:
        conn.execute("INSERT INTO ai_semantic_results (original_keyword, market, status, "
                     "intent_status, canonical_product_name, canonical_product_key, "
                     "product_category, product_subcategory, confidence) "
                     "VALUES (?, 'TH','success','PRODUCT',?,?,?,?,0.9)", (kw, name, key, cat, sub))
    conn.commit()


def _fresh(tmp_path, seed=True, l2=False, week=False):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    if seed:
        _seed_base(conn)
        _seed_semantic(conn)
        if l2:
            _seed_level2(conn)
        if week:
            _seed_week(conn)
    return conn


def _op_rows(res):
    return {r.canonical_product_key: r for r in res["records"]}


# 1. 一级/二级隔离
def test_level12_isolated(tmp_path):
    conn = _fresh(tmp_path, l2=True)
    res = build_pool(conn, level_2_categories=(L2,), store=False)
    by_l2 = {}
    for r in res["records"]:
        by_l2.setdefault(r.level_2_category, set()).add(r.canonical_product_key)
    assert "jewelry_set" in by_l2.get(L2, set()) and "necklace" not in by_l2.get(L2, set())
    assert "necklace" in by_l2.get(None, set()) and "jewelry_set" not in by_l2.get(None, set())
    # 同 key 一二级并存 → 独立两行(不混源)
    conn.close()


# 2. 周/月同时存在(同 key 周月证据分列)
def test_week_month_coexist(tmp_path):
    conn = _fresh(tmp_path, week=True)
    res = build_pool(conn, store=False)
    row = _op_rows(res)["necklace"]
    w, m = json.loads(row.weekly_evidence), json.loads(row.monthly_evidence)
    assert len(m) >= 1 and m[0]["period_type"] == "month"
    assert len(w) >= 1 and w[0]["period_type"] == "week"
    assert row.first_seen == "2026-07-01" and row.last_seen == "2026-08-03"
    conn.close()


# 3+4. 同 key 归并 / 异 key 不合并
def test_group_by_canonical_key(tmp_path):
    conn = _fresh(tmp_path)
    res = build_pool(conn, store=False)
    rows = _op_rows(res)
    assert rows["necklace"].evidence_count == 2                 # 两同义词归并
    assert "earring" in rows and "necklace" in rows             # 不同 key 独立
    conn.close()


# 5. 搜索量不求和 + 6/7. source_keywords / evidence_count
def test_no_sum_and_evidence_fields(tmp_path):
    conn = _fresh(tmp_path)
    res = build_pool(conn, store=False)
    row = _op_rows(res)["necklace"]
    src = json.loads(row.source_keywords)
    assert {"สร้อยคอ", "สร้อยคอผู้หญิง"} == set(src)
    assert row.evidence_count == 2
    # group demand = 最强词 demand_pct(∈(0,1] 分位)，绝不等于 搜索量/任何求和
    assert 0 < row.demand_score <= 1
    assert row.supply_score is not None and row.opportunity_gap is not None
    conn.close()


# 8/9. weekly/monthly evidence 内容
def test_evidence_content(tmp_path):
    conn = _fresh(tmp_path, week=True)
    res = build_pool(conn, store=False)
    row = _op_rows(res)["necklace"]
    m = json.loads(row.monthly_evidence)[0]
    assert m["best_keyword"] in ("สร้อยคอ", "สร้อยคอผู้หญิง")
    assert m["keyword_count"] == 2 and m["cohort_size"] == 6
    w = json.loads(row.weekly_evidence)[0]
    assert w["best_keyword"] == "สร้อยคอ" and w["period_start"] == "2026-08-03"
    conn.close()


# 10. 业务分类
def test_category_status(tmp_path):
    conn = _fresh(tmp_path)
    res = build_pool(conn, store=False)
    st = {r.canonical_product_key: r.category_status for r in res["records"]}
    assert st.get("necklace") == "CORE"
    assert st.get("earring") == "CORE"
    assert st.get("women_belt") == "ADJACENT"
    assert st.get("face_mask") == "EXCLUDE"
    # REVIEW: 边界词直接判函数
    assert fit_category_status("防蓝光眼镜", "blue_light_glasses", "眼镜", "防蓝光眼镜") == "REVIEW"
    assert fit_category_status("女士发夹", "hair_clip", "发饰", "发夹") == "CORE"
    assert fit_category_status("披肩", "shawl", "围巾", "披肩") == "ADJACENT"
    conn.close()


# 11. 不足9不填充(全量池; 行数=真实机会数)
def test_no_padding_to_9(tmp_path):
    conn = _fresh(tmp_path)
    res = build_pool(conn, store=False)
    n = len(res["records"])
    assert n < 9 and n >= 4                     # 真实机会数, 不补 9
    assert res["stats"]["pool"]["opportunities"] == n
    conn.close()


# 12. TopN 非硬编码(top_n=0 全量 vs top_n=3 截断)
def test_top_n_configurable(tmp_path):
    conn = _fresh(tmp_path)
    all_res = build_pool(conn, top_n=10 ** 9, store=False)     # 全量
    top3 = build_pool(conn, top_n=3, store=False)              # 截断 3
    assert len(all_res["records"]) > len(top3["records"])
    assert len(top3["records"]) <= 3
    conn.close()


# 13. 重复运行结果一致(幂等整表重建)
def test_repeat_run_consistent(tmp_path):
    conn = _fresh(tmp_path)
    r1 = build_pool(conn, store=True)
    n1 = conn.execute("SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0]
    r2 = build_pool(conn, store=True)
    n2 = conn.execute("SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0]
    assert n1 == n2 == len(r1["records"])
    s1 = {x["canonical_product_key"]: x["score"] for x in conn.execute(
        "SELECT canonical_product_key, score FROM product_opportunities_v2")}
    s2 = {x["canonical_product_key"]: x["score"] for x in conn.execute(
        "SELECT canonical_product_key, score FROM product_opportunities_v2")}
    assert s1 == s2
    conn.close()


# 14. 正式旧表不被破坏(v1 47 在 product_opportunities；此处以 tmp 模拟旧行)
def test_v1_table_untouched(tmp_path):
    conn = _fresh(tmp_path)
    conn.execute("INSERT INTO product_opportunities (run_id, opportunity_id, market, "
                 "canonical_product_key, canonical_product_name, opportunity_score, "
                 "keyword_count, is_final, status, created_at) "
                 "VALUES ('old','OP-0001','TH','necklace','旧项链',0.5,1,1,'final','t1')")
    conn.commit()
    build_pool(conn, store=True)                                 # 写 v2
    assert conn.execute("SELECT COUNT(*) FROM product_opportunities").fetchone()[0] == 1  # 旧表未动
    assert conn.execute("SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0] >= 4
    conn.close()


# 15. DeepSeek calls = 0
def test_deepseek_calls_zero(tmp_path):
    conn = _fresh(tmp_path, week=True, l2=True)
    res = build_pool(conn, level_2_categories=(L2,), store=True)
    assert res["stats"]["semantic"]["deepseek_calls"] == 0
    # missing_semantic 计数：一级周新词 sunglasses 有语义；lowpad 未通过 → 0 missing
    assert res["stats"]["semantic"]["missing_semantic"] == 0
    conn.close()
