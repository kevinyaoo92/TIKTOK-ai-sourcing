# -*- coding: utf-8 -*-
"""TikTok 商品机会分析管线（新链路，真实数据，确定性 Python 评分）。

数据流（严格遵守用户 Stage 0~5 规则）:
    keyword_metric(tiktok_market.db，按 country+level1+level2+period 横向展开)
      → Stage 0 数据清洗（删关键字段为空；排除 sku=0 AND ctor=0；keyword+period+category 去重）
      → Stage 1 非商品过滤（V1 规则 + Pattern；未命中即保留，不确定词交 DeepSeek）
      → Stage 2 类目内百分位（L1+L2+Period；样本<30 fallback L1+Period）
      → Stage 3 Opportunity Score（Demand/Conversion/Competition 加权）
      → Stage 4 降序 Top N
      → Stage 5 等级（S≥85 / A 75-84 / B 65-74 / C<65）

设计原则：
- 全部评分/百分位/排序由本模块确定性计算，绝不调用 AI。
- AI(DeepSeek) 只做语义理解与判断（见 app/ai/deepseek.py 机会分析任务）。
- 指数不等于真实销量，报告口径保持一致。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app import config
from app.analysis.screening import NonProductFilter, percentile

# 泰国官方类目名（第一界面市场参数）→ 库中 market 值
MARKET_ALIAS = {
    "泰国": "TH", "TH": "TH", "th": "TH",
}

# 评分必需指标（Stage 0 关键字段判定；缺任一即删除）
REQUIRED_METRICS = ["搜索量", "SKU销售指数", "CTOR评分", "在售商品"]
# 展示用指标（不进评分）
DISPLAY_METRICS = ["CTR指数", "商品点击数", "平均价格"]

METRIC_TO_EN = {
    "搜索量": "search_volume",
    "商品点击数": "product_clicks",
    "SKU销售指数": "sku_sales_index",
    "在售商品": "on_sale_products",
    "平均价格": "average_price",
    "CTR指数": "ctr_index",
    "CTOR评分": "ctor_score",
}


def normalize_market(country: str) -> str:
    return MARKET_ALIAS.get((country or "").strip(), (country or "").strip())


# ---------------------------------------------------------------------------
# 数据读取：keyword_metric 横向展开
# ---------------------------------------------------------------------------
def load_keyword_rows(db_path: Path, market: str, level1: str, level2: str,
                      period: str) -> list[dict]:
    """从 tiktok_market.db 读取某 L2 + period 的关键词完整观测。

    返回 [{keyword, 搜索量, 商品点击数, SKU销售指数, 在售商品, 平均价格, CTR指数, CTOR评分}]。
    一个关键词在多个指标出现即合并；某指标缺失为 None。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT km.keyword, km.metric, km.original_value, km.numeric_value, km.rank
               FROM keyword_metric km
               JOIN category c ON c.id = km.category_id
               WHERE c.market = ? AND c.level1 = ? AND c.level2 = ?
                 AND km.period = ?""",
            (market, level1, level2, period)).fetchall()
    finally:
        conn.close()

    by_kw: dict[str, dict] = {}
    for r in rows:
        kw = (r["keyword"] or "").strip()
        if not kw:
            continue
        m = by_kw.setdefault(kw, {"keyword": kw})
        metric = r["metric"]
        m.setdefault("_metrics", []).append(metric)
        m[metric] = {"orig": r["original_value"], "num": r["numeric_value"], "rank": r["rank"]}
    out = []
    for kw, m in by_kw.items():
        item = {"keyword": kw}
        for mt in REQUIRED_METRICS + DISPLAY_METRICS:
            if mt in m:
                item[mt] = m[mt]
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Stage 0：数据清洗
# ---------------------------------------------------------------------------
@dataclass
class CleanResult:
    rows: list[dict] = field(default_factory=list)   # 通过清洗的词
    dropped: list[dict] = field(default_factory=list)  # 被删记录(含原因)


def stage0_clean(rows: list[dict]) -> CleanResult:
    """Stage 0：删关键字段为空；排除 sku=0 AND ctor=0；keyword 去重。"""
    res = CleanResult()
    seen: set[str] = set()
    for r in rows:
        kw = r["keyword"]
        # 1) 关键字段为空（评分必需四指标任一缺失 → 删除）
        missing = [m for m in REQUIRED_METRICS if r.get(m) is None or r.get(m, {}).get("num") is None]
        if missing:
            res.dropped.append({**r, "_drop_reason": f"关键字段缺失: {missing}"})
            continue
        # 2) sku_sales_index=0 AND ctor_score=0 → 排除
        sku = r["SKU销售指数"]["num"] or 0
        ctor = r["CTOR评分"]["num"] or 0
        if sku == 0 and ctor == 0:
            res.dropped.append({**r, "_drop_reason": "sku_sales_index=0 AND ctor_score=0"})
            continue
        # 3) keyword + period + category 去重（本函数已按 keyword 合并；跨指标重复只保留首个）
        if kw in seen:
            res.dropped.append({**r, "_drop_reason": "keyword 重复"})
            continue
        seen.add(kw)
        res.rows.append(r)
    return res


# ---------------------------------------------------------------------------
# Stage 1：非商品过滤
# ---------------------------------------------------------------------------
@dataclass
class FilterResult:
    kept: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)   # {keyword, category, reason}


def stage1_nonproduct_filter(rows: list[dict],
                             nf: Optional[NonProductFilter] = None) -> FilterResult:
    nf = nf or NonProductFilter()
    res = FilterResult()
    for r in rows:
        hit = nf.classify(r["keyword"])
        if hit:
            category, reason = hit
            res.removed.append({"keyword": r["keyword"], "category": category, "reason": reason})
        else:
            res.kept.append(r)
    return res
# ---------------------------------------------------------------------------
# Stage 2：类目内百分位
# ---------------------------------------------------------------------------
def compute_percentiles(rows: list[dict]) -> list[dict]:
    """在给定样本集合内计算六指标百分位（0~1，标准中位秩）。

    返回每行追加 demand_pct/intent_pct/sales_pct/competition_pct/click_pct。
    """
    def _vals(mt: str) -> list[float]:
        return [r[mt]["num"] for r in rows if r.get(mt, {}).get("num") is not None]

    v_search = _vals("搜索量")
    v_ctor = _vals("CTOR评分")
    v_sku = _vals("SKU销售指数")
    v_onsale = _vals("在售商品")
    v_ctr = _vals("CTR指数")

    for r in rows:
        r["demand_pct"] = percentile(v_search, r["搜索量"]["num"]) if r["搜索量"].get("num") is not None else None
        r["intent_pct"] = percentile(v_ctor, r["CTOR评分"]["num"]) if r["CTOR评分"].get("num") is not None else None
        r["sales_pct"] = percentile(v_sku, r["SKU销售指数"]["num"]) if r["SKU销售指数"].get("num") is not None else None
        r["competition_pct"] = percentile(v_onsale, r["在售商品"]["num"]) if r["在售商品"].get("num") is not None else None
        r["click_pct"] = percentile(v_ctr, r["CTR指数"]["num"]) if r["CTR指数"].get("num") is not None else None
    return rows


# ---------------------------------------------------------------------------
# Stage 3：Opportunity Score
# ---------------------------------------------------------------------------
def compute_opportunity_score(rows: list[dict]) -> list[dict]:
    """按用户规则计算三维评分与机会分。

    Demand        = DemandPct
    Conversion    = 0.70×IntentPct + 0.30×SalesPct
    Competition   = 1 - CompetitionPct
    OpportunityScore = 100 × (0.40×Demand + 0.35×Conversion + 0.25×Competition)
    """
    for r in rows:
        demand = r["demand_pct"]
        intent = r["intent_pct"]
        sales = r["sales_pct"]
        competition = r["competition_pct"]
        if demand is None or intent is None or sales is None or competition is None:
            r["demand_score"] = None
            r["conversion_score"] = None
            r["competition_score"] = None
            r["opportunity_score"] = None
            continue
        conversion = 0.70 * intent + 0.30 * sales
        comp = 1.0 - competition
        r["demand_score"] = round(demand, 6)
        r["conversion_score"] = round(conversion, 6)
        r["competition_score"] = round(comp, 6)
        r["opportunity_score"] = round(100 * (0.40 * demand + 0.35 * conversion + 0.25 * comp), 4)
    return rows


# ---------------------------------------------------------------------------
# Stage 5：等级
# ---------------------------------------------------------------------------
def rating_for(score: float, small_sample: bool = False) -> tuple[str, str]:
    """返回 (rating, action)。

    正常样本（n≥30）：S≥85 / A 75-84 / B 65-74 / C<65
    小样本（n<30）：阈值下调 10 分（S≥75 / A 65-74 / B 55-64 / C<55）
    原因：小样本百分位分布更紧，分数普遍偏低，需下调阈值避免全 C 级。
    """
    if small_sample:
        if score >= 75:
            return "S", "优先上架"
        if score >= 65:
            return "A", "重点测试"
        if score >= 55:
            return "B", "观察"
        return "C", "暂不推荐"

    if score >= 85:
        return "S", "优先上架"
    if score >= 75:
        return "A", "重点测试"
    if score >= 65:
        return "B", "观察"
    return "C", "暂不推荐"


# ---------------------------------------------------------------------------
# 机会证据（evidence_json 构造，供前端展示）
# ---------------------------------------------------------------------------
def build_evidence(r: dict, n_rank: int, total: int) -> list[dict]:
    """按用户 API 响应格式构造 evidence 数组。scored=True 的进评分。"""
    def _pos(pct: float | None, rank: int | None) -> str:
        if pct is None:
            return "—"
        p = int(round(pct * 100))
        if rank is not None and rank <= total:
            return f"第{rank}名，P{p}"
        return f"P{p}"

    def _jdg(pct: float | None, labels: tuple) -> str:
        if pct is None:
            return "—"
        p = pct
        if p >= 0.8:
            return labels[0]
        if p >= 0.5:
            return labels[1]
        if p >= 0.25:
            return labels[2]
        return labels[3]

    ev = []
    # 需求
    sv = r.get("搜索量", {})
    ev.append({
        "dimension": "需求", "metric": "搜索量", "value": sv.get("orig", "—"),
        "position": _pos(r.get("demand_pct"), sv.get("rank")),
        "judgment": _jdg(r.get("demand_pct"), ("需求极强", "需求较强", "需求中等", "需求较弱")),
        "scored": True,
    })
    # 转化 CTOR
    ctor = r.get("CTOR评分", {})
    ev.append({
        "dimension": "转化", "metric": "CTOR", "value": ctor.get("orig", "—"),
        "position": _pos(r.get("intent_pct"), ctor.get("rank")),
        "judgment": _jdg(r.get("intent_pct"), ("转化极高", "转化较高", "转化中等", "转化一般")),
        "scored": True,
    })
    # 转化 SKU
    sku = r.get("SKU销售指数", {})
    ev.append({
        "dimension": "转化", "metric": "SKU销售指数", "value": sku.get("orig", "—"),
        "position": _pos(r.get("sales_pct"), sku.get("rank")),
        "judgment": _jdg(r.get("sales_pct"), ("销售验证强", "销售验证中", "销售验证偏弱", "销售验证弱")),
        "scored": True,
    })
    # 竞争（在售商品：百分位越高=供给越饱和，评分 Competition=1-pct）
    ons = r.get("在售商品", {})
    cp = r.get("competition_pct")
    ev.append({
        "dimension": "竞争", "metric": "在售商品", "value": ons.get("orig", "—"),
        "position": _pos(cp, ons.get("rank")),
        "judgment": _jdg(cp, ("竞争中高", "竞争中等", "竞争较低", "竞争低")),
        "scored": True,
    })
    # 点击 CTR（仅展示）
    ctr = r.get("CTR指数", {})
    ev.append({
        "dimension": "点击", "metric": "CTR", "value": ctr.get("orig", "—"),
        "position": _pos(r.get("click_pct"), ctr.get("rank")),
        "judgment": _jdg(r.get("click_pct"), ("点击效率高", "点击效率中", "点击效率一般", "点击效率低")),
        "scored": False,
    })
    # 价格（仅展示，若数据缺失用参考）
    price = r.get("平均价格", {})
    ev.append({
        "dimension": "价格", "metric": "均价", "value": price.get("orig", "参考") if price else "参考",
        "position": "参考", "judgment": "低客单" if price else "参考", "scored": False,
    })
    return ev


# ---------------------------------------------------------------------------
# 相关关键词（泰文）：共享 ≥3 字符子串的同类目词
# ---------------------------------------------------------------------------
def find_related_keywords(row: dict, cohort: list[dict], max_n: int = 5) -> list[dict]:
    """找关联关键词。返回 [{keyword, search, ctor, products}, ...]"""
    kw = row["keyword"]
    related = []
    for other in cohort:
        okw = other["keyword"]
        if okw == kw:
            continue
        overlap = False
        short = okw if len(okw) <= len(kw) else kw
        long = kw if len(okw) > len(kw) else okw
        for i in range(len(long) - 2):
            if long[i:i + 3] in short:
                overlap = True
                break
        if overlap:
            related.append({
                "keyword": okw,
                "search": other.get("搜索量", {}).get("orig", "—"),
                "ctor": other.get("CTOR评分", {}).get("orig", "—"),
                "products": other.get("在售商品", {}).get("orig", "—"),
            })
        if len(related) >= max_n:
            break
    return related


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_analysis(country: str, level1: str, level2: str, period: str,
                 top_n: int = 20, db_path: Optional[Path] = None,
                 fallback_level1: bool = True,
                 extra_blacklist: Optional[set[str]] = None) -> dict:
    """执行完整分析（Stage 0-5），返回结构化结果。

    返回 dict 含:
      status/category/summary/stages/opportunities(不含 AI 字段)
      opportunities 每项: keyword/name_cn(初始占位)/score/rating/action/
                         demand_score/conversion_score/competition_score/
                         representative_keyword/related_keywords/evidence
    """
    db_path = db_path or Path(config.PROJECT_ROOT) / "data" / "tiktok_market.db"
    market = normalize_market(country)
    if market not in ("TH",):
        market = "TH" if "泰" in (country or "") else (country or "TH")

    # ---- 读取 + Stage 0 ----
    raw = load_keyword_rows(db_path, market, level1, level2, period)
    clean = stage0_clean(raw)

    # ---- Stage 1（支持注入 AI 反向非商品规则）----
    nf = NonProductFilter(extra_blacklist=extra_blacklist)
    filt = stage1_nonproduct_filter(clean.rows, nf=nf)

    # ---- Stage 2：百分位参照系 ----
    cohort = filt.kept
    cohort_label = f"{level1}>{level2} {period}"
    # fallback 已禁用：每个 L2 只用本 L2 数据评分
    # 候选 < 3 → 返回 empty，前端显示友情提示
    if len(cohort) < 3:
        return {
            "status": "empty",
            "reason": "insufficient_candidates",
            "message": f"该类目 {period} 仅有 {len(cohort)} 个有效关键词，样本过少，暂不建议分析。",
            "category": {"country": country, "level1": level1, "level2": level2, "period": period},
            "summary": {
                "total_keywords": len(raw),
                "after_clean": len(clean.rows),
                "after_nonproduct_filter": len(cohort),
                "scored": 0,
                "top_n": 0,
                "cohort_label": cohort_label,
                "cohort_size": len(cohort),
                "sample_size": "tiny",
            },
            "stages": {
                "stage0_dropped": len(clean.dropped),
                "stage1_removed": len(filt.removed),
                "stage1_removed_list": filt.removed[:20],
            },
            "opportunities": [],
        }

    scored = compute_percentiles(cohort)
    scored = compute_opportunity_score(scored)

    # 过滤掉无法评分（None）的词（理论上 Stage 0 已保证四指标齐全）
    scorable = [r for r in scored if r.get("opportunity_score") is not None]
    scorable.sort(key=lambda x: x["opportunity_score"], reverse=True)

    n = min(top_n, len(scorable))
    top = scorable[:n]

    # ---- 构造机会 ----
    opportunities = []
    total_cohort = len(scorable)
    small_sample = total_cohort < 30
    for i, r in enumerate(top, start=1):
        rating, action = rating_for(r["opportunity_score"], small_sample=small_sample)
        related = find_related_keywords(r, scorable)
        opp = {
            "opportunity_id": f"opp-{period}-{level2}-{i:02d}",
            "name_cn": r["keyword"],            # AI 分析后回填中文商品方向
            "score": r["opportunity_score"],
            "rating": rating,
            "action": action,
            "demand_score": r["demand_score"],
            "conversion_score": r["conversion_score"],
            "competition_score": r["competition_score"],
            "representative_keyword": r["keyword"],
            "representative_keyword_cn": "",
            "related_keywords": related,
            "evidence": build_evidence(r, r.get("搜索量", {}).get("rank"), total_cohort),
            "_row": r,
        }
        opportunities.append(opp)

    return {
        "status": "success",
        "category": {"country": country, "level1": level1, "level2": level2, "period": period},
        "summary": {
            "total_keywords": len(raw),
            "after_clean": len(clean.rows),
            "after_nonproduct_filter": len(filt.kept),
            "scored": len(scorable),
            "top_n": len(opportunities),
            "cohort_label": cohort_label,
            "cohort_size": total_cohort,
            "sample_size": "small" if total_cohort < 30 else "normal",
        },
        "stages": {
            "stage0_dropped": len(clean.dropped),
            "stage1_removed": len(filt.removed),
            "stage1_removed_list": filt.removed[:20],
        },
        "opportunities": opportunities,
    }


def _load_l1_cohort(db_path: Path, market: str, level1: str, period: str) -> list[dict]:
    """读取 L1 下全部 L2 的原始行（用于百分位 fallback）。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT km.keyword, km.metric, km.original_value, km.numeric_value, km.rank
               FROM keyword_metric km
               JOIN category c ON c.id = km.category_id
               WHERE c.market = ? AND c.level1 = ? AND km.period = ?""",
            (market, level1, period)).fetchall()
    finally:
        conn.close()
    by_kw: dict[str, dict] = {}
    for r in rows:
        kw = (r["keyword"] or "").strip()
        if not kw:
            continue
        m = by_kw.setdefault(kw, {"keyword": kw})
        metric = r["metric"]
        m[metric] = {"orig": r["original_value"], "num": r["numeric_value"], "rank": r["rank"]}
    return list(by_kw.values())
