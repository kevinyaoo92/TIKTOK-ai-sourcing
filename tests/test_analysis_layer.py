# -*- coding: utf-8 -*-
"""V2.1 分析层测试（合成 V2 池 → 规则分析；覆盖验证要求 1~17）。

断言核心: EXCLUDE 不入分析/REVIEW 保持 REVIEW/同簇多机会独立/属性类型分离/
证据与数值不被改变/priority 可复算/幂等/无 AI 可运行/AI 异常不污染/AI 调用可记录。
"""
from __future__ import annotations

import json

from app.analysis.analysis_layer import (
    build_analysis,
    evidence_state,
    parse_identity,
)
from app.database import connect, init_db

W = json.dumps([{"period_type": "week", "period_start": "2026-08-03", "best_keyword": "w"}],
               ensure_ascii=False)
M = json.dumps([{"period_type": "month", "period_start": "2026-07-01", "best_keyword": "m"}],
               ensure_ascii=False)


def _pool_row(run, i, key, name, status, score=0.6, w=0, m=1, l3="饰品", l2=None):
    return (run, f"OP-{i:04d}", "TH", "时尚配件", l2, l3, name, key, score,
            0.8, 0.7, 0.5, 0.2, 1, json.dumps([key]), W if w else "[]", M if m else "[]",
            "2026-07-01", "2026-08-03" if w else "2026-07-31", status)


def _seed(conn):
    run = "pool-run-1"
    cols = ("run_id, opportunity_id, market, level_1_category, level_2_category, "
            "level_3_category, canonical_product_name, canonical_product_key, score, "
            "demand_score, purchase_intent_score, supply_score, opportunity_gap, "
            "evidence_count, source_keywords, weekly_evidence, monthly_evidence, "
            "first_seen, last_seen, category_status")
    rows = [
        _pool_row(run, 1, "hair_clip", "女士发夹", "CORE", score=0.70),
        _pool_row(run, 2, "luxury_hair_clip", "高档发夹", "CORE", score=0.62),
        _pool_row(run, 3, "necklace", "不掉色项链", "CORE", score=0.66, w=1),
        _pool_row(run, 4, "women_belt", "女士腰带", "ADJACENT", score=0.55),
        _pool_row(run, 5, "face_mask", "一次性口罩", "EXCLUDE", score=0.80),
        _pool_row(run, 6, "blue_light_glasses", "防蓝光眼镜", "REVIEW", score=0.71),
    ]
    for r in rows:
        conn.execute(f"INSERT INTO product_opportunities_v2 ({cols}) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    conn.execute("INSERT INTO product_opportunities (run_id, opportunity_id, market, "
                 "canonical_product_key, canonical_product_name, opportunity_score, "
                 "keyword_count, is_final, status, created_at) "
                 "VALUES ('old','OP-0001','TH','necklace','旧项链',0.5,1,1,'final','t1')")
    conn.commit()
    return run


def _fresh(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed(conn)
    return conn


def _ana_by_key(rows):
    return {r.canonical_product_key: r for r in rows}


# 1. V2 pool 读取成功 / 2. 原始 v1 不被修改 / 3. EXCLUDE 不入分析
def test_read_pool_and_exclude(tmp_path):
    conn = _fresh(tmp_path)
    r = build_analysis(conn, store=True)
    st = r["stats"]
    assert st["input"] == 6 and st["analyzed"] == 5
    assert "face_mask" in st["excluded_keys"]
    assert "face_mask" not in _ana_by_key(r["analysis"])
    # 原始 product_opportunities 未被修改
    assert conn.execute("SELECT COUNT(*) FROM product_opportunities").fetchone()[0] == 1
    conn.close()


# 4. REVIEW 保持 REVIEW
def test_review_stays_review(tmp_path):
    conn = _fresh(tmp_path)
    r = build_analysis(conn, store=False)
    a = _ana_by_key(r["analysis"])["blue_light_glasses"]
    assert a.evidence_state == "REVIEW"
    assert "BUSINESS_SCOPE_REVIEW" in json.loads(a.risk_tags)
    assert r["stats"]["evidence_states"].get("REVIEW") == 1
    conn.close()


# 5. CORE/ADJACENT 分类正确
def test_core_adjacent(tmp_path):
    conn = _fresh(tmp_path)
    r = build_analysis(conn, store=False)
    hair_cl = next(c for c in r["clusters"] if "发夹" in c.cluster_name)
    assert hair_cl.cluster_type == "PRODUCT" and hair_cl.core_count == 2
    belt_cl = next(c for c in r["clusters"] if "腰带" in c.cluster_name)
    assert belt_cl.cluster_type == "ADJACENT" and belt_cl.adjacent_count == 1
    conn.close()


# 6+7+8. 同一簇多机会独立存在；不删除原机会；key 不破坏
def test_cluster_keeps_opportunities(tmp_path):
    conn = _fresh(tmp_path)
    n_pool = conn.execute("SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0]
    r = build_analysis(conn, store=True)
    assert conn.execute("SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0] == n_pool
    by = _ana_by_key(r["analysis"])
    assert {"hair_clip", "luxury_hair_clip"} <= set(by)
    # 两成员同一簇，但各自 analysis 行独立
    assert by["hair_clip"].cluster_id == by["luxury_hair_clip"].cluster_id
    assert by["hair_clip"].analysis_id != by["luxury_hair_clip"].analysis_id
    for k, row in (("hair_clip", by["hair_clip"]), ("luxury_hair_clip", by["luxury_hair_clip"])):
        assert row.canonical_product_key == k
    conn.close()


# 9. 属性与类型分离（宁空不编造）
def test_identity_parse():
    assert parse_identity("不掉色项链")["product_type"] == "项链"
    assert parse_identity("不掉色项链")["attributes"] == ["不掉色"]
    assert parse_identity("Y2K项链")["style"] == ["Y2K"]
    assert parse_identity("女士发夹")["target_group"] == ["女士"]
    assert parse_identity("发箍")["product_type"] == "发箍"


# 10. weekly/monthly evidence 不被改变
def test_evidence_untouched(tmp_path):
    conn = _fresh(tmp_path)
    before = {r["canonical_product_key"]: (r["weekly_evidence"], r["monthly_evidence"])
              for r in conn.execute("SELECT canonical_product_key, weekly_evidence, "
                                    "monthly_evidence FROM product_opportunities_v2")}
    build_analysis(conn, store=True)
    after = {r["canonical_product_key"]: (r["weekly_evidence"], r["monthly_evidence"])
             for r in conn.execute("SELECT canonical_product_key, weekly_evidence, "
                                   "monthly_evidence FROM product_opportunities_v2")}
    assert before == after
    conn.close()


# 11. 数值指标不被覆盖
def test_scores_untouched(tmp_path):
    conn = _fresh(tmp_path)
    build_analysis(conn, store=True)
    row = conn.execute("SELECT score, demand_score, purchase_intent_score, opportunity_gap "
                       "FROM product_opportunities_v2 WHERE canonical_product_key='necklace'"
                       ).fetchone()
    assert row["score"] == 0.66 and row["demand_score"] == 0.8
    assert row["purchase_intent_score"] == 0.7 and row["opportunity_gap"] == 0.2
    conn.close()


# 12. priority 可重复计算
def test_priority_repeatable(tmp_path):
    conn = _fresh(tmp_path)
    r1 = build_analysis(conn, store=False)
    r2 = build_analysis(conn, store=False)
    p1 = {c.cluster_id: c.priority for c in r1["clusters"]}
    p2 = {c.cluster_id: c.priority for c in r2["clusters"]}
    assert p1 == p2
    conn.close()


# 13. 分析结果幂等（同池批次重复分析不翻倍）
def test_idempotent(tmp_path):
    conn = _fresh(tmp_path)
    build_analysis(conn, store=True)
    n1 = (conn.execute("SELECT COUNT(*) FROM opportunity_directions").fetchone()[0],
          conn.execute("SELECT COUNT(*) FROM opportunity_clusters").fetchone()[0],
          conn.execute("SELECT COUNT(*) FROM opportunity_analysis").fetchone()[0])
    build_analysis(conn, store=True)
    n2 = (conn.execute("SELECT COUNT(*) FROM opportunity_directions").fetchone()[0],
          conn.execute("SELECT COUNT(*) FROM opportunity_clusters").fetchone()[0],
          conn.execute("SELECT COUNT(*) FROM opportunity_analysis").fetchone()[0])
    assert n1 == n2 and n1[2] == 5
    conn.close()


# 14. 无 DeepSeek 仍可运行基础分析
def test_no_ai_runs(tmp_path):
    conn = _fresh(tmp_path)
    r = build_analysis(conn, store=False)
    assert r["stats"]["deepseek_calls"] == 0 and len(r["analysis"]) == 5
    conn.close()


# 15. AI 异常 JSON 不污染数据库
def test_ai_bad_payload_no_pollution(tmp_path):
    conn = _fresh(tmp_path)
    def bad_fn(candidates):
        raise RuntimeError("网络错误")
    r = build_analysis(conn, store=True, ai_naming_fn=bad_fn)
    assert r["stats"]["ai_failures"] == 1 and r["stats"]["deepseek_calls"] == 1
    # 分析表仍为规则结果，model 全 rule-based，无任何 AI 内容
    assert conn.execute("SELECT COUNT(DISTINCT model) FROM opportunity_analysis"
                        ).fetchone()[0] == 1
    assert conn.execute("SELECT model FROM opportunity_analysis LIMIT 1").fetchone()[0] \
        == "rule-based-v2.1"
    conn.close()


# 16. AI 调用数量可记录（合法 payload 亦计入 calls）
def test_ai_calls_recorded(tmp_path):
    conn = _fresh(tmp_path)
    calls = {"n": 0}

    def good_fn(candidates):
        calls["n"] += 1
        return {"clusters": [{"cluster_key": "x", "cluster_name_zh": "忽略", "cluster_type": "X",
                              "members": []}]}
    r = build_analysis(conn, store=False, ai_naming_fn=good_fn)
    assert calls["n"] == 1 and r["stats"]["deepseek_calls"] == 1
    assert r["stats"]["ai_failures"] == 0
    conn.close()


# 补充: evidence_state 规则单元
def test_evidence_state_rule():
    assert evidence_state(1, 1, 2, "CORE") == "STABLE"
    assert evidence_state(2, 0, 3, "CORE") == "RISING"
    assert evidence_state(1, 0, 1, "CORE") == "RECENT"
    assert evidence_state(0, 1, 3, "CORE") == "WEAK_EVIDENCE"
    assert evidence_state(0, 1, 1, "REVIEW") == "REVIEW"
    assert evidence_state(0, 0, 1, "ADJACENT") == "UNKNOWN"
