# -*- coding: utf-8 -*-
"""V1.1 商品机会归一化层（Python 聚类 + 评分 + 最终 ≤9；全程不调用 DeepSeek）。

流程:
    ai_semantic_results（success & intent=PRODUCT，且原词属 screening_v1 pass=1 top_rank<=80）
      → 按 canonical_product_key 聚类（同一商品机会只形成一组；禁止仅因一级分类相同合并）
      → 组内以"最强关键词"(V1 opportunity_score 最高) 取 Demand/PurchaseIntent/OpportunityGap
        （禁止把同机会多关键词搜索量相加 —— 同一需求的不同表达不叠加需求）
      → Opportunity Score = 0.35×demand + 0.30×intent + 0.20×gap + 0.15×evidence
        EvidenceConsistency 有限增强（config.V11_EVIDENCE，封顶防同义词数量作弊）
      → 排序 + 去重（一 key 一机会天然无重复）→ 最终 ≤ FINAL_OPPORTUNITY_LIMIT(9)
        不足 9 个就少展示；禁止降质凑数。
      → 落库 product_opportunities（幂等重建：每次运行=基于当前语义结果的全量重算，
        同一 market 旧快照先删后写，重复运行不会产生重复机会行）

V1(screening_v1/keyword_data/opportunity_data) 只读，绝不修改。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app import config
from app.database import OpportunityV11Repo, now_str
from app.models import OpportunityV11Record

# canonical_product_key 合法格式（与语义校验一致）
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")


def evidence_score(keyword_count: int) -> float:
    """需求证据一致性 0~1（封顶，防数量作弊）。

    score = min(cap, base + step × (min(count, max_count) - 1))
    1 词=base；2 词=+step；≥3 词封顶；超过 max_count 不再增加。
    """
    e = config.V11_EVIDENCE
    count = max(0, keyword_count)
    return min(e["cap"], e["base"] + e["step"] * (min(count, e["max_count"]) - 1))


def _load_semantic_products(conn, market: str, top_rank_max: int):
    """取语义成功且属 V1 候选的 PRODUCT 词 + 其 screening_v1 指标行。

    返回 {keyword: {key, name, screencols...}}；仅 join 到 screening_v1 最新批次的
    passed=1 & top_rank<=上限 的词（候选范围与 V1 一致）。
    """
    rows = conn.execute(
        "SELECT s.original_keyword AS kw, s.canonical_product_key AS ckey, "
        "       s.canonical_product_name AS cname, s.confidence "
        "FROM ai_semantic_results s "
        "WHERE s.market=? AND s.status='success' AND s.intent_status='PRODUCT' "
        "  AND s.canonical_product_key IS NOT NULL AND s.canonical_product_key<>''",
        (market,),
    ).fetchall()
    kw_map = {r["kw"]: r for r in rows}

    scr = conn.execute(
        "SELECT keyword, demand_pct, purchase_intent, supply_pct, opportunity_gap, "
        "       opportunity_score FROM screening_v1 "
        "WHERE run_id=(SELECT MAX(run_id) FROM screening_v1) AND market=? "
        "  AND passed=1 AND top_rank<=?",
        (market, top_rank_max),
    ).fetchall()
    joined = []
    skipped = []
    for r in scr:
        sem = kw_map.get(r["keyword"])
        if sem is None:
            continue  # 该词尚无成功语义（本次未处理完）→ 留待后续批次，不计入
        if not KEY_RE.fullmatch(sem["ckey"]):
            skipped.append((r["keyword"], f"canonical_product_key 非法: {sem['ckey']!r}"))
            continue
        joined.append({
            "keyword": r["keyword"],
            "ckey": sem["ckey"],
            "cname": sem["cname"],
            "demand_pct": r["demand_pct"],
            "purchase_intent": r["purchase_intent"],
            "supply_pct": r["supply_pct"],
            "opportunity_gap": r["opportunity_gap"],
            "v1_score": r["opportunity_score"],
        })
    return joined, skipped


def build_opportunities(
    conn,
    *,
    market: str = "TH",
    top_rank_max: Optional[int] = None,
    store: bool = True,
) -> dict:
    """执行 V1.1：聚类 → 商品机会评分 → 最终 ≤9。返回结果汇总。

    结果:
      opportunities: 全部机会(按 opportunity_score 降序, 已标记 is_final/final_rank)
      final: 最终 ≤9 列表
      skipped: 语义成功但 key 非法等被跳过项（安全失败，不崩溃）
      stats: {semantic_products, candidate_matched, groups, final_count, stored}
    """
    cap = top_rank_max if top_rank_max is not None else config.AI_CANDIDATE_LIMIT
    joined, skipped = _load_semantic_products(conn, market, cap)
    if not joined:
        return {"opportunities": [], "final": [], "skipped": skipped, "run_id": "",
                "stats": {"matched_candidates": 0, "groups": 0, "scored_groups": 0,
                          "skipped_groups": 0, "final_count": 0, "stored": 0}}

    # ---------- 1) Python 聚类：按 canonical_product_key ----------
    groups: dict[str, dict] = {}
    for row in joined:
        g = groups.setdefault(row["ckey"], {
            "ckey": row["ckey"], "cname": row["cname"],
            "members": [],   # 按 v1_score 降序
        })
        g["members"].append(row)
    for g in groups.values():
        g["members"].sort(key=lambda m: (m["v1_score"] is not None, m["v1_score"] or 0),
                          reverse=True)

    # ---------- 2) 组内指标：最强关键词（禁止搜索量相加）----------
    w = config.V11_SCORE_WEIGHTS
    run_id = now_str()   # 先取批次号，写库与内存一致
    opportunities: list[OpportunityV11Record] = []
    for gid, (ckey, g) in enumerate(sorted(groups.items(),
                                           key=lambda kv: kv[1]["members"][0]["v1_score"] or 0,
                                           reverse=True), start=1):
        best = g["members"][0]
        kw_count = len(g["members"])
        evid = evidence_score(kw_count)
        demand = best["demand_pct"] if best["demand_pct"] is not None else 0.0
        intent = best["purchase_intent"] if best["purchase_intent"] is not None else 0.0
        gap = best["opportunity_gap"] if best["opportunity_gap"] is not None else 0.0
        # 关键指标缺失（防御性）：该机会标记 skipped，不参与评分与最终
        if best["demand_pct"] is None or best["purchase_intent"] is None \
                or best["opportunity_gap"] is None:
            status = "skipped"
        else:
            status = "pool"
        opp = OpportunityV11Record(
            run_id=run_id,
            opportunity_id=f"OP-{gid:04d}",
            market=market,
            canonical_product_key=ckey,
            canonical_product_name=g["cname"],
            keyword_count=kw_count,
            source_keywords=json.dumps([m["keyword"] for m in g["members"]],
                                       ensure_ascii=False),
            best_keyword=best["keyword"],
            best_keyword_score=best["v1_score"],
            best_demand_pct=demand if status != "skipped" else None,
            best_purchase_intent=intent if status != "skipped" else None,
            best_supply_pct=best["supply_pct"],
            best_opportunity_gap=gap if status != "skipped" else None,
            evidence_consistency=evid,
            demand_component=round(w["demand"] * demand, 6) if status != "skipped" else None,
            intent_component=round(w["purchase_intent"] * intent, 6) if status != "skipped" else None,
            gap_component=round(w["opportunity_gap"] * gap, 6) if status != "skipped" else None,
            evidence_component=round(w["evidence_consistency"] * evid, 6) if status != "skipped" else None,
            opportunity_score=0.0,
            is_final=0,
            status=status,
            created_at=run_id,
        )
        if status != "skipped":
            opp.opportunity_score = round(
                (opp.demand_component or 0) + (opp.intent_component or 0)
                + (opp.gap_component or 0) + (opp.evidence_component or 0), 6)
        opportunities.append(opp)

    # ---------- 3) 排序 + 最终 ≤9（不足 9 就少；绝不为凑数降质）----------
    scored = [o for o in opportunities if o.status == "pool"]
    scored.sort(key=lambda o: o.opportunity_score, reverse=True)
    final_limit = config.FINAL_OPPORTUNITY_LIMIT
    final = scored[:final_limit]
    for rank, o in enumerate(final, start=1):
        o.is_final = 1
        o.final_rank = rank
        o.status = "final"
    # 同 key 天然唯一（聚类保证）→ 最终列表无重复机会
    keys = [o.canonical_product_key for o in final]
    assert len(keys) == len(set(keys)), "最终列表出现重复商品机会(聚类异常)"

    # ---------- 4) 落库（幂等重建：只写 product_opportunities，不动 V1/语义表）----------
    stored = 0
    if store:
        repo = OpportunityV11Repo(conn)
        repo.replace_market(market)   # 删同 market 旧快照 → 表只保留本次完整结果
        stored = repo.insert_many(opportunities)
        conn.commit()

    return {
        "opportunities": opportunities,
        "final": final,
        "skipped": skipped,
        "run_id": run_id,
        "stats": {
            "matched_candidates": len(joined),
            "groups": len(opportunities),
            "scored_groups": len(scored),
            "skipped_groups": len(opportunities) - len(scored),
            "final_count": len(final),
            "stored": stored,
        },
    }
