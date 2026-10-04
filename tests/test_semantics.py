# -*- coding: utf-8 -*-
"""AI 语义流水线测试（核心行为；候选源无关：直接构造候选 items 走 process）。

所有测试注入 chat_completion 替身（不访问网络/不调用真实 DeepSeek），
但 analyze_keywords_batch 的 JSON 解析、intent_status 校验、批处理编排、落库逻辑全部真实执行。
"""
from __future__ import annotations

import json

import pytest

from app import config
from app.ai import deepseek
from app.ai.deepseek import DeepSeekDisabledError
from app.ai.semantics import SemanticsPipeline
from app.database import SemanticRepo, connect, init_db
from app.models import KeywordRecord, SemanticRecord  # noqa: F401 (seed 兼容保留)

CAT = "时尚配件"
KT = "热门搜索关键词"
PERIOD = "2026-07-01"

# 规范示例词：สร้อยคอแฟชั่น（时尚项链，PRODUCT）
SAMPLE = {
    "meaning_zh": "时尚项链",
    "product_category": "饰品",
    "product_subcategory": "项链",
    "canonical_product_name": "女士项链",
    "canonical_product_key": "necklace",
    "search_terms_1688": ["女士时尚项链", "韩版项链", "不锈钢项链"],
    "intent_status": "PRODUCT",
    "is_product": 1,
    "confidence": 0.92,
}


def _item(kw: str) -> dict:
    """构造测试候选 item（供 process 消费，attempts 默认 0）。"""
    return {"keyword": kw, "market": "TH", "category": CAT, "keyword_type": KT,
            "period_start": PERIOD, "attempts": 0}


def _items(words: list[str]) -> list[dict]:
    return [_item(w) for w in words]


def _enable_ai(monkeypatch) -> None:
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-real")


def _fake_chat(messages: list[dict], **kwargs) -> str:
    """通用替身：读取输入 words，返回与真实任务同构的确定性 JSON 数组（含 intent_status）。"""
    payload = json.loads(messages[-1]["content"])
    words = payload["words"]
    arr = []
    for i, w in enumerate(words):
        if w.startswith("nonprod") or w == "ราคา0.01บาท":
            arr.append({
                "index": i, "original_keyword": w, "language": "th",
                "meaning_zh": "促销/价格类短语（非商品搜索）",
                "product_category": "", "product_subcategory": "",
                "canonical_product_name": "", "canonical_product_key": "",
                "search_terms_1688": [], "intent_status": "NON_PRODUCT",
                "is_product": 0, "confidence": 0.99,
            })
        elif w == "สร้อยคอแฟชั่น":
            arr.append({"index": i, "original_keyword": w, "language": "th", **SAMPLE})
        else:
            arr.append({
                "index": i, "original_keyword": w, "language": "th",
                "meaning_zh": f"{w} 的中文含义", "product_category": "饰品",
                "product_subcategory": "其他",
                "canonical_product_name": f"测试商品{i}", "canonical_product_key": f"test_{i}",
                "search_terms_1688": [f"{w} 搜索词1", f"{w} 搜索词2"],
                "intent_status": "PRODUCT", "is_product": 1, "confidence": 0.9,
            })
    return json.dumps(arr, ensure_ascii=False)


def _mk_pipeline(conn, monkeypatch, batch_size=10, fake=None):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(deepseek, "chat_completion", fake or _fake_chat)
    return SemanticsPipeline(conn, batch_size=batch_size, market="TH")


# ---------------------------------------------------------------------------
# 1/2/3. 泰语商品词解析 + 商品类别 + 1688 搜索词（PRODUCT 落库）
# ---------------------------------------------------------------------------
def test_parse_thai_keyword_meaning_category_and_terms(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    pipe = _mk_pipeline(conn, monkeypatch)

    stats = pipe.process(_items(["สร้อยคอแฟชั่น", "พวงกุญแจ"]))
    assert stats["success"] == 2 and stats["failed"] == 0

    row = SemanticRepo(conn).get("TH", "สร้อยคอแฟชั่น")
    assert row["status"] == "success"
    assert row["intent_status"] == "PRODUCT"
    assert row["is_product"] == 1
    assert row["meaning_zh"] == SAMPLE["meaning_zh"]
    assert row["product_category"] == "饰品"
    assert row["product_subcategory"] == "项链"
    terms = json.loads(row["search_terms_1688"])
    assert isinstance(terms, list) and terms == SAMPLE["search_terms_1688"]
    assert row["confidence"] == pytest.approx(0.92)
    assert row["model"] == config.DEEPSEEK_MODEL
    conn.close()


# ---------------------------------------------------------------------------
# 4. 非商品关键词识别（NON_PRODUCT / is_product=0 / terms=[]）
# ---------------------------------------------------------------------------
def test_non_product_keyword_recognized(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    pipe = _mk_pipeline(conn, monkeypatch)
    pipe.process(_items(["ราคา0.01บาท", "nonprod_ฟรี", "สร้อยคอแฟชั่น"]))

    repo = SemanticRepo(conn)
    bad = repo.get("TH", "ราคา0.01บาท")
    assert bad["status"] == "success"
    assert bad["intent_status"] == "NON_PRODUCT"
    assert bad["is_product"] == 0
    assert json.loads(bad["search_terms_1688"]) == []
    assert bad["meaning_zh"]
    assert repo.get("TH", "สร้อยคอแฟชั่น")["intent_status"] == "PRODUCT"
    conn.close()


# ---------------------------------------------------------------------------
# 5. AI 关闭时调用被拒绝（AI Guard）
# ---------------------------------------------------------------------------
def test_disabled_ai_rejected_in_pipeline(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    monkeypatch.setattr(config, "AI_ENABLED", False)   # 不开 AI
    pipe = SemanticsPipeline(conn, batch_size=10, market="TH")
    with pytest.raises(DeepSeekDisabledError):
        pipe.process(_items(["สร้อยคอแฟชั่น"]))
    # 未被标记失败（中止而非污染数据），库里无记录
    assert SemanticRepo(conn).count_by_status("TH") == {}
    conn.close()


# ---------------------------------------------------------------------------
# 6. 成功词不重复调用（success 缓存 + 断点过滤）
# ---------------------------------------------------------------------------
def test_no_repeat_calls_for_success(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)

    calls = {"n": 0}
    def spy(messages, **kwargs):
        calls["n"] += 1
        return _fake_chat(messages, **kwargs)
    monkeypatch.setattr(deepseek, "chat_completion", spy)

    pipe = SemanticsPipeline(conn, batch_size=3, market="TH")   # 8 词 → 3 批
    items = _items([f"kw{i}" for i in range(8)])
    first = pipe.process(items)
    assert first["success"] == 8
    assert calls["n"] == 3                                       # 首轮 3 次调用

    # 断点续跑：filter_eligible 后无待处理 → 0 次调用
    second = pipe.process(pipe.filter_eligible(items))
    assert second["requested"] == 0 and second["success"] == 0
    assert calls["n"] == 3                                       # 成功词不再调用
    conn.close()


# ---------------------------------------------------------------------------
# 7. AI 返回异常 JSON 时安全处理（整批 failed + 错误留痕，可重试，不伪造成功）
# ---------------------------------------------------------------------------
def test_malformed_json_safe_handling(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)
    monkeypatch.setattr(deepseek, "chat_completion",
                        lambda messages, **kwargs: "抱歉，我不太明白你的意思……")  # 非 JSON

    pipe = SemanticsPipeline(conn, batch_size=2, market="TH")   # 4 词 → 2 批，两批全坏
    stats = pipe.process(_items(["kw_a", "kw_b", "kw_c", "kw_d"]))  # 不得抛异常
    assert stats["failed"] == 4 and stats["success"] == 0

    repo = SemanticRepo(conn)
    row = repo.get("TH", "kw_a")
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert "JSON" in (row["error"] or "")                       # 错误信息留痕

    # 断点重试（仍坏）：attempts 由断点路径从 DB 读出(=1) → 本次后 =2
    retry_item = {"keyword": "kw_a", "market": "TH", "category": CAT,
                  "keyword_type": KT, "period_start": PERIOD,
                  "attempts": repo.get("TH", "kw_a")["attempts"]}
    pipe.process([retry_item])
    assert repo.get("TH", "kw_a")["attempts"] == 2
    conn.close()


# ---------------------------------------------------------------------------
# 8. 单个关键词失败不影响整个批次
# ---------------------------------------------------------------------------
def test_single_item_failure_isolated(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)

    def broken(messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        arr = json.loads(_fake_chat(messages, **kwargs))
        for item in arr:
            if payload["words"][item["index"]] == "bad1":
                item.pop("meaning_zh")                          # 该条缺字段 → 单条失败
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", broken)

    pipe = _mk_pipeline(conn, monkeypatch, fake=broken)
    stats = pipe.process(_items(["good1", "bad1", "good2"]))
    assert stats["success"] == 2 and stats["failed"] == 1

    repo = SemanticRepo(conn)
    assert repo.get("TH", "good1")["status"] == "success"
    assert repo.get("TH", "good2")["status"] == "success"
    bad = repo.get("TH", "bad1")
    assert bad["status"] == "failed"
    assert "meaning_zh" in (bad["error"] or "")
    conn.close()


# ---------------------------------------------------------------------------
# 9. AI 结果写入数据库并可重新读取（持久化 + 字段完整，含 intent_status）
# ---------------------------------------------------------------------------
def test_results_persist_and_reread(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    conn = connect(db)
    init_db(conn)
    pipe = _mk_pipeline(conn, monkeypatch)
    pipe.process(_items(["สร้อยคอแฟชั่น"]))
    conn.close()

    conn2 = connect(db)
    row = SemanticRepo(conn2).get("TH", "สร้อยคอแฟชั่น")
    assert row["status"] == "success"
    assert row["intent_status"] == "PRODUCT" and row["is_product"] == 1
    assert row["meaning_zh"] == "时尚项链"
    assert row["product_category"] == "饰品"
    assert json.loads(row["search_terms_1688"]) == SAMPLE["search_terms_1688"]
    assert row["confidence"] == pytest.approx(0.92)
    assert row["language"] == "th"
    assert row["model"] == config.DEEPSEEK_MODEL
    assert row["attempts"] == 1
    assert row["error"] is None
    assert row["created_at"] and row["updated_at"]
    conn2.close()


# ---------------------------------------------------------------------------
# 10. batch_size 超过 30 必须被拒绝（产品硬上限）
# ---------------------------------------------------------------------------
def test_batch_over_30_rejected(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    with pytest.raises(ValueError, match="超过硬上限"):
        SemanticsPipeline(conn, batch_size=31, market="TH")
    with pytest.raises(ValueError, match="超过硬上限"):
        SemanticsPipeline(conn, batch_size=100, market="TH")
    conn.close()


# ---------------------------------------------------------------------------
# 12. 回归：failed 行升级为 success 时必须完整覆盖 intent_status/canonical 列
#     （真实调用曾因 DO UPDATE 漏列导致 success 行 canonical 残留 NULL）
# ---------------------------------------------------------------------------
def test_failed_to_success_upsert_overwrites_all_columns(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)
    repo = SemanticRepo(conn)
    # 先写入一条 failed 行（模拟首轮失败，intent/canonical 为 NULL）
    repo.upsert_failed(SemanticRecord(
        original_keyword="สร้อยคอ", market="TH", status="failed", attempts=1,
        error="整批调用失败: x", created_at="t1", updated_at="t1"))
    assert repo.get("TH", "สร้อยคอ")["intent_status"] is None

    def ok_fake(messages, **kwargs):
        w = json.loads(messages[-1]["content"])["words"][0]
        return json.dumps([{"index": 0, "original_keyword": w, "language": "th",
                            "meaning_zh": "时尚项链", "product_category": "饰品",
                            "product_subcategory": "项链",
                            "canonical_product_name": "女士项链",
                            "canonical_product_key": "necklace",
                            "search_terms_1688": ["项链甲", "项链乙"],
                            "intent_status": "PRODUCT", "is_product": 1,
                            "confidence": 0.9}], ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", ok_fake)
    pipe = _mk_pipeline(conn, monkeypatch, fake=ok_fake)
    stats = pipe.process(_items(["สร้อยคอ"]))
    assert stats["success"] == 1
    row = repo.get("TH", "สร้อยคอ")
    assert row["status"] == "success"
    assert row["intent_status"] == "PRODUCT"                       # 不再残留 NULL
    assert row["is_product"] == 1
    assert row["canonical_product_key"] == "necklace"
    assert row["canonical_product_name"] == "女士项链"
    conn.close()
def test_one_based_index_compatible(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _enable_ai(monkeypatch)

    def one_based(messages, **kwargs):
        words = json.loads(messages[-1]["content"])["words"]
        arr = []
        for i, w in enumerate(words, start=1):          # index 从 1 开始
            arr.append({"index": i, "original_keyword": w, "language": "th",
                        "meaning_zh": f"{w} 含义", "product_category": "饰品",
                        "product_subcategory": "其他",
                        "canonical_product_name": f"商品{i}",
                        "canonical_product_key": f"prod_{i}",
                        "search_terms_1688": [f"{w} 词1", f"{w} 词2"],
                        "intent_status": "PRODUCT", "is_product": 1, "confidence": 0.9})
        return json.dumps(arr, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "chat_completion", one_based)

    pipe = _mk_pipeline(conn, monkeypatch, fake=one_based)
    words = ["สร้อยคอ", "พวงกุญแจ", "เข็มขัด"]
    stats = pipe.process(_items(words))
    assert stats["success"] == 3 and stats["failed"] == 0     # 1-based index 不再误杀
    row = SemanticRepo(conn).get("TH", "พวงกุญแจ")
    assert row["status"] == "success"
    assert row["meaning_zh"] == "พวงกุญแจ 含义"                # 正确对位（非错位）
    conn.close()
