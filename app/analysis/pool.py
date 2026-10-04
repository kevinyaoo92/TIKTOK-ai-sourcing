# -*- coding: utf-8 -*-
"""V2 商品机会池生成（后台全量 Pool；不做用户选择/不截断成 9）。

流程（DeepSeek calls = 0，只用已有 ai_semantic_results）:
    keyword_data(level_1/level_2 × week/month 各源最新周期)
      → V1 确定性筛选（store=False，公式不变；TopN 取 config，非硬编码业务规则）
      → join 已有语义(success & PRODUCT & canonical key 合法)；缺语义 → missing_semantic 计数
      → 按 canonical_product_key 归并（禁止仅因中文名相似合并）
      → group 需求 = 最强关键词证据（禁止搜索量求和）；evidence 封顶防词数作弊
      → 每机会保存 weekly_evidence / monthly_evidence 摘要（不发明周月综合公式）
      → category_status: CORE / ADJACENT / EXCLUDE / REVIEW（边界争议保留并标记，不删除）
      → 写入 product_opportunities_v2（不动正式 product_opportunities=47）

字段定义:
  level_3_category = 三级类目（语义商品子类，如 项链/发夹；缺省回退 product_category）
  score           = V1.1 公式: 0.35×demand + 0.30×intent + 0.20×gap + 0.15×evidence(封顶)
  weekly/monthly_evidence = JSON 数组，每期一项:
    {period_type, period_start, period_end, cohort_size, keyword_count, keywords,
     best_keyword, best_demand_pct, best_purchase_intent, best_supply_pct,
     best_opportunity_gap, best_score}
"""
from __future__ import annotations

import json
from typing import Optional

from app import config
from app.analysis.opportunity_v11 import evidence_score
from app.analysis.screening import V1Screener
from app.database import KeywordRepo, OpportunityPoolRepo, now_str
from app.models import OpportunityPoolRecord

# 业务分类规则（泰国 TikTok 低价饰品/时尚发饰；边界争议 → REVIEW 不删除）
_EXCLUDE_HINTS = ("口罩", "防护", "防晒面罩", "假发", "发片", "刘海", "发网",
                  "碎发整理", "指套", "美容", "按摩")
_EXCLUDE_KEYS = ("face_mask", "slimming_face_mask", "uv_face_mask",
                 "sun_protection_face_cover", "finger_cot", "wig", "fake_bangs",
                 "hair_net", "baby_hair_stick", "hair_piece", "hair_piece_long",
                 "ponytail_hair_piece", "hair_piece_tail")
_CORE_HINTS = ("发夹", "发箍", "发圈", "发带", "发簪", "蝴蝶结", "项链", "手链",
               "手镯", "耳环", "耳饰", "胸针", "戒指", "头花", "头饰发")
_CORE_KEYS = ("hair_clip", "hair_band", "hair_tie", "hair_bow", "hair_stick",
              "headband", "lace_headband", "necklace", "bracelet", "bangle",
              "earring", "earrings", "brooch", "ring", "hair_accessory")
_ADJACENT_HINTS = ("腰带", "帽子", "头巾", "眼镜", "钥匙扣", "手表", "披肩",
                   "围巾", "包挂", "丝巾")
_ADJACENT_KEYS = ("belt", "belt_buckle", "hat", "cap", "head_wrap", "glasses",
                  "sunglasses", "keychain", "watch", "wristwatch", "shawl")
# 明显业务边界争议（功能镜/跨界品）→ 保留并标记 REVIEW
_REVIEW_HINTS = ("防蓝光", "远视", "老花", "运动", "户外")


def fit_category_status(
    canonical_product_name: str,
    canonical_product_key: str,
    product_category: str = "",
    product_subcategory: str = "",
) -> str:
    """确定性业务分类：CORE / ADJACENT / EXCLUDE / REVIEW。

    判定顺序: EXCLUDE(强词) → CORE → ADJACENT → REVIEW(边界/无法确定)。
    依据现有语义字段，不改变商品含义；争议项不删除。
    """
    name = (canonical_product_name or "")
    key = (canonical_product_key or "").lower()
    cat_sub = f"{product_category or ''}/{product_subcategory or ''}"

    def hit(text_hints, key_hints):
        if any(h in name for h in text_hints):
            return True
        if any(h in cat_sub for h in text_hints):
            return True
        if any(kh in key or key.startswith(kh) for kh in key_hints):
            return True
        return False

    if any(h in name or h in cat_sub for h in _REVIEW_HINTS):
        return "REVIEW"                      # 边界争议(如 防蓝光眼镜) 保留标记
    if hit(_EXCLUDE_HINTS, _EXCLUDE_KEYS):
        return "EXCLUDE"
    if hit(_CORE_HINTS, _CORE_KEYS):
        return "CORE"
    if hit(_ADJACENT_HINTS, _ADJACENT_KEYS):
        return "ADJACENT"
    return "REVIEW"                          # 无法确定 → 人工复核，不删除


def _semantic_row(conn, market: str, keyword: str):
    return conn.execute(
        "SELECT canonical_product_name, canonical_product_key, product_category, "
        "product_subcategory, intent_status FROM ai_semantic_results "
        "WHERE market=? AND original_keyword=? AND status='success' AND intent_status='PRODUCT' "
        "AND canonical_product_key IS NOT NULL AND canonical_product_key<>''",
        (market, keyword),
    ).fetchone()


def build_pool(
    conn,
    *,
    market: str = "TH",
    level_1: str = "时尚配件",
    level_2_categories: tuple[str, ...] = (),
    top_n: Optional[int] = None,
    max_periods: int = 1,
    store: bool = True,
) -> dict:
    """生成 V2 商品机会池。返回汇总 dict（records/stats/differences…由调用方组装报告）。"""
    cap = top_n if top_n is not None else config.V1_TOP_N
    repo = KeywordRepo(conn)
    run_id = now_str()

    sources = [(level_1, None)] + [(level_1, l2) for l2 in level_2_categories]
    opportunities: dict[tuple, dict] = {}   # (level_2, key) -> 累积组
    stats = {
        "sources": [],          # 每源每周期统计
        "screening": {"raw": 0, "reject_non_product": 0, "reject_low_demand": 0,
                      "protection": 0, "passed": 0, "candidates": 0},
        "semantic": {"matched": 0, "missing_semantic": 0, "deepseek_calls": 0},
    }

    for l1, l2 in sources:
        for period_type in ("month", "week"):
            periods = repo.snapshot_periods_by_level(market, l1, l2, config.DEFAULT_KEYWORD_TYPE,
                                                     period_type)
            if not periods:
                stats["sources"].append({"level_1": l1, "level_2": l2,
                                         "period_type": period_type, "n_periods": 0,
                                         "records": 0, "screening_candidates": 0})
                continue
            for ps in periods[-max_periods:]:
                pe_row = conn.execute(
                    "SELECT DISTINCT period_end FROM keyword_data WHERE market=? "
                    "AND level_1_category=? AND level_2_category IS ? AND keyword_type=? "
                    "AND period_type=? AND period_start=? LIMIT 1",
                    (market, l1, l2, config.DEFAULT_KEYWORD_TYPE, period_type, ps)).fetchone()
                period_end = pe_row["period_end"] if pe_row else ps
                res = V1Screener(conn, market=market, level_1_category=l1,
                                 level_2_category=l2, period_type=period_type,
                                 period_start=ps, top_n=cap, store_results=False).run()
                cands = res["candidates"]
                cohort = res["cohort_size"]
                stats["sources"].append({
                    "level_1": l1, "level_2": l2, "period_type": period_type,
                    "period_start": ps, "period_end": period_end,
                    "n_periods": 1, "records": cohort, "screening_candidates": len(cands),
                })
                stats["screening"]["raw"] += cohort
                stats["screening"]["passed"] += res["passed"]
                stats["screening"]["protection"] += res["protection"]
                stats["screening"]["candidates"] += len(cands)
                # reject_stats: {category: n}
                for cat_, n_ in (res.get("reject_stats") or {}).items():
                    if cat_ == "non_product":
                        stats["screening"]["reject_non_product"] += n_
                    elif cat_ == "low_demand":
                        stats["screening"]["reject_low_demand"] += n_

                if not cands:
                    continue
                # 该期分组证据
                members_by_key: dict[str, list[dict]] = {}
                for d in cands:           # ScreeningDecision: 全部 passed & top_rank 1..len
                    sem = _semantic_row(conn, market, d.keyword)
                    if sem is None:
                        stats["semantic"]["missing_semantic"] += 1
                        continue
                    stats["semantic"]["matched"] += 1
                    members_by_key.setdefault(sem["canonical_product_key"], []).append({
                        "keyword": d.keyword,
                        "v1score": d.opportunity_score or 0.0,
                        "demand_pct": d.demand_pct,
                        "purchase_intent": d.purchase_intent,
                        "supply_pct": d.supply_pct,
                        "opportunity_gap": d.opportunity_gap,
                        "name": sem["canonical_product_name"],
                        "key": sem["canonical_product_key"],
                        "category": sem["product_category"],
                        "subcategory": sem["product_subcategory"],
                    })
                for key, ms in members_by_key.items():
                    ms.sort(key=lambda m: m["v1score"], reverse=True)
                    g = opportunities.setdefault((l2, key), {
                        "level_2": l2, "key": key, "name": ms[0]["name"],
                        "category": ms[0]["category"], "subcategory": ms[0]["subcategory"],
                        "members": [], "month": [], "week": [], "periods": set(),
                    })
                    g["members"].extend(ms)
                    g["periods"].add(ps)
                    ev = {
                        "period_type": period_type, "period_start": ps,
                        "period_end": period_end, "cohort_size": cohort,
                        "keyword_count": len(ms),
                        "keywords": [m["keyword"] for m in ms],
                        "best_keyword": ms[0]["keyword"],
                        "best_demand_pct": ms[0]["demand_pct"],
                        "best_purchase_intent": ms[0]["purchase_intent"],
                        "best_supply_pct": ms[0]["supply_pct"],
                        "best_opportunity_gap": ms[0]["opportunity_gap"],
                        "best_score": ms[0]["v1score"],
                    }
                    (g["month"] if period_type == "month" else g["week"]).append(ev)

    # ---- 组装池行 ----
    records: list[OpportunityPoolRecord] = []
    all_keys = sorted(opportunities, key=lambda kv: kv[1])
    for i, (l2, key) in enumerate(all_keys, start=1):
        g = opportunities[(l2, key)]
        # primary 证据：优先月(长期基础)，无月则用周
        primary = g["month"] if g["month"] else g["week"]
        best = max(primary, key=lambda e: e["best_score"])
        members_all = g["members"]
        src_kws = sorted({m["keyword"] for m in members_all})
        evid = evidence_score(len(src_kws))          # 跨期去重词数 → 封顶证据分
        kw_count = len(src_kws)
        score = round(0.35 * best["best_demand_pct"] + 0.30 * best["best_purchase_intent"]
                      + 0.20 * best["best_opportunity_gap"] + 0.15 * evid, 6)
        periods_sorted = sorted(g["periods"])
        st = fit_category_status(g["name"], key, g["category"], g["subcategory"])
        records.append(OpportunityPoolRecord(
            run_id=run_id,
            opportunity_id=f"OP-{i:04d}",
            market=market,
            level_1_category=level_1,
            level_2_category=l2,
            level_3_category=g["subcategory"] or g["category"],
            canonical_product_name=g["name"],
            canonical_product_key=key,
            score=score,
            demand_score=best["best_demand_pct"],
            purchase_intent_score=best["best_purchase_intent"],
            supply_score=best["best_supply_pct"],
            opportunity_gap=best["best_opportunity_gap"],
            evidence_count=kw_count,
            source_keywords=json.dumps(src_kws, ensure_ascii=False),
            weekly_evidence=json.dumps(g["week"], ensure_ascii=False),
            monthly_evidence=json.dumps(g["month"], ensure_ascii=False),
            first_seen=periods_sorted[0],
            last_seen=periods_sorted[-1],
            category_status=st,
            created_at=run_id,
            updated_at=run_id,
        ))

    stored = 0
    if store:
        repo2 = OpportunityPoolRepo(conn)
        repo2.replace_all()
        stored = repo2.insert_many(records)
        conn.commit()

    from collections import Counter
    cat_cnt = Counter(r.category_status for r in records)
    stats["pool"] = {
        "opportunities": len(records),
        "category_status": dict(cat_cnt),
        "level3_categories": len({r.level_3_category for r in records}),
        "canonical_keys": len({r.canonical_product_key for r in records}),
        "stored": stored,
    }
    return {"run_id": run_id, "records": records, "stats": stats}
