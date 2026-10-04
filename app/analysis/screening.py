# -*- coding: utf-8 -*-
"""V1 初期关键词筛选层（第一阶段筛选漏斗的落地实现，全部确定性 Python，不调 AI）。

数据流：
    keyword_data(只读)
      → 数据清洗        (空值/缺失/非法 → data_quality 淘汰)
      → 非商品意图过滤  (精确黑名单 + 模式分类规则，可扩展 → non_product 淘汰)
        ★ 先整体剔除非商品词，再计算分位参照系 —— 避免促销词抬高需求分位
          （V1 报告实证：E 类"高需求低竞争"中 5/7 为促销词，会污染参照系）
      → 高需求主通道    (搜索量分位 ≥ P25)
      → 高购买意图保护通道 (搜索量分位 < P25 但 CTOR 分位 ≥ P75 → 放行)
      → Opportunity Score 排序 → Top N candidate（默认 80，V1 假设值）

计分（只读 keyword_data 六字段；CTR 不进主评分；趋势不进 V1）：
    demand_pct   = 搜索量百分位(0~1)              # 需求簇唯一代表（点击仅展示，不进公式）
    supply_pct   = 在售商品百分位(0~1)            # 不用"在售<X"做竞争硬判断
    opportunity_gap = demand_pct - supply_pct
    ctor_pct / sku_pct = CTOR / SKU销售指数 百分位
    purchase_intent   = 0.70×ctor_pct + 0.30×sku_pct
    opportunity_score = 0.40×demand_pct + 0.30×purchase_intent + 0.30×opportunity_gap

百分位口径与 V1 数据分析报告一致：标准中位秩(含样本自身)，(below + 0.5×ties)/n。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app import config
from app.database import KeywordRepo, ScreeningRepo, now_str
from app.models import ScreeningRecord

# ---------------------------------------------------------------------------
# 非商品意图过滤：分类规则（可扩展：在此列表追加 (类别, 正则) 元组即可）
# 规则设计避开了 V1 报告确认的误报模式（泰语 ของ 前缀等真实商品词）。
# ---------------------------------------------------------------------------
NONPRODUCT_PATTERN_RULES: tuple[tuple[str, str], ...] = (
    # 1) 价格/泰铢促销（บาท/฿/ราคา/包邮）
    ("price_promo_baht", r"บาท|฿|ราคา|ส่งฟรี"),
    # 2) 优惠券/促销活动词
    ("coupon_noise", r"โค้ด|คูปอง|payday|ลด 50|ลด50"),
    # 3) 索取免费样品/赠品
    ("free_sample_noise", r"ขอสินค้าฟรี|ตัวอย่างฟรี|ของแถม|แจกฟรี"),
    # 4) 货到付款/支付方式词
    ("cod_payment_noise", r"cash on delivery|cash delivery|เก็บเงินปลายทาง"),
    # 5) 跨区比索促销（泰店出现菲/菲币垃圾词）
    ("php_cross_region", r"\bpiso\b|\bpeso\b|₱|\bphp\b"),
    # 6) 分期/额度支付促销
    ("paylater_noise", r"pay later|ผ่อน|วงเงิน"),
    # 7) 新客/首单促销
    ("new_customer_promo", r"ลูกค้าใหม่"),
    # 8) 平台入口/功能词
    ("platform_noise", r"tiktokshop|tiktok shop|จาก tiktok|ในตอนนี้ tiktok|บน tiktok"),
    # 9) 纯数字/数字开头营销词（泰语商品词极少以数字开头；规则可扩展可调）
    ("digit_leading_noise", r"^\s*[\d฿₱•.\-\s]+\s*$|^\s*0[\.\s•]?\d"),
)

# 非商品意图类别 → 中文说明（用于 reject_reason / 统计）
NONPRODUCT_CATEGORY_CN = {
    "price_promo_baht": "价格/泰铢促销词",
    "coupon_noise": "优惠券/活动词",
    "free_sample_noise": "索取免费样品/赠品词",
    "cod_payment_noise": "货到付款/支付词",
    "php_cross_region": "跨区比索促销词",
    "paylater_noise": "分期/额度支付词",
    "new_customer_promo": "新客/首单促销词",
    "platform_noise": "平台入口/功能词",
    "digit_leading_noise": "纯数字/数字开头营销词",
    "exact_blacklist": "精确黑名单词",
}


@dataclass
class NonProductFilter:
    """非商品意图过滤器：精确黑名单(可扩展) + 模式分类规则(可扩展)。

    命中即返回 (类别, 中文原因)；未命中返回 None。
    extra_blacklist 可注入额外词；extra_blacklist_file 每行一个词(UTF-8，# 开头为注释)。
    """

    extra_blacklist: Optional[set[str]] = None
    extra_blacklist_file: Optional[str] = None

    def __post_init__(self):
        self._blacklist: set[str] = set(config.V1_NONPRODUCT_EXACT_BLACKLIST)
        if self.extra_blacklist:
            self._blacklist |= set(self.extra_blacklist)
        f = self.extra_blacklist_file or config.V1_NONPRODUCT_BLACKLIST_FILE
        if f:
            from pathlib import Path as _P
            p = _P(f)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self._blacklist.add(line)
        self._compiled = [(name, re.compile(pat)) for name, pat in NONPRODUCT_PATTERN_RULES]

    @property
    def blacklist_size(self) -> int:
        return len(self._blacklist)

    def classify(self, keyword: str) -> Optional[tuple[str, str]]:
        """返回 (category, reason) 或 None。"""
        k = keyword.strip()
        if not k:
            return "data_quality", "空关键词"
        if k in self._blacklist:
            return "exact_blacklist", f"{NONPRODUCT_CATEGORY_CN['exact_blacklist']}: {k}"
        for name, rx in self._compiled:
            if rx.search(k):
                return name, NONPRODUCT_CATEGORY_CN.get(name, name)
        return None


# ---------------------------------------------------------------------------
# 分位
# ---------------------------------------------------------------------------
def percentile(values: list[float], v: float) -> Optional[float]:
    """标准中位秩百分位(含样本自身)，0~1；与 V1 报告口径一致。空样本返回 None。"""
    n = len(values)
    if n == 0:
        return None
    below = sum(1 for x in values if x < v)
    ties = sum(1 for x in values if x == v)
    return (below + 0.5 * ties) / n


# ---------------------------------------------------------------------------
# 决策载体
# ---------------------------------------------------------------------------
@dataclass
class ScreeningDecision:
    """单个关键词的筛选决策（内存载体，入库为 ScreeningRecord）。"""
    keyword: str
    passed: bool = False
    channel: Optional[str] = None          # main / protection
    reject_category: Optional[str] = None  # data_quality / non_product / low_demand
    reject_reason: Optional[str] = None
    demand_pct: Optional[float] = None
    supply_pct: Optional[float] = None
    opportunity_gap: Optional[float] = None
    ctor_pct: Optional[float] = None
    sku_pct: Optional[float] = None
    purchase_intent: Optional[float] = None
    opportunity_score: Optional[float] = None
    top_rank: Optional[int] = None
    # 展示用源数据（不进评分；商品点击数仅为展示）
    sv: Optional[float] = None
    clicks: Optional[float] = None
    sku: Optional[float] = None
    onsale: Optional[int] = None
    ctr: Optional[float] = None
    ctor: Optional[float] = None

    def as_record(self, run_id: str, ctx: dict) -> ScreeningRecord:
        return ScreeningRecord(
            run_id=run_id, market=ctx["market"], category=ctx["category"],
            keyword_type=ctx["keyword_type"], period_type=ctx["period_type"],
            period_start=ctx["period_start"], keyword=self.keyword,
            passed=1 if self.passed else 0, channel=self.channel,
            reject_category=self.reject_category, reject_reason=self.reject_reason,
            demand_pct=self.demand_pct, supply_pct=self.supply_pct,
            opportunity_gap=self.opportunity_gap, ctor_pct=self.ctor_pct,
            sku_pct=self.sku_pct, purchase_intent=self.purchase_intent,
            opportunity_score=self.opportunity_score, top_rank=self.top_rank,
        )


# ---------------------------------------------------------------------------
# V1 筛选器
# ---------------------------------------------------------------------------
class V1Screener:
    """V1 初期筛选（keyword_data → 清洗 → 非商品过滤 → 通道 → 评分 → TopN）。"""

    def __init__(
        self,
        conn,
        *,
        market: Optional[str] = None,
        category: Optional[str] = None,
        keyword_type: Optional[str] = None,
        period_type: Optional[str] = None,
        period_start: Optional[str] = None,
        level_1_category: Optional[str] = None,
        level_2_category: Optional[str] = None,
        top_n: Optional[int] = None,
        weights: Optional[dict] = None,
        pi_weights: Optional[dict] = None,
        low_demand_pct: Optional[float] = None,
        high_intent_ctor_pct: Optional[float] = None,
        nonproduct_filter: Optional[NonProductFilter] = None,
        store_results: bool = True,
    ):
        self.conn = conn
        self.store_results = store_results
        self.market = market or config.DEFAULT_MARKET
        self.category = category or config.DEFAULT_CATEGORY
        self.keyword_type = keyword_type or config.DEFAULT_KEYWORD_TYPE
        self.period_type = period_type or config.DEFAULT_PERIOD_TYPE
        self.period_start = period_start
        # 规范化类目层级（新标准化市场数据层入口）：提供 level_1_category 时按层级取数；
        # 否则沿用 category 口径（兼容既有调用/测试；category=一级类目名即等于一级数据）。
        self.level_1_category = level_1_category
        self.level_2_category = level_2_category
        self.top_n = top_n if top_n is not None else config.V1_TOP_N
        self.w = dict(weights or config.V1_WEIGHTS)
        self.pi_w = dict(pi_weights or config.V1_PURCHASE_INTENT_WEIGHTS)
        self.low_demand_pct = low_demand_pct if low_demand_pct is not None else config.V1_LOW_DEMAND_PCT
        self.high_intent_ctor = high_intent_ctor_pct if high_intent_ctor_pct is not None \
            else config.V1_HIGH_INTENT_CTOR_PCT
        self.filter = nonproduct_filter or NonProductFilter()

    # ---- 读数据（只读 keyword_data；新层按 level_1/level_2，旧口径按 category）----
    def _cohort_rows(self):
        kw_repo = KeywordRepo(self.conn)
        if self.level_1_category:
            # 标准化市场数据层：一级(level_2=None → IS NULL) 或 二级(精确匹配) 独立取数
            periods = kw_repo.snapshot_periods_by_level(
                self.market, self.level_1_category, self.level_2_category,
                self.keyword_type, self.period_type)
            if not periods:
                return [], None
            baseline = self.period_start or periods[-1]  # 默认最新周期
            rows = kw_repo.snapshot_rows_by_level(
                self.market, self.level_1_category, self.level_2_category,
                self.keyword_type, self.period_type, baseline)
            return rows, baseline
        periods = kw_repo.snapshot_periods(
            self.market, self.category, self.keyword_type, self.period_type)
        if not periods:
            return [], None
        baseline = self.period_start or periods[-1]  # 默认最新周期
        rows = kw_repo.snapshot_rows(
            self.market, self.category, self.keyword_type, self.period_type, baseline)
        return rows, baseline

    # ---- 主流程 ----
    def run(self) -> dict:
        """执行筛选，返回结果汇总（records 含全部决策；candidates 为 TopN）。"""
        rows, baseline = self._cohort_rows()
        ctx = {"market": self.market, "category": self.category,
               "keyword_type": self.keyword_type, "period_type": self.period_type,
               "period_start": baseline or ""}
        if not rows:
            return {"records": [], "candidates": [], "ctx": ctx, "cohort_size": 0,
                    "reject_stats": {}, "passed": 0, "protection": 0, "main": 0,
                    "cleaned": 0, "stored": 0, "run_id": "", "top_n_cap": 0,
                    "top_n_config": self.top_n}

        run_id = now_str()
        records: list[ScreeningRecord] = []

        # ---------- 1) 数据清洗：字段缺失的词直接淘汰 ----------
        parsed = []   # 完整字段的词
        for r in rows:
            kw = (r["keyword"] or "").strip()
            if not kw or r["search_volume"] is None or r["sku_sales_index"] is None \
                    or r["ctor_score"] is None or r["on_sale_products"] is None:
                records.append(ScreeningRecord(
                    run_id=run_id, market=self.market, category=self.category,
                    keyword_type=self.keyword_type, period_type=self.period_type,
                    period_start=baseline or "", keyword=kw or "(空)",
                    passed=0, reject_category="data_quality",
                    reject_reason="关键字段缺失(关键词/搜索量/SKU/CTOR/在售)，无法评分"))
                continue
            parsed.append({
                "keyword": kw, "sv": float(r["search_volume"]),
                "clicks": float(r["product_clicks"]) if r["product_clicks"] is not None else None,
                "sku": float(r["sku_sales_index"]),
                "onsale": int(r["on_sale_products"]),
                "ctr": float(r["ctr_index"]) if r["ctr_index"] is not None else None,
                "ctor": float(r["ctor_score"]),
            })

        # ---------- 2) 非商品意图过滤（机会评分前；先整体剔除，避免污染分位参照系）----------
        kept: list[dict] = []
        for p in parsed:
            hit = self.filter.classify(p["keyword"])
            if hit:
                _category, reason = hit  # reason 已含中文类别说明
                records.append(ScreeningRecord(
                    run_id=run_id, market=self.market, category=self.category,
                    keyword_type=self.keyword_type, period_type=self.period_type,
                    period_start=baseline or "", keyword=p["keyword"],
                    passed=0, reject_category="non_product",
                    reject_reason=f"非商品意图({reason})"))
            else:
                kept.append(p)

        # ---------- 3) 分位（仅基于通过非商品过滤的词，当期同类目快照内 0~1）----------
        sv_vals = [p["sv"] for p in kept]
        onsale_vals = [float(p["onsale"]) for p in kept]
        sku_vals = [p["sku"] for p in kept]
        ctor_vals = [p["ctor"] for p in kept]

        decisions: list[ScreeningDecision] = []
        for p in kept:
            d = ScreeningDecision(keyword=p["keyword"], sv=p["sv"], clicks=p["clicks"],
                                  sku=p["sku"], onsale=p["onsale"], ctr=p["ctr"], ctor=p["ctor"])
            d.demand_pct = percentile(sv_vals, p["sv"])
            d.supply_pct = percentile(onsale_vals, float(p["onsale"]))
            d.ctor_pct = percentile(ctor_vals, p["ctor"])
            d.sku_pct = percentile(sku_vals, p["sku"])
            if d.demand_pct is not None and d.supply_pct is not None:
                d.opportunity_gap = round(d.demand_pct - d.supply_pct, 6)
            if d.ctor_pct is not None and d.sku_pct is not None:
                d.purchase_intent = round(
                    self.pi_w["ctor_percentile"] * d.ctor_pct
                    + self.pi_w["sku_percentile"] * d.sku_pct, 6)

            # ---------- 4) 通道：搜索量分位 < P25 → 低需求；CTOR 达高意图阈值可走保护通道 ----------
            if d.demand_pct is not None and d.demand_pct < self.low_demand_pct:
                if d.ctor_pct is not None and d.ctor_pct >= self.high_intent_ctor:
                    d.passed = True
                    d.channel = "protection"
                else:
                    d.reject_category = "low_demand"
                    d.reject_reason = (
                        f"低需求(搜索量分位 {d.demand_pct:.2f} < "
                        f"P{self.low_demand_pct * 100:.0f})且未达高购买意图"
                        f"(CTOR分位 {d.ctor_pct:.2f} < P{self.high_intent_ctor * 100:.0f})")
            else:
                d.passed = True
                d.channel = "main"

            # ---------- 5) 机会分（仅通过词；CTR/商品点击数不进主评分；趋势不进 V1）----------
            if d.passed:
                d.opportunity_score = round(
                    self.w.get("demand", 0.40) * d.demand_pct
                    + self.w.get("purchase_intent", 0.30) * d.purchase_intent
                    + self.w.get("opportunity_gap", 0.30) * d.opportunity_gap, 6)
            records.append(d.as_record(run_id, ctx))
            decisions.append(d)

        # ---------- 6) Top N ----------
        passed_list = [d for d in decisions if d.passed]
        passed_list.sort(key=lambda x: x.opportunity_score, reverse=True)
        n = min(self.top_n, len(passed_list))
        for i, d in enumerate(passed_list[:n], start=1):
            d.top_rank = i

        # 排名后再回填到已序列化的 records（入库行 top_rank 与内存一致）
        kw2rec = {rec.keyword: rec for rec in records}
        for d in passed_list[:n]:
            rec = kw2rec.get(d.keyword)
            if rec is not None:
                rec.top_rank = d.top_rank

        # ---------- 7) 落库（只写 screening_v1，不动其它表；预览可跳过）----------
        stored = 0
        if self.store_results:
            stored = ScreeningRepo(self.conn).insert_many(records)
            self.conn.commit()

        stats = {"data_quality": 0, "non_product": 0, "low_demand": 0}
        for rec in records:
            c = rec.reject_category
            if c in stats:
                stats[c] += 1
        protection = sum(1 for d in decisions if d.passed and d.channel == "protection")
        main = sum(1 for d in decisions if d.passed and d.channel == "main")

        return {
            "records": records, "candidates": passed_list[:n],
            "ctx": ctx, "run_id": run_id, "cohort_size": len(rows),
            "cleaned": len(kept), "stored": stored,
            "reject_stats": stats, "passed": len(passed_list),
            "protection": protection, "main": main, "top_n_cap": n,
            "top_n_config": self.top_n,
        }
