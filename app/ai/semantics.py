# -*- coding: utf-8 -*-
"""AI 语义处理服务（第二阶段：批量编排 + 断点续跑 + 重试 + 单条失败隔离）。

数据流（V1 筛选后接入 AI 语义层，AI 只做语义，数字继续由 Python 负责）：
    screening_v1（通过筛选且 top_rank <= AI_CANDIDATE_LIMIT，按 top_rank 升序）
      → SemanticsPipeline 分批(每批≤30)调 deepseek.analyze_keywords_batch
      → 严格校验（intent_status↔is_product 一致 / terms 2~5 / confidence 0~1）
      → ai_semantic_results（PRODUCT/NON_PRODUCT/AMBIGUOUS + 全量追溯字段）

断点语义：
- status='success' 的词 = 已成功，默认永不再调用 AI；
- 无记录或 status='failed' 且 attempts < 上限 的词 = 待处理（下次运行自动续跑）；
- attempts 达上限默认跳过，可用 force_retry=True 重置再试。
- batch JSON 解析失败：整批记 failed + error，绝不写伪造成功结果。
"""
from __future__ import annotations

import json
import math
import re
from typing import Callable, Optional

from app import config
from app.ai import deepseek
from app.ai.deepseek import DeepSeekDisabledError
from app.database import SemanticRepo, now_str
from app.models import SemanticRecord

# intent_status ↔ is_product 唯一合法映射
INTENT_IS_PRODUCT = {"PRODUCT": 1, "NON_PRODUCT": 0, "AMBIGUOUS": None}


def planned_batches(n_items: int, batch_size: int) -> int:
    """预计 DeepSeek 调用批数 = ceil(待处理/每批)；0 表示无需调用。"""
    if n_items <= 0:
        return 0
    return math.ceil(n_items / batch_size)


def _num(v) -> Optional[float]:
    """confidence 宽松转 float（AI 可能输出 0.92 / "0.92" / 92%）。"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().rstrip("%")
        try:
            return float(s) / 100.0 if "%" in v else float(s)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean_bool(v) -> Optional[int]:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v == 1 else 0
    return None


class SemanticsPipeline:
    """把候选关键词（默认来自 screening_v1 TopN）送入 DeepSeek 并落库。"""

    def __init__(
        self,
        conn,
        *,
        batch_size: Optional[int] = None,
        max_attempts: Optional[int] = None,
        force_retry: bool = False,
        market: str = "TH",
        model: Optional[str] = None,
        call_ai: Optional[Callable] = None,
    ):
        self.conn = conn
        self.repo = SemanticRepo(conn)
        self.batch_size = batch_size or config.AI_BATCH_SIZE
        if self.batch_size > config.AI_BATCH_MAX:  # 硬上限(30)
            raise ValueError(
                f"batch_size={self.batch_size} 超过硬上限 {config.AI_BATCH_MAX}"
                "（产品规则：每批最多 30 个关键词）")
        if self.batch_size < 1:
            raise ValueError("batch_size 必须 ≥1")
        self.max_attempts = max_attempts or config.AI_MAX_ATTEMPTS
        self.force_retry = force_retry
        self.market = market
        self.model = model or config.DEEPSEEK_MODEL
        # 可注入（测试替身）；默认真实 DeepSeek 调用
        self._call_ai = call_ai or deepseek.analyze_keywords_batch

    # ------------------------------------------------------------------
    # 候选来源：screening_v1（产品路径：V1 筛选 → AI 语义）
    # ------------------------------------------------------------------
    def screening_candidates(
        self,
        top_rank_max: Optional[int] = None,
        *,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """读取最近一次 V1 筛选中 passed 且 top_rank<=上限 的词（按 top_rank 升序）。

        返回 [{keyword, market, category, keyword_type, period_type, period_start, attempts}]。
        attempts 为 ai_semantic_results 中该词已失败次数（首见=0）。
        """
        cap = top_rank_max if top_rank_max is not None else config.AI_CANDIDATE_LIMIT
        rows = self.conn.execute(
            "SELECT keyword, market, category, keyword_type, period_type, period_start "
            "FROM screening_v1 WHERE run_id=(SELECT MAX(run_id) FROM screening_v1) "
            "AND passed=1 AND top_rank<=? ORDER BY top_rank ASC LIMIT ?",
            (cap, limit or 10_000_000),
        ).fetchall()
        attempts_map = self.repo.failed_attempts(self.market)
        return [{
            "keyword": r["keyword"], "market": r["market"], "category": r["category"],
            "keyword_type": r["keyword_type"], "period_type": r["period_type"],
            "period_start": r["period_start"],
            "attempts": attempts_map.get(r["keyword"], 0),
        } for r in rows]

    def filter_eligible(self, candidates: list[dict]) -> list[dict]:
        """断点过滤：剔除已成功词；剔除失败次数达上限的词（force_retry 可重置）。"""
        success_set = {
            r["original_keyword"]
            for r in self.conn.execute(
                "SELECT original_keyword FROM ai_semantic_results "
                "WHERE market=? AND status='success'", (self.market,)).fetchall()
        }
        out = []
        for c in candidates:
            kw = c["keyword"]
            if kw in success_set:
                continue  # 已成功 → 不重复调用
            prev = c.get("attempts", 0)
            if prev >= self.max_attempts and not self.force_retry:
                continue  # 失败达上限，本次跳过
            out.append(c)
        return out

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------
    def process(self, items: list[dict], on_batch: Optional[Callable] = None) -> dict:
        """处理一批候选词（内部按 batch_size 分片送 AI，每片 ≤30）。

        on_batch(stats_dict) 每片回调一次（CLI 进度用）。
        返回统计: {requested, sent_batches, success, failed, skipped}
        """
        stats = {"requested": len(items), "sent_batches": 0, "success": 0,
                 "failed": 0, "skipped": 0}
        if not items:
            return stats

        for start in range(0, len(items), self.batch_size):
            chunk_items = items[start:start + self.batch_size]
            chunk_words = [it["keyword"] for it in chunk_items]
            ok_n, fail_n = self._process_chunk(chunk_items, chunk_words)
            stats["sent_batches"] += 1
            stats["success"] += ok_n
            stats["failed"] += fail_n
            if on_batch:
                on_batch(dict(stats))
        return stats

    def _process_chunk(self, chunk_items: list[dict], chunk_words: list[str]) -> tuple[int, int]:
        """送一个分片给 AI 并落库。整片解析失败→整片 failed；单条校验失败→仅该条 failed。"""
        now = now_str()
        base_attempts = {it["keyword"]: it["attempts"] for it in chunk_items}

        try:
            results = self._call_ai(chunk_words)
        except DeepSeekDisabledError:
            raise  # AI 被禁用属配置问题：中止而非把整片标记失败
        except Exception as e:  # noqa: BLE001 —— 网络/限流/非法 JSON 等调用层错误
            # 整片失败：保留错误信息、逐条记 failed，绝不写伪造成功结果
            for it in chunk_items:
                self.repo.upsert_failed(self._failed_rec(it, now, base_attempts[it["keyword"]], f"整批调用失败: {e}"))
            return 0, len(chunk_items)

        if not isinstance(results, list):
            msg = f"AI 返回类型异常(期望数组, 实为 {type(results).__name__})"
            for it in chunk_items:
                self.repo.upsert_failed(self._failed_rec(it, now, base_attempts[it["keyword"]], msg))
            return 0, len(chunk_items)

        # 按 index 对齐。真实调用发现：模型偶发输出 1-based index(1..n) 而非 0-based(0..n-1)，
        # 若不兼容会把整批误判"回显不一致/缺序号0"。这里推断偏移：只要存在 index==0 即 0-based，
        # 否则按 1-based 整体减一。index 缺失/越界的项仍视为单条失败（防乱序）。
        raw_indices = [item.get("index") for item in results
                       if isinstance(item, dict) and isinstance(item.get("index"), int)]
        offset = 0 if 0 in raw_indices else 1
        by_index: dict[int, dict] = {}
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                by_index[item["index"] - offset] = item

        ok = fail = 0
        for pos, it in enumerate(chunk_items):
            kw = it["keyword"]
            item = by_index.get(pos)
            err = self._validate_item(item, pos, kw)
            if err:
                self.repo.upsert_failed(self._failed_rec(it, now, base_attempts[kw], err))
                fail += 1
                continue
            self.repo.upsert_success(self._success_rec(it, item, now, base_attempts[kw]))
            ok += 1
        return ok, fail

    # ------------------------------------------------------------------
    # 严格校验（结构化输出契约；宁缺毋滥：不合规即失败重试，不伪造）
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_item(item, pos: int, kw: str) -> Optional[str]:
        """返回错误原因(有错)或 None(通过)。"""
        if item is None:
            return f"AI 结果缺少序号 {pos} 的条目（共返回不足）"
        if item.get("original_keyword") not in (None, kw):
            return f"AI 回显原词不一致: {item.get('original_keyword')!r} != {kw!r}"
        if not isinstance(item.get("meaning_zh"), str) or not item["meaning_zh"].strip():
            return "缺少 meaning_zh（中文含义）"
        if not isinstance(item.get("language"), str):
            return "缺少 language"

        # intent_status 三元 + 与 is_product 一致性
        intent = str(item.get("intent_status") or "").strip().upper()
        if intent not in INTENT_IS_PRODUCT:
            return f"intent_status 缺失或非法: {item.get('intent_status')!r}（须为 PRODUCT/NON_PRODUCT/AMBIGUOUS）"
        ip = _clean_bool(item.get("is_product"))
        if intent in ("PRODUCT", "NON_PRODUCT"):
            expect = INTENT_IS_PRODUCT[intent]
            if ip != expect:
                return f"intent_status={intent} 与 is_product={item.get('is_product')!r} 不一致（应={expect}）"

        # search_terms_1688：结构化数组；PRODUCT 必须 2~5 个
        terms = item.get("search_terms_1688")
        if not isinstance(terms, list):
            return "search_terms_1688 不是数组"
        if any(not isinstance(t, str) or not t.strip() for t in terms):
            return "search_terms_1688 含非字符串项"
        if intent == "PRODUCT":
            if not 2 <= len(terms) <= 5:
                return f"PRODUCT 的 search_terms_1688 须为 2~5 个，收到 {len(terms)}"
            if not item.get("product_category"):
                return "PRODUCT 缺少 product_category"
            # V1.1：标准商品机会名称与归一化键（PRODUCT 必填）
            cname = str(item.get("canonical_product_name") or "").strip()
            ckey = str(item.get("canonical_product_key") or "").strip()
            if not cname:
                return "PRODUCT 缺少 canonical_product_name（标准商品机会名称）"
            if not ckey:
                return "PRODUCT 缺少 canonical_product_key（商品机会归一化键）"
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,49}", ckey):
                return (f"canonical_product_key 格式非法: {ckey!r}"
                        "（须小写字母开头，仅 a-z/0-9/_，2~50 字符）")
        if intent == "NON_PRODUCT":
            if terms:
                return "NON_PRODUCT 的 search_terms_1688 应为空数组"
        if len(terms) > 5:
            return f"search_terms_1688 最多 5 个，收到 {len(terms)}"

        # confidence 必须存在且在 0~1（低置信度条目保留，不因低值删除）
        conf = _num(item.get("confidence"))
        if conf is None or not (0.0 <= conf <= 1.0):
            return f"confidence 缺失或超出 0~1: {item.get('confidence')!r}"
        return None

    # ------------------------------------------------------------------
    # 记录构造
    # ------------------------------------------------------------------
    def _success_rec(self, it: dict, item: dict, now: str, prev_attempts: int) -> SemanticRecord:
        kw = it["keyword"]
        intent = str(item["intent_status"]).strip().upper()
        terms = [t for t in item["search_terms_1688"]]
        return SemanticRecord(
            original_keyword=kw,
            market=self.market,
            category=it.get("category"),
            keyword_type=it.get("keyword_type"),
            period_start=it.get("period_start"),
            language=item.get("language") or config.SEMANTIC_DEFAULT_LANGUAGE,
            meaning_zh=str(item["meaning_zh"]).strip(),
            product_category=(str(item.get("product_category") or "").strip() or None),
            product_subcategory=(str(item.get("product_subcategory") or "").strip() or None),
            canonical_product_name=(str(item.get("canonical_product_name") or "").strip() or None),
            canonical_product_key=(str(item.get("canonical_product_key") or "").strip() or None),
            search_terms_1688=json.dumps(terms, ensure_ascii=False),
            is_product=INTENT_IS_PRODUCT[intent],   # PRODUCT→1 / NON_PRODUCT→0 / AMBIGUOUS→None
            intent_status=intent,
            confidence=_num(item.get("confidence")),
            model=self.model,
            status="success",
            attempts=prev_attempts + 1,
            error=None,
            created_at=now,
            updated_at=now,
        )

    def _failed_rec(self, it: dict, now: str, prev_attempts: int, error: str) -> SemanticRecord:
        return SemanticRecord(
            original_keyword=it["keyword"],
            market=self.market,
            category=it.get("category"),
            keyword_type=it.get("keyword_type"),
            period_start=it.get("period_start"),
            language=config.SEMANTIC_DEFAULT_LANGUAGE,
            meaning_zh=None,
            search_terms_1688="[]",
            is_product=None,
            intent_status=None,
            model=self.model,
            status="failed",
            attempts=prev_attempts + 1,
            error=str(error)[:2000],
            created_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # 一键流程（CLI 用）：screening 候选 → 断点过滤 → 分批处理
    # ------------------------------------------------------------------
    def run_from_screening(
        self,
        top_rank_max: Optional[int] = None,
        *,
        limit: Optional[int] = None,
        on_batch: Optional[Callable] = None,
    ) -> dict:
        candidates = self.screening_candidates(top_rank_max, limit=limit)
        items = self.filter_eligible(candidates)
        return self.process(items, on_batch=on_batch)
