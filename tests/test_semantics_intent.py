# -*- coding: utf-8 -*-
"""V1 筛选 → DeepSeek 语义层 测试（需求清单 1~11 + screening 集成）。

覆盖:
  1 PRODUCT（intent_status 与 is_product 一致落库）
  2 NON_PRODUCT
  3 AMBIGUOUS（不确定不强制判断，is_product 存 NULL）
  4 泰语商品词
  5 平台促销词
  6 短词/歧义词
  7 malformed JSON（整批失败、错误保留、不伪造成功）
  8 单项失败隔离
  9 checkpoint/resume（半批成功后断点只处理剩余/失败词）
 10 batch > 30 拒绝（服务层 + analyze_keywords_batch 层）
 11 已成功关键词不重复调用
 12 集成：screening_v1 TopN 候选 → 语义（只处理 top_rank<=limit 的词）
"""
from __future__ import annotations

import json

import pytest

from app import config
from app.ai import deepseek
from app.ai.semantics import SemanticsPipeline
from app.analysis.screening import V1Screener
from app.database import KeywordRepo, SemanticRepo, connect, init_db
from app.models import KeywordRecord

CAT = "时尚配件"
KT = "热门搜索关键词"
PERIOD = "2026-07-01"


def _enable_ai(monkeypatch) -> None:
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-real")


def _fake_chat(messages, **kwargs):
    """按词特征返回意图：amb_ 前缀→AMBIGUOUS；nonprod/ราคา→NON_PRODUCT；其余 PRODUCT。"""
    words = json.loads(messages[-1]["content"])["words"]
    arr = []
    for i, w in enumerate(words):
        if w.startswith("nonprod") or w == "ราคา0.01บาท":
            arr.append({"index": i, "original_keyword": w, "language": "th",
                        "meaning_zh": "促销词(非商品搜索)", "product_category": "",
                        "product_subcategory": "", "canonical_product_name": "",
                        "canonical_product_key": "", "search_terms_1688": [],
                        "intent_status": "NON_PRODUCT", "is_product": 0,
                        "confidence": 0.99})
        elif w.startswith("amb_"):
            arr.append({"index": i, "original_keyword": w, "language": "th",
                        "meaning_zh": "词义过短/过泛，难以判定", "product_category": "",
                        "product_subcategory": "", "canonical_product_name": "",
                        "canonical_product_key": "", "search_terms_1688": [],
                        "intent_status": "AMBIGUOUS", "is_product": 0,
                        "confidence": 0.35})
        elif w == "สร้อยคอแฟชั่น":
            arr.append({"index": i, "original_keyword": w, "language": "th",
                        "meaning_zh": "时尚项链", "product_category": "饰品",
                        "product_subcategory": "项链",
                        "canonical_product_name": "女士项链",
                        "canonical_product_key": "necklace",
                        "search_terms_1688": ["女士时尚项链", "韩版项链", "不锈钢项链"],
                        "intent_status": "PRODUCT", "is_product": 1,
                        "confidence": 0.92})
        else:
            arr.append({"index": i, "original_keyword": w, "language": "th",
                        "meaning_zh": f"{w} 中文含义", "product_category": "饰品",
                        "product_subcategory": "其他",
                        "canonical_product_name": f"测试商品{i}",
                        "canonical_product_key": f"test_{i}",
                        "search_terms_1688": [f"{w} 词1", f"{w} 词2"],
                        "intent_status": "PRODUCT", "is_product": 1,
                        "confidence": 0.9})
    return json.dumps(arr, ensure_ascii=False)


def _mk_pipe(conn, monkeypatch, batch_size=10, fake=None):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(deepseek, "chat_completion", fake or _fake_chat)
    return SemanticsPipeline(conn, batch_size=batch_size, market="TH")


def _items(words):
    return [{"keyword": w, "market": "TH", "category": CAT, "keyword_type": KT,
             "period_start": PERIOD, "attempts": 0} for w in words]


def _row(conn, kw):
    return SemanticRepo(conn).get("TH", kw)


# ---------------------------------------------------------------------------
# 1/2/3/4/5/6 意图三元 + 泰语词 + 促销词 + 歧义词
# ---------------------------------------------------------------------------
def test_intent_product(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _mk_pipe(conn, monkeypatch).process(_items(["สร้อยคอแฟชั่น"]))
    r = _row(conn, "สร้อยคอแฟชั่น")
    assert r["status"] == "success"
    assert r["intent_status"] == "PRODUCT" and r["is_product"] == 1     # 一致
    assert r["meaning_zh"] == "时尚项链"
    conn.close()


def test_intent_non_product(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _mk_pipe(conn, monkeypatch).process(_items(["nonprod_โปรโมชั่น", "ราคา0.01บาท"]))
    for kw in ("nonprod_โปรโมชั่น", "ราคา0.01บาท"):
        r = _row(conn, kw)
        assert r["intent_status"] == "NON_PRODUCT" and r["is_product"] == 0
        assert json.loads(r["search_terms_1688"]) == []
    conn.close()


def test_intent_ambiguous(tmp_path, monkeypatch):
    """无法确定 → AMBIGUOUS（不强制判断），is_product 存 NULL，条目保留。"""
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _mk_pipe(conn, monkeypatch).process(_items(["amb_คำสั้นๆ"]))
    r = _row(conn, "amb_คำสั้นๆ")
    assert r["status"] == "success"
    assert r["intent_status"] == "AMBIGUOUS"
    assert r["is_product"] is None                                     # 未强制
    assert r["confidence"] == pytest.approx(0.35)                      # 低置信度保留
    conn.close()


def test_thai_product_word_and_terms(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _mk_pipe(conn, monkeypatch).process(_items(["สร้อยคอแฟชั่น"]))
    r = _row(conn, "สร้อยคอแฟชั่น")
    assert r["language"] == "th"
    assert r["product_category"] == "饰品" and r["product_subcategory"] == "项链"
    terms = json.loads(r["search_terms_1688"])
    assert len(terms) == 3 and all(isinstance(t, str) for t in terms)
    conn.close()


def test_platform_promo_word(tmp_path, monkeypatch):
    """平台促销词（即使搜索量高）→ NON_PRODUCT，不生成 1688 词。"""
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _mk_pipe(conn, monkeypatch).process(_items(["nonprod_ราคา0.01บาทส่งฟรี"]))
    r = _row(conn, "nonprod_ราคา0.01บาทส่งฟรี")
    assert r["intent_status"] == "NON_PRODUCT" and r["is_product"] == 0
    conn.close()


def test_short_ambiguous_word(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _mk_pipe(conn, monkeypatch).process(_items(["amb_ของ"]))
    r = _row(conn, "amb_ของ")
    assert r["intent_status"] == "AMBIGUOUS"
    conn.close()


# ---------------------------------------------------------------------------
# 7. malformed JSON：整批失败、错误保留、不写伪造成功
# ---------------------------------------------------------------------------
def test_malformed_json_no_fake_success(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)
    monkeypatch.setattr(deepseek, "chat_completion",
                        lambda messages, **kwargs: "这不是 JSON")      # 非法
    pipe = SemanticsPipeline(conn, batch_size=30, market="TH")
    stats = pipe.process(_items([f"w{i}" for i in range(35)]))          # 2 批全坏
    assert stats["failed"] == 35 and stats["success"] == 0
    for i in range(35):
        r = _row(conn, f"w{i}")
        assert r["status"] == "failed"
        assert r["error"] and "JSON" in r["error"]
        assert r["meaning_zh"] is None                                  # 无伪造结果
        assert r["intent_status"] is None
    conn.close()


# ---------------------------------------------------------------------------
# 8. 单项失败隔离（intent_status 与 is_product 不一致 → 仅该项失败）
# ---------------------------------------------------------------------------
def test_single_item_inconsistency_isolated(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)

    def tricky(messages, **kwargs):
        arr = json.loads(_fake_chat(messages, **kwargs))
        for item in arr:
            if item["original_keyword"] == "bad_inconsistent":
                item["intent_status"] = "PRODUCT"
                item["is_product"] = 0                                 # 自相矛盾
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", tricky)

    pipe = _mk_pipe(conn, monkeypatch, fake=tricky)
    stats = pipe.process(_items(["good1", "bad_inconsistent", "good2"]))
    assert stats["success"] == 2 and stats["failed"] == 1
    bad = _row(conn, "bad_inconsistent")
    assert bad["status"] == "failed"
    assert "不一致" in bad["error"]
    assert _row(conn, "good1")["status"] == "success"
    conn.close()


# ---------------------------------------------------------------------------
# 9. checkpoint/resume：半批成功 + 单项失败 → 断点后只处理失败/剩余词
# ---------------------------------------------------------------------------
def test_checkpoint_resume(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)
    call_log = {"words": []}

    def flaky(messages, **kwargs):
        words = json.loads(messages[-1]["content"])["words"]
        call_log["words"].append(words)
        arr = json.loads(_fake_chat(messages, **kwargs))
        # "kw_fail" 永远返回坏项（缺 confidence）
        for item in arr:
            if item["original_keyword"] == "kw_fail":
                item.pop("confidence")
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", flaky)

    pipe = SemanticsPipeline(conn, batch_size=30, market="TH")
    words = [f"kw{i}" for i in range(20)] + ["kw_fail"]
    first = pipe.process(_items(words))
    assert first["success"] == 20 and first["failed"] == 1

    # 断点续跑：只应处理 kw_fail（其余已 success）
    pipe2 = SemanticsPipeline(conn, batch_size=30, market="TH")
    eligible = pipe2.filter_eligible(_items(words))
    assert [c["keyword"] for c in eligible] == ["kw_fail"]
    second = pipe2.process(eligible)
    assert second["requested"] == 1
    # 首次调用覆盖全部 21 词；断点续跑只发 1 词（kw_fail）→ 不重复调用成功词
    assert len(call_log["words"]) == 2
    assert call_log["words"][1] == ["kw_fail"]
    conn.close()


# ---------------------------------------------------------------------------
# 10. batch > 30 拒绝（服务层 + deepseek 层）
# ---------------------------------------------------------------------------
def test_batch_over_30_rejected_both_layers(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    with pytest.raises(ValueError):
        SemanticsPipeline(conn, batch_size=31, market="TH")
    _enable_ai(monkeypatch)
    with pytest.raises(ValueError):
        deepseek.analyze_keywords_batch([f"w{i}" for i in range(31)])  # >30 拒绝
    conn.close()


# ---------------------------------------------------------------------------
# 11. 已成功关键词不重复调用（screening 候选源）
# ---------------------------------------------------------------------------
def test_success_keywords_not_recalled(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    calls = {"n": 0}

    def spy(messages, **kwargs):
        calls["n"] += 1
        return _fake_chat(messages, **kwargs)
    _enable_ai(monkeypatch)
    monkeypatch.setattr(deepseek, "chat_completion", spy)

    pipe = SemanticsPipeline(conn, batch_size=30, market="TH")
    words = [f"kw{i}" for i in range(5)]
    pipe.process(_items(words))
    assert calls["n"] == 1

    # 同一批候选再来一次 → filter_eligible 全空 → 零调用
    pipe.process(pipe.filter_eligible(_items(words)))
    assert calls["n"] == 1
    conn.close()


# ---------------------------------------------------------------------------
# 12. 集成：screening_v1 TopN 候选 → 语义层（只处理 top_rank<=limit）
# ---------------------------------------------------------------------------
def _seed_keyword_data(conn, n: int):
    repo = KeywordRepo(conn)
    repo.insert_many([
        KeywordRecord(period_start=PERIOD, period_end="2026-07-31", period_type="month",
                      market="TH", category=CAT, keyword_type=KT, keyword=f"w{i}",
                      search_volume=float(900 - i * 50), product_clicks=float(800 - i * 50),
                      sku_sales_index=float(700 - i * 40), on_sale_products=500 + i * 300,
                      ctr_index=28.0, ctor_score=6.0 + i * 0.5, source_file="t.xlsx")
        for i in range(n)
    ])


def test_screening_to_semantics_topn_only(tmp_path, monkeypatch):
    """只处理 screening_v1 通过且 top_rank<=AI_CANDIDATE_LIMIT(测试用3) 的词。"""
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed_keyword_data(conn, 8)
    V1Screener(conn, top_n=8, store_results=True).run()          # 生成 screening_v1 批次

    called = {"keywords": []}

    def record(messages, **kwargs):
        words = json.loads(messages[-1]["content"])["words"]
        called["keywords"].extend(words)
        return _fake_chat(messages, **kwargs)
    _enable_ai(monkeypatch)
    monkeypatch.setattr(deepseek, "chat_completion", record)

    pipe = SemanticsPipeline(conn, batch_size=30, market="TH")
    stats = pipe.run_from_screening(top_rank_max=3)               # AI_CANDIDATE_LIMIT=3
    assert stats["success"] == 3 and stats["failed"] == 0
    assert len(called["keywords"]) == 3                           # 只处理 top3，非全部8
    # top3 与 screening top_rank<=3 对应
    top3 = [r["keyword"] for r in conn.execute(
        "SELECT keyword FROM screening_v1 WHERE run_id=(SELECT MAX(run_id) FROM screening_v1) "
        "AND top_rank<=3 ORDER BY top_rank").fetchall()]
    assert called["keywords"] == top3
    assert len(SemanticRepo(conn).count_by_status("TH")) > 0
    # 其余 top4~8 未调用、未入库
    done = {r["original_keyword"] for r in conn.execute(
        "SELECT original_keyword FROM ai_semantic_results").fetchall()}
    assert done == set(top3)
    conn.close()
