# -*- coding: utf-8 -*-
"""V1 初期关键词筛选层测试。

覆盖: 正常高需求词 / 中等需求+高CTOR / 高需求+高供给 / 非商品词 /
      极端值 / 低需求+高CTOR保护通道 / Top N限制 /
      点击不进评分(与搜索量不重复计分) / 低需求且低CTOR淘汰。
全部使用临时库 + 合成 keyword_data，纯 Python，不调用 AI。
"""
from __future__ import annotations

from app.analysis.screening import NonProductFilter, V1Screener
from app.database import KeywordRepo, connect, init_db
from app.models import KeywordRecord

CAT = "时尚配件"
KT = "热门搜索关键词"
PERIOD = "2026-07-01"


def _seed(conn, specs: list[tuple]) -> None:
    """specs: (keyword, search_volume, product_clicks, sku_sales_index, on_sale_products, ctr_index, ctor_score)"""
    repo = KeywordRepo(conn)
    repo.insert_many([
        KeywordRecord(
            period_start=PERIOD, period_end="2026-07-31", period_type="month",
            market="TH", category=CAT, keyword_type=KT, keyword=kw,
            search_volume=sv, product_clicks=clk, sku_sales_index=sku,
            on_sale_products=ons, ctr_index=ctr, ctor_score=ctor,
            source_file="scrn_test.xlsx",
        )
        for kw, sv, clk, sku, ons, ctr, ctor in specs
    ])


def _run(tmp_path, specs, top_n=80, store=False, **kw) -> dict:
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed(conn, specs)
    r = V1Screener(conn, top_n=top_n, store_results=store, **kw).run()
    conn.close()
    return r


def _by_kw(result) -> dict:
    return {rec.keyword: rec for rec in result["records"]}


# ---------------------------------------------------------------------------
# 1. 正常高需求词：搜索量最高+CTOR/SKU 高 → 主通道通过、机会分最高
# ---------------------------------------------------------------------------
def test_normal_high_demand_keyword(tmp_path):
    specs = [
        ("คีย์เชนแบรนด์เนม", 1000, 900, 800, 300, 30, 15),   # 全能选手
        ("สร้อยคอ", 500, 460, 500, 5000, 28, 8),
        ("หมวกแก๊ป", 400, 380, 450, 4000, 27, 7),
        ("เข็มขัด", 300, 280, 350, 3000, 26, 6),
        ("กำไล", 200, 180, 250, 2000, 25, 5),
        ("ต่างหู", 100, 90, 100, 1000, 24, 4),
    ]
    r = _run(tmp_path, specs)
    rec = _by_kw(r)["คีย์เชนแบรนด์เนม"]
    assert rec.passed == 1 and rec.channel == "main"
    assert rec.demand_pct == 0.9166667 or abs(rec.demand_pct - (5 + 0.5) / 6) < 1e-6
    assert rec.opportunity_score == max(x.opportunity_score for x in r["records"]
                                        if x.opportunity_score is not None)
    assert r["candidates"][0].keyword == rec.keyword


# ---------------------------------------------------------------------------
# 2. 中等需求 + 高 CTOR → 通过（需求≥P25 走主通道；购买意图高）
# ---------------------------------------------------------------------------
def test_mid_demand_high_ctor(tmp_path):
    specs = [
        ("แมสก์หน้าเรียว", 500, 460, 600, 600, 30, 19),   # 需求第4(中等) CTOR最高 SKU高
        ("w1", 800, 700, 600, 3000, 28, 8),
        ("w2", 700, 650, 500, 2500, 27, 7),
        ("w3", 600, 550, 450, 2000, 26, 6),
        ("w4", 400, 360, 350, 1500, 25, 5),
        ("w5", 300, 260, 300, 1000, 24, 4),
        ("w6", 200, 160, 200, 500, 23, 3),
    ]
    r = _run(tmp_path, specs)
    rec = _by_kw(r)["แมสก์หน้าเรียว"]
    assert rec.passed == 1
    assert rec.channel == "main"
    assert 0.25 <= rec.demand_pct < 0.9                     # 搜索量中等(非最高)
    assert rec.ctor_pct == (6 + 0.5) / 7                    # CTOR 全场最高
    assert rec.purchase_intent > 0.85                       # 意图主导(0.7×CTOR+0.3×SKU)


# ---------------------------------------------------------------------------
# 3. 高需求 + 高供给（红海）→ 通过但 gap 为负/低，机会分受供给压制
# ---------------------------------------------------------------------------
def test_high_demand_high_supply(tmp_path):
    specs = [
        ("พวงกุญแจ", 1000, 900, 700, 13039, 28, 8),   # 需求第2、供给最高
        ("w_top_sv", 1200, 950, 750, 300, 29, 9),
        ("w3", 600, 550, 450, 2000, 27, 7),
        ("w4", 400, 360, 350, 1500, 26, 6),
        ("w5", 300, 260, 300, 1000, 25, 5),
        ("w6", 200, 160, 200, 500, 24, 4),
    ]
    r = _run(tmp_path, specs)
    rec = _by_kw(r)["พวงกุญแจ"]
    assert rec.passed == 1
    assert rec.supply_pct == (5 + 0.5) / 6                   # 供给全场最高
    assert rec.demand_pct < rec.supply_pct
    assert rec.opportunity_gap < 0                            # 红海 → 负 gap
    # 供给高的词机会分应低于"同需求但供给低"的词
    red = rec.opportunity_score
    blue = _by_kw(r)["w_top_sv"].opportunity_score
    assert blue > red


# ---------------------------------------------------------------------------
# 4. 非商品词：黑名单 + 模式规则，均在评分前过滤（score=None）
# ---------------------------------------------------------------------------
def test_non_product_filtered_before_scoring(tmp_path):
    specs = [
        ("ราคา0.01บาท", 9000, 8000, 1500, 50, 30, 19),   # 高搜索促销词(黑名单)
        ("ของแถมฟรี", 8000, 7000, 1400, 60, 29, 18),      # 赠品词(模式规则)
        ("ขอสินค้าฟรี 2569", 7000, 6000, 1300, 70, 28, 17),  # 索取免费(模式规则)
        ("พวงกุญแจ", 1000, 900, 800, 500, 27, 9),        # 正常商品
    ]
    r = _run(tmp_path, specs)
    recs = _by_kw(r)
    for junk in ("ราคา0.01บาท", "ของแถมฟรี", "ขอสินค้าฟรี 2569"):
        rec = recs[junk]
        assert rec.passed == 0
        assert rec.reject_category == "non_product"
        assert rec.opportunity_score is None                # 未进入机会评分
        assert "非商品意图" in rec.reject_reason
    # 正常词不受影响
    assert recs["พวงกุญแจ"].passed == 1
    # 高搜索促销词不得占据候选名额
    assert all(c.keyword not in ("ราคา0.01บาท", "ของแถมฟรี", "ขอสินค้าฟรี 2569")
               for c in r["candidates"])


# ---------------------------------------------------------------------------
# 5. 极端值：超大/超小/0 值不崩溃；缺失字段按 data_quality 淘汰
# ---------------------------------------------------------------------------
def test_extreme_values(tmp_path):
    specs = [
        ("w_extreme_high", 999999, 900000, 1500, 1, 38, 20),  # 搜索量极端大+在售1
        ("w_zero_signal", 500, 460, 0, 1000, 25, 0),          # SKU=0 & CTOR=0
        ("w_normal", 400, 380, 400, 2000, 27, 7),
        ("w_low", 200, 180, 200, 500, 24, 4),
    ]
    r = _run(tmp_path, specs)
    recs = _by_kw(r)
    assert recs["w_extreme_high"].passed == 1                 # 极端高需求仍通过
    assert recs["w_zero_signal"].passed == 1                  # 0 值不算缺失，正常评分
    assert recs["w_zero_signal"].purchase_intent is not None
    assert recs["w_zero_signal"].purchase_intent < 0.3        # 无销售信号 → 意图低
    # 缺失搜索量的词 → data_quality
    conn = connect(tmp_path / "t2.db")
    init_db(conn)
    KeywordRepo(conn).insert_many([
        KeywordRecord(period_start=PERIOD, period_end="2026-07-31", period_type="month",
                      market="TH", category=CAT, keyword_type=KT, keyword="w_missing",
                      search_volume=None, product_clicks=None, sku_sales_index=100,
                      on_sale_products=50, ctr_index=20, ctor_score=5,
                      source_file="x.xlsx"),
        KeywordRecord(period_start=PERIOD, period_end="2026-07-31", period_type="month",
                      market="TH", category=CAT, keyword_type=KT, keyword="w_ok",
                      search_volume=400, product_clicks=300, sku_sales_index=100,
                      on_sale_products=50, ctr_index=20, ctor_score=5,
                      source_file="x.xlsx"),
    ])
    r2 = V1Screener(conn, store_results=False).run()
    conn.close()
    recs2 = {x.keyword: x for x in r2["records"]}
    assert recs2["w_missing"].passed == 0
    assert recs2["w_missing"].reject_category == "data_quality"
    assert recs2["w_ok"].passed == 1


# ---------------------------------------------------------------------------
# 6. 低需求 + 高CTOR → 保护通道放行；低需求 + 低CTOR → 淘汰
# ---------------------------------------------------------------------------
def test_low_demand_protection_channel(tmp_path):
    # 目标词: 搜索量全场最低(demand_pct≈0.06 <0.25)，但 CTOR 全场最高
    specs = [
        ("w_protect", 50, 40, 100, 300, 25, 19),    # 低需求高CTOR
        ("w_a", 900, 800, 700, 2000, 28, 8),
        ("w_b", 800, 700, 600, 1800, 27, 7),
        ("w_c", 700, 600, 500, 1600, 26, 6),
        ("w_d", 600, 500, 400, 1400, 25, 5),
        ("w_e", 500, 400, 300, 1200, 24, 4),
        ("w_f", 400, 300, 200, 1000, 23, 3),
        ("w_g", 300, 200, 150, 800, 22, 2),
        ("w_low_low", 60, 50, 100, 300, 20, 1),    # 低需求低CTOR(应淘汰)
    ]
    r = _run(tmp_path, specs)
    recs = _by_kw(r)
    prot = recs["w_protect"]
    assert prot.passed == 1
    assert prot.channel == "protection"                       # 保护通道
    assert prot.demand_pct < 0.25                             # 搜索量分位<P25
    assert prot.ctor_pct >= 0.75                              # CTOR 达高意图
    assert prot.opportunity_score is not None
    low = recs["w_low_low"]
    assert low.passed == 0
    assert low.reject_category == "low_demand"                # 保护不达标 → 淘汰
    assert "低需求" in low.reject_reason
    # 主通道与保护通道合计 = 全部通过词
    assert r["main"] + r["protection"] == r["passed"]


# ---------------------------------------------------------------------------
# 7. Top N 限制
# ---------------------------------------------------------------------------
def test_top_n_limit(tmp_path):
    specs = [
        ("kw1", 900, 800, 700, 3000, 28, 10),
        ("kw2", 800, 700, 600, 2500, 27, 9),
        ("kw3", 700, 600, 500, 2000, 26, 8),
        ("kw4", 600, 500, 400, 1500, 25, 7),
        ("kw5", 500, 400, 300, 1000, 24, 6),
        ("kw6", 400, 300, 200, 800, 23, 5),
    ]
    r = _run(tmp_path, specs, top_n=2, store=True)   # 入库模式，验证 top_rank 落库一致
    cands = r["candidates"]
    assert len(cands) == 2                                     # 只保留 Top 2
    assert [c.top_rank for c in cands] == [1, 2]
    assert cands[0].opportunity_score >= cands[1].opportunity_score  # 降序
    # 其余通过词无排名
    ranked = {c.keyword for c in cands}
    for rec in r["records"]:
        if rec.passed == 1 and rec.keyword not in ranked:
            assert rec.top_rank is None
    assert r["top_n_cap"] == 2 and r["top_n_config"] == 2
    # 入库行的 top_rank 必须与内存一致（排名后回填）
    conn = connect(tmp_path / "t.db")
    rows = {x["keyword"]: x["top_rank"] for x in
            conn.execute("SELECT keyword, top_rank FROM screening_v1 "
                         "WHERE run_id=(SELECT MAX(run_id) FROM screening_v1)").fetchall()}
    conn.close()
    assert rows["kw1"] == 1 and rows["kw2"] == 2
    assert rows["kw3"] is None and rows["kw6"] is None


# ---------------------------------------------------------------------------
# 8. 商品点击数不进主评分（与搜索量不重复计分）
# ---------------------------------------------------------------------------
def test_product_clicks_not_double_counted(tmp_path):
    specs = [
        ("w_clicks_low", 500, 10, 300, 1500, 26, 6),   # 点击极低
        ("w_clicks_high", 500, 9999, 300, 1500, 26, 6),  # 点击极高(其余完全一致)
    ]
    r = _run(tmp_path, specs)
    recs = _by_kw(r)
    a, b = recs["w_clicks_low"], recs["w_clicks_high"]
    assert a.demand_pct == b.demand_pct                       # 需求分位相同(基于搜索量)
    assert a.opportunity_score == b.opportunity_score         # 点击差异不影响机会分
    assert a.purchase_intent == b.purchase_intent


# ---------------------------------------------------------------------------
# 9. 可扩展黑名单结构：extra_blacklist 注入生效
# ---------------------------------------------------------------------------
def test_extra_blacklist_extension(tmp_path):
    specs = [
        ("custom_junk_word", 9000, 8000, 1500, 50, 30, 19),
        ("พวงกุญแจ", 1000, 900, 800, 500, 27, 9),
    ]
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed(conn, specs)
    flt = NonProductFilter(extra_blacklist={"custom_junk_word"})
    r = V1Screener(conn, store_results=False, nonproduct_filter=flt).run()
    conn.close()
    recs = _by_kw(r)
    assert recs["custom_junk_word"].reject_category == "non_product"
    assert recs["custom_junk_word"].reject_reason == "非商品意图(精确黑名单词: custom_junk_word)"
    assert recs["พวงกุญแจ"].passed == 1
