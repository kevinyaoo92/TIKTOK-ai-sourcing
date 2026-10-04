# -*- coding: utf-8 -*-
"""机会分析测试：确定性评分 + 可解释理由 + 历史环比 + 入库/替换语义。

场景（同一类目两期月快照）：
  上期(2026-06): A=50, B=200, C=100, D=100
  本期(2026-07): A=400(大涨) B=300(下滑) C=200(持平) D=100(持平)；E 为新上榜
断言全部基于可复算的公式（分位/线性增长映射），无随机成分。
"""
from __future__ import annotations

import json

import pytest

from app.analysis import opportunity as opp
from app.database import KeywordRepo, OpportunityRepo, connect, init_db
from app.models import KeywordRecord

CAT = "时尚配件"
KT = "热门搜索关键词"
PERIOD = "month"


def _mk(period: str, kw: str, vol: float, clicks: float, sku: float,
        onsale: int, price: float = 10.0) -> KeywordRecord:
    return KeywordRecord(
        period_start=period, period_end=period, period_type=PERIOD,
        market="TH", category=CAT, keyword_type=KT, keyword=kw,
        search_volume=vol, product_clicks=clicks, sku_sales_index=sku,
        on_sale_products=onsale, avg_price=price, source_file="test.xlsx",
    )


def _seed_two_periods(conn) -> None:
    repo = KeywordRepo(conn)
    # 上期 2026-06-01（D 持平、E 不存在 → 本期 E 为新上榜）
    repo.insert_many([
        _mk("2026-06-01", "A", 50, 30, 20, 5000),
        _mk("2026-06-01", "B", 500, 250, 200, 100),
        _mk("2026-06-01", "C", 100, 50, 40, 200),
        _mk("2026-06-01", "D", 100, 10, 5, 9000),
    ])
    # 本期 2026-07-01（最新 → 默认基准）
    repo.insert_many([
        _mk("2026-07-01", "A", 400, 300, 200, 50),     # 搜索 50→400 暴涨、在售最少
        _mk("2026-07-01", "B", 300, 150, 120, 100),    # 500→300 下滑但仍高
        _mk("2026-07-01", "C", 200, 100, 80, 200),     # 100→200 翻倍
        _mk("2026-07-01", "D", 100, 20, 10, 400),      # 持平、在售多竞争大
        _mk("2026-07-01", "E", 80, 60, 30, 300),       # 新上榜
    ])


def test_analysis_deterministic_and_explainable(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed_two_periods(conn)

    r = opp.analyze(conn, market="TH", category=CAT, keyword_type=KT, period_type=PERIOD)
    recs = {x.keyword: x for x in r["records"]}

    assert r["baseline"] == "2026-07-01"
    assert r["previous"] == "2026-06-01"
    assert r["cohort_size"] == 5
    assert len(r["records"]) == 5
    # 机会分降序
    scores = [x.opportunity_score for x in r["records"]]
    assert scores == sorted(scores, reverse=True)

    # 需求分位(标准中位秩,含自身): 本期搜索量 400/300/200/100/80 → A=90, B=70, C=50, D=30, E=10
    assert recs["A"].demand_score == 90.0
    assert recs["B"].demand_score == 70.0
    assert recs["E"].demand_score == 10.0
    # 增长线性映射：A 涨 7 倍→100；B 跌 40%→10；C 翻倍→100；D 持平→50；E 新上榜→默认 65
    assert recs["A"].growth_score == 100.0
    assert recs["B"].growth_score == 10.0
    assert recs["C"].growth_score == 100.0
    assert recs["D"].growth_score == 50.0
    assert recs["E"].growth_score == opp.config.NEW_KEYWORD_GROWTH_SCORE
    assert recs["B"].growth_rate == pytest.approx(-0.4)
    # 竞争(分位反转,含自身): 在售 50/100/200/300/400 → A=90 竞争分最高，D=10 最低
    assert recs["A"].competition_score == 90.0
    assert recs["B"].competition_score == 70.0
    assert recs["C"].competition_score == 50.0
    assert recs["E"].competition_score == 30.0
    assert recs["D"].competition_score == 10.0
    # A 综合机会分最高（需求+增长+销售+竞争全面领先）
    assert recs["A"].opportunity_score == max(x.opportunity_score for x in r["records"])
    # 全部因素分都在 0~100
    for x in r["records"]:
        for f in ("demand_score", "growth_score", "click_score", "sales_score",
                  "competition_score"):
            v = getattr(x, f)
            assert v is None or 0.0 <= v <= 100.0
        assert 0.0 <= x.opportunity_score <= 100.0
        assert x.label in ("高机会", "中机会", "低机会")
        reasons = json.loads(x.reasons)
        assert len(reasons) >= 6
        assert any("【综合】" in s for s in reasons)
        assert any("【需求】" in s for s in reasons)
        assert any("【增长】" in s for s in reasons)
    conn.close()


def test_single_period_no_growth_signal(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    KeywordRepo(conn).insert_many([
        _mk("2026-07-01", "A", 400, 300, 200, 50),
        _mk("2026-07-01", "B", 300, 150, 120, 100),
    ])
    r = opp.analyze(conn)
    assert r["previous"] is None
    for x in r["records"]:
        assert x.growth_score == opp.config.NEW_KEYWORD_GROWTH_SCORE  # 无上期→默认分
        assert x.search_volume_prev is None
        assert any("上期" in s and "无此词" in s for s in json.loads(x.reasons))


def test_store_and_replace_run(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed_two_periods(conn)
    repo = OpportunityRepo(conn)

    r1 = opp.analyze(conn)
    assert opp.store(conn, r1) == 5                       # 追加历史
    r2 = opp.analyze(conn)
    assert opp.store(conn, r2) == 5                       # 同基准再存一批
    assert len(repo.latest("TH", CAT, KT, PERIOD)) == 5   # 读最近一批不受旧批干扰
    assert repo.delete_snapshot_batch("TH", CAT, KT, PERIOD, "2026-07-01") == 10
    assert opp.store(conn, r2, replace_run=True) == 5     # replace 语义
    assert repo.delete_snapshot_batch("TH", CAT, KT, PERIOD, "2026-07-01") == 5
    conn.close()
