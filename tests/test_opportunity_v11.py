# -*- coding: utf-8 -*-
"""V1.1 商品机会归一化层测试（需求清单 1~13）。

核心断言：
- 同 canonical_product_key → 同一商品机会；不同商品形态不合并；
- 禁止同机会关键词搜索量相加（组 Demand=最强关键词，非求和）；
- EvidenceConsistency 封顶（keyword_count 不能无限提分）；
- 最终 ≤9、不足 9 允许、无重复；key 缺失安全失败；
- PRODUCT 须 2~5 个 1688 词 / NON_PRODUCT 不生成；
- checkpoint 不重复调用；batch>30 拒绝；总批数>3 拒绝。
"""
from __future__ import annotations

import json

import pytest

from app import config
from app.analysis.opportunity_v11 import build_opportunities, evidence_score
from app.database import connect, init_db
from app.models import ScreeningRecord, SemanticRecord

CAT = "时尚配件"
KT = "热门搜索关键词"
PERIOD = "2026-07-01"
RUN = "test-run"


def _seed(conn, specs) -> None:
    """specs: list of dict(keyword, key, name, demand, intent, supply, gap, v1score)

    直接构造 screening_v1 行 + ai_semantic_results success(PRODUCT) 行，
    便于精确控制聚类/评分输入（绕过 AI）。
    """
    scr = conn.cursor()
    sem = conn.cursor()
    scr_sql = (
        "INSERT INTO screening_v1 (run_id, market, category, keyword_type, period_type, "
        "period_start, keyword, passed, channel, demand_pct, supply_pct, opportunity_gap, "
        "ctor_pct, sku_pct, purchase_intent, opportunity_score, top_rank) "
        "VALUES (?,?,?,?,?,?,?,1,'main',?,?,?,0.5,0.5,?,?,?)",
    )
    sem_sql = (
        "INSERT INTO ai_semantic_results (original_keyword, market, category, keyword_type, "
        "period_start, language, meaning_zh, product_category, product_subcategory, "
        "canonical_product_name, canonical_product_key, search_terms_1688, is_product, "
        "intent_status, confidence, model, status, attempts, error, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'PRODUCT',0.9,'m','success',1,NULL,?,?)",
    )
    for i, s in enumerate(specs, start=1):
        scr.execute(scr_sql[0], (RUN, "TH", CAT, KT, "month", PERIOD, s["keyword"],
                                 s["demand"], s["supply"], s["gap"], s["intent"], s["v1score"], i))
        terms = json.dumps(s.get("terms", ["搜索词甲", "搜索词乙"]), ensure_ascii=False)
        sem.execute(sem_sql[0], (s["keyword"], "TH", CAT, KT, PERIOD, "th", s["name"] + "含义",
                                 "饰品", "其他", s["name"], s["key"], terms, "t1", "t1"))
    conn.commit()


def _build(conn, store=False):
    return build_opportunities(conn, market="TH", store=store)


def _mk(specs):
    return specs


def _fresh(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# 1. 同义关键词 → 同一商品机会
# ---------------------------------------------------------------------------
def test_synonyms_same_opportunity(tmp_path):
    conn = _fresh(tmp_path)
    _seed(conn, [
        {"keyword": "发圈", "key": "hair_tie", "name": "女士发圈",
         "demand": 0.9, "intent": 0.8, "supply": 0.5, "gap": 0.4, "v1score": 0.75},
        {"keyword": "时尚发圈", "key": "hair_tie", "name": "女士发圈",
         "demand": 0.7, "intent": 0.7, "supply": 0.6, "gap": 0.1, "v1score": 0.6},
        {"keyword": "扎头发橡皮筋", "key": "hair_tie", "name": "女士发圈",
         "demand": 0.5, "intent": 0.6, "supply": 0.7, "gap": -0.2, "v1score": 0.5},
    ])
    r = _build(conn)
    assert r["stats"]["groups"] == 1                       # 只形成一个商品机会
    opp = r["opportunities"][0]
    assert opp.canonical_product_key == "hair_tie"
    assert opp.keyword_count == 3
    assert json.loads(opp.source_keywords) == ["发圈", "时尚发圈", "扎头发橡皮筋"]
    assert opp.best_keyword == "发圈"                       # V1 分最高者
    conn.close()


# ---------------------------------------------------------------------------
# 2. 不同商品形态不能错误合并（同属 Hair Accessories 但形态不同）
# ---------------------------------------------------------------------------
def test_distinct_forms_not_merged(tmp_path):
    conn = _fresh(tmp_path)
    _seed(conn, [
        {"keyword": "发圈", "key": "hair_tie", "name": "女士发圈",
         "demand": 0.9, "intent": 0.8, "supply": 0.5, "gap": 0.4, "v1score": 0.75},
        {"keyword": "发夹", "key": "hair_clip", "name": "女士发夹",
         "demand": 0.8, "intent": 0.7, "supply": 0.4, "gap": 0.4, "v1score": 0.7},
        {"keyword": "发箍", "key": "hair_band", "name": "女士发箍",
         "demand": 0.7, "intent": 0.6, "supply": 0.3, "gap": 0.4, "v1score": 0.65},
    ])
    r = _build(conn)
    keys = {o.canonical_product_key for o in r["opportunities"]}
    assert keys == {"hair_tie", "hair_clip", "hair_band"}  # 三个独立机会
    conn.close()


# ---------------------------------------------------------------------------
# 3. 多关键词不能简单相加（组 Demand = 最强关键词，非求和）
# ---------------------------------------------------------------------------
def test_no_sum_of_search_demand(tmp_path):
    conn = _fresh(tmp_path)
    _seed(conn, [
        {"keyword": "k_high", "key": "g1", "name": "商品A",
         "demand": 0.9, "intent": 0.8, "supply": 0.4, "gap": 0.5, "v1score": 0.8},
        {"keyword": "k_low", "key": "g1", "name": "商品A",
         "demand": 0.5, "intent": 0.6, "supply": 0.6, "gap": -0.1, "v1score": 0.5},
    ])
    r = _build(conn)
    opp = r["opportunities"][0]
    assert opp.best_demand_pct == 0.9                       # 最强词，而不是 1.4
    assert opp.demand_component == pytest.approx(0.35 * 0.9)
    conn.close()


# ---------------------------------------------------------------------------
# 4. keyword_count 不能无限提高分数（evidence 封顶）
# ---------------------------------------------------------------------------
def test_keyword_count_cannot_inflate_score(tmp_path):
    conn = _fresh(tmp_path)
    base = {"demand": 0.8, "intent": 0.7, "supply": 0.4, "gap": 0.4, "v1score": 0.7}
    specs = [{"keyword": f"a{i}", "key": "grp_a", "name": "商品A", **base} for i in range(3)]
    specs += [{"keyword": f"b{i}", "key": "grp_b", "name": "商品B", **base} for i in range(10)]
    _seed(conn, specs)
    r = _build(conn)
    by_key = {o.canonical_product_key: o for o in r["opportunities"]}
    a, b = by_key["grp_a"], by_key["grp_b"]
    assert a.evidence_consistency == b.evidence_consistency == 1.0   # 3 词已封顶
    assert a.opportunity_score == pytest.approx(b.opportunity_score)  # 10 词不比 3 词高
    # evidence 曲线本身：1→0.4, 2→0.7, ≥3→1.0
    assert evidence_score(1) == 0.4 and evidence_score(2) == 0.7 and evidence_score(3) == 1.0
    assert evidence_score(20) == 1.0
    conn.close()


# ---------------------------------------------------------------------------
# 5. 最终结果最多 9 个
# ---------------------------------------------------------------------------
def test_final_max_9(tmp_path):
    conn = _fresh(tmp_path)
    specs = []
    for i in range(12):
        d = 0.9 - i * 0.02
        specs.append({"keyword": f"kw{i}", "key": f"op_{i}", "name": f"商品{i}",
                      "demand": d, "intent": 0.7, "supply": 0.3, "gap": 0.5, "v1score": 0.6})
    _seed(conn, specs)
    r = _build(conn)
    assert r["stats"]["groups"] == 12
    assert len(r["final"]) == 9
    assert [o.final_rank for o in r["final"]] == list(range(1, 10))
    assert all(o.is_final == 1 for o in r["final"])
    assert sum(1 for o in r["opportunities"] if o.is_final == 1) == 9
    conn.close()


# ---------------------------------------------------------------------------
# 6. 不足 9 个时允许少于 9
# ---------------------------------------------------------------------------
def test_final_less_than_9_allowed(tmp_path):
    conn = _fresh(tmp_path)
    _seed(conn, [
        {"keyword": f"kw{i}", "key": f"op_{i}", "name": f"商品{i}",
         "demand": 0.8, "intent": 0.7, "supply": 0.4, "gap": 0.4, "v1score": 0.6}
        for i in range(5)
    ])
    r = _build(conn)
    assert len(r["final"]) == 5                                # 不凑数到 9
    assert r["stats"]["final_count"] == 5
    conn.close()


# 6b. 落库幂等：重复运行不产生重复机会行（只保留最新一份完整快照）
def test_store_idempotent_no_duplicate_rows(tmp_path):
    conn = _fresh(tmp_path)
    _seed(conn, [
        {"keyword": f"kw{i}", "key": f"op_{i}", "name": f"商品{i}",
         "demand": 0.8, "intent": 0.7, "supply": 0.4, "gap": 0.4, "v1score": 0.6}
        for i in range(5)
    ])
    r1 = build_opportunities(conn, market="TH", store=True)
    n1 = conn.execute("SELECT COUNT(*) FROM product_opportunities").fetchone()[0]
    keys1 = sorted(x[0] for x in conn.execute(
        "SELECT canonical_product_key FROM product_opportunities"))
    assert n1 == 5 and r1["stats"]["stored"] == 5
    # 第二次运行：快照重建，行数不变、key 集合不变（无重复/无翻倍）
    r2 = build_opportunities(conn, market="TH", store=True)
    n2 = conn.execute("SELECT COUNT(*) FROM product_opportunities").fetchone()[0]
    keys2 = sorted(x[0] for x in conn.execute(
        "SELECT canonical_product_key FROM product_opportunities"))
    assert n2 == 5 and n2 == n1
    assert keys2 == keys1
    assert len(keys2) == len(set(keys2))                       # 无重复 key
    assert r1["run_id"] != r2["run_id"]                        # 批次标识更新但表不翻倍
    conn.close()


# ---------------------------------------------------------------------------
# 7. 同一商品机会不能重复出现
# ---------------------------------------------------------------------------
def test_no_duplicate_opportunity(tmp_path):
    conn = _fresh(tmp_path)
    _seed(conn, [
        {"keyword": f"kw{i}", "key": f"op_{i}", "name": f"商品{i}",
         "demand": 0.8, "intent": 0.7, "supply": 0.4, "gap": 0.4, "v1score": 0.6}
        for i in range(12)
    ])
    r = _build(conn)
    keys = [o.canonical_product_key for o in r["final"]]
    assert len(keys) == len(set(keys))                         # 无重复
    assert len(keys) == 9
    conn.close()


# ---------------------------------------------------------------------------
# 8. canonical_product_key 缺失/非法 → 安全失败
# ---------------------------------------------------------------------------
def test_missing_key_safe_failure(tmp_path, monkeypatch):
    # 8a. 语义校验层：PRODUCT 缺 key → 该项 failed，同批其它成功
    from app.ai import deepseek
    from app.ai.semantics import SemanticsPipeline
    from app.database import SemanticRepo
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-t")

    def no_key_fake(messages, **kwargs):
        words = json.loads(messages[-1]["content"])["words"]
        arr = []
        for i, w in enumerate(words):
            item = {"index": i, "original_keyword": w, "language": "th",
                    "meaning_zh": "含义", "product_category": "饰品",
                    "product_subcategory": "其他",
                    "canonical_product_name": f"商品{i}",
                    "canonical_product_key": "good_key",
                    "search_terms_1688": ["a", "b"], "intent_status": "PRODUCT",
                    "is_product": 1, "confidence": 0.9}
            if w == "no_key":
                item["canonical_product_key"] = ""              # 缺 key
            arr.append(item)
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", no_key_fake)
    conn = _fresh(tmp_path)
    pipe = SemanticsPipeline(conn, market="TH")
    pipe.process([{"keyword": "ok1", "attempts": 0, "market": "TH"},
                  {"keyword": "no_key", "attempts": 0, "market": "TH"}])
    assert SemanticRepo(conn).get("TH", "no_key")["status"] == "failed"
    assert "canonical_product_key" in (SemanticRepo(conn).get("TH", "no_key")["error"] or "")
    assert SemanticRepo(conn).get("TH", "ok1")["status"] == "success"
    conn.close()

    # 8b. 聚类层：直接入库的非法/缺失 key 行被跳过且不崩溃
    conn = _fresh(tmp_path)
    conn.execute("INSERT INTO ai_semantic_results (original_keyword, market, status, "
                 "intent_status, canonical_product_key) VALUES ('x','TH','success','PRODUCT',NULL)")
    conn.commit()
    r = _build(conn)                                          # 不崩
    assert r["stats"]["matched_candidates"] == 0 and r["final"] == []
    conn.close()


# ---------------------------------------------------------------------------
# 9. PRODUCT 必须有 2~5 个 1688 搜索词
# ---------------------------------------------------------------------------
def test_product_requires_2to5_terms(tmp_path, monkeypatch):
    from app.ai import deepseek
    from app.ai.semantics import SemanticsPipeline
    from app.database import SemanticRepo
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-t")

    def short_terms(messages, **kwargs):
        words = json.loads(messages[-1]["content"])["words"]
        arr = [{"index": i, "original_keyword": w, "language": "th", "meaning_zh": "含义",
                "product_category": "饰品", "product_subcategory": "其他",
                "canonical_product_name": "商品", "canonical_product_key": "good_key",
                "search_terms_1688": ["只有一个词"], "intent_status": "PRODUCT",
                "is_product": 1, "confidence": 0.9} for i, w in enumerate(words)]
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", short_terms)
    conn = _fresh(tmp_path)
    SemanticsPipeline(conn, market="TH").process(
        [{"keyword": "kw", "attempts": 0, "market": "TH"}])
    assert "2~5" in (SemanticRepo(conn).get("TH", "kw")["error"] or "")
    conn.close()


# ---------------------------------------------------------------------------
# 10. NON_PRODUCT 不生成 1688 搜索词
# ---------------------------------------------------------------------------
def test_non_product_no_terms(tmp_path, monkeypatch):
    from app.ai import deepseek
    from app.ai.semantics import SemanticsPipeline
    from app.database import SemanticRepo
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-t")

    def nonprod(messages, **kwargs):
        words = json.loads(messages[-1]["content"])["words"]
        arr = [{"index": i, "original_keyword": w, "language": "th",
                "meaning_zh": "促销", "product_category": "", "product_subcategory": "",
                "canonical_product_name": "", "canonical_product_key": "",
                "search_terms_1688": [], "intent_status": "NON_PRODUCT",
                "is_product": 0, "confidence": 0.95} for i, w in enumerate(words)]
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", nonprod)
    conn = _fresh(tmp_path)
    SemanticsPipeline(conn, market="TH").process(
        [{"keyword": "ราคา0.01บาท", "attempts": 0, "market": "TH"}])
    row = SemanticRepo(conn).get("TH", "ราคา0.01บาท")
    assert row["intent_status"] == "NON_PRODUCT"
    assert json.loads(row["search_terms_1688"]) == []
    conn.close()


# ---------------------------------------------------------------------------
# 11. checkpoint 不会重复调用（成功词第二次 0 调用）
# ---------------------------------------------------------------------------
def test_checkpoint_no_recall(tmp_path, monkeypatch):
    from app.ai import deepseek
    from app.ai.semantics import SemanticsPipeline
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-t")
    calls = {"n": 0}

    def spy(messages, **kwargs):
        calls["n"] += 1
        words = json.loads(messages[-1]["content"])["words"]
        arr = [{"index": i, "original_keyword": w, "language": "th", "meaning_zh": "含义",
                "product_category": "饰品", "product_subcategory": "其他",
                "canonical_product_name": "商品", "canonical_product_key": f"k_{i}",
                "search_terms_1688": ["a", "b"], "intent_status": "PRODUCT",
                "is_product": 1, "confidence": 0.9} for i, w in enumerate(words)]
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", spy)
    conn = _fresh(tmp_path)
    pipe = SemanticsPipeline(conn, batch_size=30, market="TH")
    items = [{"keyword": f"kw{i}", "attempts": 0, "market": "TH"} for i in range(5)]
    pipe.process(items)
    assert calls["n"] == 1
    pipe.process(pipe.filter_eligible(items))                    # 断点
    assert calls["n"] == 1                                       # 不重复
    conn.close()


# ---------------------------------------------------------------------------
# 12. batch > 30 拒绝
# ---------------------------------------------------------------------------
def test_batch_over_30_rejected_v11(tmp_path, monkeypatch):
    from app.ai import deepseek
    from app.ai.semantics import SemanticsPipeline
    conn = _fresh(tmp_path)
    with pytest.raises(ValueError):
        SemanticsPipeline(conn, batch_size=31, market="TH")
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-t")
    with pytest.raises(ValueError):
        deepseek.analyze_keywords_batch([f"w{i}" for i in range(31)])
    conn.close()


# ---------------------------------------------------------------------------
# 13. 总批数超过 3 时拒绝执行
# ---------------------------------------------------------------------------
def test_total_batches_over_3_rejected():
    from app.ai.semantics import planned_batches
    assert planned_batches(80, 30) == 3                         # 30+30+20 = 3 批(边界通过)
    assert planned_batches(81, 30) == 3
    assert planned_batches(90, 30) == 3
    assert planned_batches(91, 30) == 4                         # >3 批 → 超预算
    assert planned_batches(0, 30) == 0
    assert planned_batches(121, 30) == 5
    # 与硬预算对照
    assert planned_batches(80, 30) <= config.AI_MAX_BATCHES
    assert planned_batches(91, 30) > config.AI_MAX_BATCHES
