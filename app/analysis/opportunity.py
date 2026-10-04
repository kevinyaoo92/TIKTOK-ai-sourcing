# -*- coding: utf-8 -*-
"""市场机会分析（第一阶段：可解释、透明评分模型）。

边界：
- 全部数学计算由 Python 完成，绝不调用 DeepSeek/AI 算分。
- 输出能回答"为什么这个关键词被/不被认为是市场机会"——逐因素中文理由。
- 规则与权重集中在 app/config.py，便于产品老板后续调整。

评分因素（详见 config.SCORE_WEIGHTS）：
  需求 demand / 增长 growth / 点击承接 click / 销售 sales / 竞争 competition / 价格 price
综合分 = 各因素分(0~100)按权重加权；某因素数据不足时剔除该因素并对余下权重归一。
"""
from __future__ import annotations

import json
from typing import Optional

from app import config
from app.database import KeywordRepo, OpportunityRepo, now_str
from app.models import OpportunityRecord

# 与 keyword_data 列一致的取值辅助
def _row_get(row, key):
    return row[key] if key in row.keys() else None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _midrank_pct(values: list[float], v: float) -> float:
    """v 在 values 中的分位(0~100)，并列取中位秩。n<=1 时给 50（样本不足，中性）。"""
    n = len(values)
    if n == 0:
        return 50.0
    below = sum(1 for x in values if x < v)
    ties = sum(1 for x in values if x == v)
    return (below + 0.5 * ties) / n * 100.0


def _cohort_values(cohort, field: str) -> list[float]:
    out = []
    for r in cohort:
        v = _row_get(r, field)
        if v is not None:
            out.append(float(v))
    return out


def _fmt(v: float, nd: int = 2) -> str:
    """数值展示：整数不带小数点。"""
    if v == int(v):
        return str(int(v))
    return f"{v:.{nd}f}"


# ---------------------------------------------------------------------------
# 核心：分析
# ---------------------------------------------------------------------------
def analyze(
    conn,
    *,
    market: Optional[str] = None,
    category: Optional[str] = None,
    keyword_type: Optional[str] = None,
    period_type: Optional[str] = None,
    baseline_period: Optional[str] = None,
    weights: Optional[dict] = None,
    price_band: Optional[tuple] = None,
) -> dict:
    """对某市场/类目/榜单类型/粒度的最新快照计算市场机会。

    返回:
      {"records": [OpportunityRecord 按机会分降序],
       "market":..., "category":..., "keyword_type":..., "period_type":...,
       "baseline": 基准周期, "previous": 上期周期或 None, "cohort_size": 词数}
    """
    market = market or config.DEFAULT_MARKET
    category = category or config.DEFAULT_CATEGORY
    keyword_type = keyword_type or config.DEFAULT_KEYWORD_TYPE
    period_type = period_type or config.DEFAULT_PERIOD_TYPE
    weights = weights or dict(config.SCORE_WEIGHTS)
    price_band = price_band if price_band is not None else config.PRICE_BAND_THB

    kw_repo = KeywordRepo(conn)
    periods = kw_repo.snapshot_periods(market, category, keyword_type, period_type)
    if not periods:
        return {
            "records": [], "market": market, "category": category,
            "keyword_type": keyword_type, "period_type": period_type,
            "baseline": None, "previous": None, "cohort_size": 0,
        }

    baseline = baseline_period or periods[-1]          # 默认最新周期
    previous = kw_repo.previous_period(market, category, keyword_type, period_type, baseline)
    cohort = kw_repo.snapshot_rows(market, category, keyword_type, period_type, baseline)
    prev_rows: dict[str, object] = {}
    if previous:
        for r in kw_repo.snapshot_rows(market, category, keyword_type, period_type, previous):
            prev_rows.setdefault(_row_get(r, "keyword"), r)

    cohort_size = len(cohort)
    # 点击承接比：商品点击数/搜索量（搜索量<=0 视为无法计算）
    def click_ratio(r) -> Optional[float]:
        c, s = _row_get(r, "product_clicks"), _row_get(r, "search_volume")
        if c is None or not s or s <= 0:
            return None
        return float(c) / float(s)

    # 分位分母（各因素只统计本因素有值的词，理由中给出样本数，保证可解释）
    vol_vals = _cohort_values(cohort, "search_volume")
    ratio_vals = [cr for cr in (click_ratio(r) for r in cohort) if cr is not None]
    sale_vals = _cohort_values(cohort, "sku_sales_index")
    onsale_vals = _cohort_values(cohort, "on_sale_products")

    computed_at = now_str()
    records: list[OpportunityRecord] = []

    for r in cohort:
        kw = _row_get(r, "keyword")
        reasons: list[str] = []
        scores: dict[str, Optional[float]] = {}
        growth_rate: Optional[float] = None
        vol_prev: Optional[float] = None

        # ---- 1 需求：搜索量分位 ----
        v = _row_get(r, "search_volume")
        if v is not None and vol_vals:
            s = _midrank_pct(vol_vals, float(v))
            scores["demand"] = s
            reasons.append(
                f"【需求】本期搜索量 {_fmt(float(v))}，高于同期同类目 {s:.0f}% 的关键词"
                f"（样本 {len(vol_vals)} 词）。"
            )
        else:
            scores["demand"] = None
            reasons.append("【需求】本词无搜索量数据，未计分。")

        # ---- 2 增长：与上期(同粒度)环比 ----
        prev_r = prev_rows.get(kw)
        if prev_r is None:
            scores["growth"] = config.NEW_KEYWORD_GROWTH_SCORE
            reasons.append(
                f"【增长】上期({previous or '无快照'})榜单无此词（新上榜/新进入榜单），"
                f"环比不可算，给中性偏高默认分 {config.NEW_KEYWORD_GROWTH_SCORE:.0f}；"
                f"建议下期复核是否持续在榜。"
            )
        else:
            v_prev = _row_get(prev_r, "search_volume")
            if v_prev is not None and float(v_prev) > 0 and v is not None:
                rate = (float(v) - float(v_prev)) / float(v_prev)
                growth_rate = rate
                vol_prev = float(v_prev)
                s = _clamp(config.GROWTH_BASE_SCORE + rate * config.GROWTH_SCORE_PER_UNIT)
                scores["growth"] = s
                reasons.append(
                    f"【增长】环比上期({previous})搜索量 {_fmt(vol_prev)} → {_fmt(float(v))}，"
                    f"增长率 {rate:+.1%}，增长分 {s:.0f}（基准 {config.GROWTH_BASE_SCORE:.0f}，"
                    f"每+1%约+1分）。"
                )
            else:
                scores["growth"] = None
                reasons.append("【增长】上期搜索量为 0 或缺失，环比不可比，未计分。")

        # ---- 3 点击承接：商品点击数/搜索量 分位 ----
        ratio = click_ratio(r)
        if ratio is not None and ratio_vals:
            s = _midrank_pct(ratio_vals, ratio)
            scores["click"] = s
            reasons.append(
                f"【点击承接】商品点击数/搜索量 = {ratio:.2f}，高于 {s:.0f}% 的词"
                f"（样本 {len(ratio_vals)} 词）——比值越高，买家搜索后点进商品的意图越明确。"
            )
        else:
            scores["click"] = None
            reasons.append("【点击承接】本词缺点击/搜索数据，未计分。")

        # ---- 4 销售信号：SKU 销售指数分位 ----
        v = _row_get(r, "sku_sales_index")
        if v is not None and sale_vals:
            s = _midrank_pct(sale_vals, float(v))
            scores["sales"] = s
            reasons.append(
                f"【销售信号】SKU销售指数 {_fmt(float(v))}，高于 {s:.0f}% 的词"
                f"（样本 {len(sale_vals)} 词）。"
            )
        else:
            scores["sales"] = None
            reasons.append("【销售信号】本词缺 SKU销售指数，未计分。")

        # ---- 5 竞争：在售商品数越低分越高（分位反转）----
        v = _row_get(r, "on_sale_products")
        if v is not None and onsale_vals:
            pct_low = _midrank_pct(onsale_vals, float(v))  # 值越小该分位越低
            s = 100.0 - pct_low
            scores["competition"] = s
            reasons.append(
                f"【竞争】在售商品 {_fmt(float(v))}，在 {len(onsale_vals)} 词中处于"
                f"{'低位（在售少、竞争小）' if s >= 50 else '高位（在售多、竞争大）'}，竞争分 {s:.0f}"
                f"（分高=竞争小）。"
            )
        else:
            scores["competition"] = None
            reasons.append("【竞争】本词缺在售商品数，未计分。")

        # ---- 6 价格带：落在目标区间才得分（可配置，默认不计分）----
        v = _row_get(r, "avg_price")
        if price_band is None:
            scores["price"] = None
            reasons.append("【价格】未配置目标价格区间（config.PRICE_BAND_THB），本阶段价格不计分。")
        elif v is None:
            scores["price"] = None
            reasons.append("【价格】本词缺平均价格，未计分。")
        else:
            lo, hi = price_band
            vv = float(v)
            if lo <= vv <= hi:
                s = 100.0
                reasons.append(f"【价格】平均价格 {_fmt(vv)} ฿ 落在目标区间 {_fmt(lo)}~{_fmt(hi)} ฿ 内，价格分 100。")
            else:
                edge = lo if vv < lo else hi
                dist = abs(vv - edge) / edge if edge else 1.0
                s = _clamp(100.0 - dist * 100.0)
                reasons.append(
                    f"【价格】平均价格 {_fmt(vv)} ฿ 在目标区间 {_fmt(lo)}~{_fmt(hi)} ฿ 之外，"
                    f"偏离 {dist:.0%}，价格分 {s:.0f}。"
                )
            scores["price"] = s

        # ---- 综合：有效因素加权（剔除 None 后对剩余权重归一）----
        active = [(k, w, scores[k]) for k, w in weights.items() if scores.get(k) is not None]
        active_sum_w = sum(w for _, w, _ in active)
        if active and active_sum_w > 0:
            opp = sum((w / active_sum_w) * s for _, w, s in active)
        else:
            opp = 0.0
        opp = round(opp, 2)

        label = "低机会"
        for min_s, lb in config.LABEL_BANDS:  # 从高到低命中第一档
            if opp >= min_s:
                label = lb
                break

        factor_cn = {"demand": "需求", "growth": "增长", "click": "点击承接",
                     "sales": "销售信号", "competition": "竞争", "price": "价格"}
        factor_txt = "，".join(f"{factor_cn[k]} {s:.0f}" for k, _, s in active)
        reasons.append(
            f"【综合】机会分 {opp:.1f}/100 → 「{label}」。计入因素: {factor_txt}。"
            f"（有效权重 {active_sum_w:.2f}/1.00 参与归一）"
        )

        records.append(OpportunityRecord(
            computed_at=computed_at,
            market=market, category=category, keyword_type=keyword_type, period_type=period_type,
            period_start=baseline, prev_period_start=previous,
            cohort_size=cohort_size, keyword=kw,
            search_volume=float(v) if (v := _row_get(r, "search_volume")) is not None else None,
            search_volume_prev=vol_prev,
            growth_rate=growth_rate,
            on_sale_products=_row_get(r, "on_sale_products"),
            avg_price=_row_get(r, "avg_price"),
            demand_score=round(scores["demand"], 2) if scores["demand"] is not None else None,
            growth_score=round(scores["growth"], 2) if scores["growth"] is not None else None,
            click_score=round(scores["click"], 2) if scores["click"] is not None else None,
            sales_score=round(scores["sales"], 2) if scores["sales"] is not None else None,
            competition_score=round(scores["competition"], 2) if scores["competition"] is not None else None,
            price_score=round(scores["price"], 2) if scores["price"] is not None else None,
            opportunity_score=opp,
            label=label,
            reasons=json.dumps(reasons, ensure_ascii=False),
        ))

    records.sort(key=lambda x: x.opportunity_score, reverse=True)
    return {
        "records": records,
        "market": market, "category": category, "keyword_type": keyword_type,
        "period_type": period_type,
        "baseline": baseline, "previous": previous, "cohort_size": cohort_size,
    }


def store(conn, result: dict, replace_run: bool = False) -> int:
    """把 analyze() 结果写入 opportunity_data。replace_run=True 清理同基准旧批次。"""
    opp_repo = OpportunityRepo(conn)
    if replace_run and result["baseline"]:
        opp_repo.delete_snapshot_batch(
            result["market"], result["category"], result["keyword_type"],
            result["period_type"], result["baseline"],
        )
    return opp_repo.insert_many(result["records"])
