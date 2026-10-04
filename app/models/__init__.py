# -*- coding: utf-8 -*-
"""数据模型（与 SQLite 表结构一一对应）。

用 dataclass 做导入/计算时的内存载体；数据库读写列顺序以 *_COLUMNS 常量
为单一事实来源，方便未来扩展字段或切换存储。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# keyword_data 表的列顺序（INSERT/SELECT 共用）
KEYWORD_COLUMNS = [
    "period_start",      # 周期开始日期 YYYY-MM-DD
    "period_end",        # 周期结束日期 YYYY-MM-DD
    "period_type",       # week=周 / month=月（数据本身粒度）
    "market",            # 市场，如 TH=泰国
    "category",          # 榜单类目原文（以导出/页面实际为准）：一级=一级类目名，二级=二级类目名
    "level_1_category",  # 规范化一级类目（如 时尚配件）；与 category 的关系见 database.py 注释
    "level_2_category",  # 规范化二级类目（如 平价饰品）；一级数据为 NULL
    "keyword_type",      # 榜单类型：热门搜索关键词 / 飙升关键词 / 高潜力关键词
    "keyword",           # 关键词原文（泰文/数字，原样保存，不翻译不改写）
    "rank",              # 榜单名次（导出表内出现顺序，1 起）
    "search_volume",     # 搜索量（导出原值，保留小数，不做换算推断）
    "product_clicks",    # 商品点击数
    "sku_sales_index",   # SKU 销售指数
    "on_sale_products",  # 在售商品数
    "avg_price",         # 平均价格（原值；单位见 price_unit）
    "price_unit",        # 价格单位（泰国站导出为 ฿=THB；导出未标注则为 NULL）
    "ctr_index",         # CTR 指数
    "ctor_score",        # CTOR 评分
    "source_file",       # 溯源：来自哪个导出文件（文件名）
    "file_hash",         # 源文件 sha256（同文件重复导入识别；前 16 位）
    "imported_at",       # 入库时间（历史快照不被覆盖，靠该字段区分导入批次）
]


@dataclass
class KeywordRecord:
    """一条关键词榜单记录（keyword_data 一行）。"""
    period_start: str
    period_end: str
    period_type: str
    market: str
    category: str
    keyword_type: str
    keyword: str
    level_1_category: Optional[str] = None
    level_2_category: Optional[str] = None
    rank: Optional[int] = None
    search_volume: Optional[float] = None
    product_clicks: Optional[float] = None
    sku_sales_index: Optional[float] = None
    on_sale_products: Optional[int] = None
    avg_price: Optional[float] = None
    price_unit: Optional[str] = None
    ctr_index: Optional[float] = None
    ctor_score: Optional[float] = None
    source_file: str = ""
    file_hash: Optional[str] = None
    imported_at: Optional[str] = None  # 入库时自动填充

    def as_tuple(self) -> tuple:
        """按 KEYWORD_COLUMNS 顺序取值，供 INSERT 使用。"""
        return tuple(getattr(self, c) for c in KEYWORD_COLUMNS)


# opportunity_data 表的列顺序（INSERT/SELECT 共用）
OPPORTUNITY_COLUMNS = [
    "computed_at",          # 本次机会计算时间（同一批相同）
    "market", "category", "keyword_type", "period_type",
    "period_start",         # 本期（基准）周期
    "prev_period_start",    # 上期周期（用于环比；无则为 NULL）
    "cohort_size",          # 基准周期同类目词数（分位分母，保证可解释）
    "keyword",
    "search_volume",        # 本期搜索量（原值）
    "search_volume_prev",   # 上期搜索量（原值，用于核对环比）
    "growth_rate",          # 环比增长率（本期/上期 - 1；无法计算为 NULL）
    "on_sale_products",     # 在售商品数（源数据，供用户查看数据依据）
    "avg_price",            # 平均价格（源数据原值，฿=THB）
    "demand_score",         # 各因素分 0~100（NULL=该期数据不足未计分）
    "growth_score",
    "click_score",
    "sales_score",
    "competition_score",
    "price_score",
    "opportunity_score",    # 加权综合机会分 0~100
    "label",                # 机会等级标签（高机会/中机会/低机会…）
    "reasons",              # JSON 数组：逐条中文解释"为什么是/不是机会"
]


# ---------------------------------------------------------------------------
# AI 语义结果（第二阶段：keyword_data → DeepSeek 语义理解 → 1688 中文搜索词）
# 以 (market, original_keyword) 为自然键：同一词只保留一条语义缓存，
# status='success' 的词默认不再调用 AI（断点续跑靠 status/attempts/error）。
# ---------------------------------------------------------------------------
SEMANTIC_COLUMNS = [
    "original_keyword",     # 原始关键词（泰文原样）
    "market",               # 市场上下文（如 TH）
    "category",             # 来源类目（溯源参考）
    "keyword_type",         # 来源榜单类型（溯源参考）
    "period_start",         # 来源周期（溯源参考）
    "language",             # 关键词语言（ISO639-1，泰国市场默认 th）
    "meaning_zh",           # 中文含义（AI 语义理解结果）
    "product_category",     # 商品大类（中文，如 饰品/服装）
    "product_subcategory",  # 商品子类（如 项链/耳饰）
    "canonical_product_name",  # 标准商品机会名称（用户可理解的具体商品，如 女士发圈）
    "canonical_product_key",   # 商品机会归一化键（Python 聚类用稳定标准键，如 hair_tie）
    "search_terms_1688",    # 适合 1688 搜索的中文词（JSON 数组）
    "is_product",           # AI 判断是否为商品类关键词（1=PRODUCT / 0=NON_PRODUCT / NULL=AMBIGUOUS）
    "intent_status",        # PRODUCT | NON_PRODUCT | AMBIGUOUS（与 is_product 一致）
    "confidence",           # AI 置信度 0~1（原样保存；低置信度不删除）
    "model",                # 使用的模型名（可追溯）
    "status",               # success / failed
    "attempts",             # 已尝试次数（重试/断点依据）
    "error",                # 错误信息（失败时留痕）
    "created_at",           # 该词首次写入时间
    "updated_at",           # 最近一次状态变更时间
]


@dataclass
class SemanticRecord:
    """一条 AI 语义结果（ai_semantic_results 一行，含全量追溯字段）。"""
    original_keyword: str
    market: str = "TH"
    category: Optional[str] = None
    keyword_type: Optional[str] = None
    period_start: Optional[str] = None
    language: str = "th"
    meaning_zh: Optional[str] = None
    product_category: Optional[str] = None
    product_subcategory: Optional[str] = None
    canonical_product_name: Optional[str] = None
    canonical_product_key: Optional[str] = None
    search_terms_1688: str = "[]"
    is_product: Optional[int] = None
    intent_status: Optional[str] = None     # PRODUCT / NON_PRODUCT / AMBIGUOUS
    confidence: Optional[float] = None
    model: Optional[str] = None
    status: str = "success"
    attempts: int = 1
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in SEMANTIC_COLUMNS)


@dataclass
class OpportunityRecord:
    """一条机会计算结果（opportunity_data 一行）。"""
    computed_at: str
    market: str
    category: str
    keyword_type: str
    period_type: str
    period_start: str
    prev_period_start: Optional[str]
    cohort_size: int
    keyword: str
    search_volume: Optional[float] = None
    search_volume_prev: Optional[float] = None
    growth_rate: Optional[float] = None
    on_sale_products: Optional[int] = None
    avg_price: Optional[float] = None
    demand_score: Optional[float] = None
    growth_score: Optional[float] = None
    click_score: Optional[float] = None
    sales_score: Optional[float] = None
    competition_score: Optional[float] = None
    price_score: Optional[float] = None
    opportunity_score: float = 0.0
    label: str = ""
    reasons: str = "[]"

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in OPPORTUNITY_COLUMNS)


# ---------------------------------------------------------------------------
# V1 初期关键词筛选结果（每次运行一批历史；run_id=computed_at 区分批次）
# 每个关键词都记录: 是否通过 / 淘汰原因 / demand_pct / supply_pct /
#                   opportunity_gap / purchase_intent / opportunity_score
# ---------------------------------------------------------------------------
SCREENING_COLUMNS = [
    "run_id",               # 本次筛选运行时间戳(批次标识)
    "market", "category", "keyword_type", "period_type", "period_start",
    "keyword",              # 关键词原文
    "passed",               # 1=通过进入候选池 / 0=被淘汰
    "channel",              # 'main'(高需求主通道) / 'protection'(高购买意图保护通道) / NULL
    "reject_category",      # 淘汰类别: data_quality / non_product / low_demand / NULL
    "reject_reason",        # 淘汰原因中文/规则名（记录用）
    "demand_pct",           # 搜索量当期百分位 0~1
    "supply_pct",           # 在售商品当期百分位 0~1（越高供给越饱和）
    "opportunity_gap",      # demand_pct - supply_pct（可为负）
    "ctor_pct",             # CTOR 百分位 0~1（购买意图核心）
    "sku_pct",              # SKU销售指数 百分位 0~1（购买意图辅助）
    "purchase_intent",      # 0.70×ctor_pct + 0.30×sku_pct
    "opportunity_score",    # 0.40×demand + 0.30×intent + 0.30×gap
    "top_rank",             # 进入候选池的名次 1..N（未入候选为 NULL）
]


@dataclass
class ScreeningRecord:
    """一条 V1 筛选记录（screening_v1 一行）。"""
    run_id: str
    market: str
    category: str
    keyword_type: str
    period_type: str
    period_start: str
    keyword: str
    passed: int = 0
    channel: Optional[str] = None
    reject_category: Optional[str] = None
    reject_reason: Optional[str] = None
    demand_pct: Optional[float] = None
    supply_pct: Optional[float] = None
    opportunity_gap: Optional[float] = None
    ctor_pct: Optional[float] = None
    sku_pct: Optional[float] = None
    purchase_intent: Optional[float] = None
    opportunity_score: Optional[float] = None
    top_rank: Optional[int] = None

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in SCREENING_COLUMNS)


# ---------------------------------------------------------------------------
# V1.1 商品机会（product_opportunities：Python 聚类 + 评分结果）
# 数据来源: ai_semantic_results(success & intent=PRODUCT & 属 screening_v1 候选)
#           + screening_v1(该词 V1 指标 best 值)
# 规则: 同一 canonical_product_key 只形成一个商品机会；禁止多关键词搜索量相加；
#       EvidenceConsistency 有限增强（见 config.V11_EVIDENCE）；
#       最终仅 ≤ FINAL_OPPORTUNITY_LIMIT(9) 个标记 is_final。
# ---------------------------------------------------------------------------
OPPORTUNITY_V11_COLUMNS = [
    "run_id",               # 本次机会生成批次(computed_at)
    "opportunity_id",       # 如 OP-0001（本批内唯一）
    "market",
    "canonical_product_key",    # 聚类键（唯一 → 一 key 一机会）
    "canonical_product_name",   # 标准商品机会名称
    "keyword_count",            # 组内关键词数（证据，非线性加分）
    "source_keywords",          # 来源关键词 JSON 数组（按 V1 分降序）
    "best_keyword",             # 组内 V1 机会分最高的关键词
    "best_keyword_score",       # 该词 V1 opportunity_score
    "best_demand_pct",          # 该词 demand_pct（市场需求，用 best 而非求和）
    "best_purchase_intent",     # 该词 purchase_intent
    "best_supply_pct",          # 该词 supply_pct
    "best_opportunity_gap",     # 该词 opportunity_gap
    "evidence_consistency",     # 需求证据一致性 0~1（封顶）
    "demand_component",         # 0.35×demand
    "intent_component",         # 0.30×intent
    "gap_component",            # 0.20×gap
    "evidence_component",       # 0.15×evidence
    "opportunity_score",        # 四分量之和
    "is_final",                 # 1=进入最终 ≤9 结果 / 0=仅机会池
    "final_rank",               # 最终排名 1..≤9（未进入为 NULL）
    "status",                   # 'final' / 'pool' / 'skipped'（无有效指标）
    "created_at",
]


@dataclass
class OpportunityV11Record:
    """一条 V1.1 商品机会（product_opportunities 一行）。"""
    run_id: str
    opportunity_id: str
    market: str
    canonical_product_key: str
    canonical_product_name: str
    keyword_count: int = 0
    source_keywords: str = "[]"
    best_keyword: Optional[str] = None
    best_keyword_score: Optional[float] = None
    best_demand_pct: Optional[float] = None
    best_purchase_intent: Optional[float] = None
    best_supply_pct: Optional[float] = None
    best_opportunity_gap: Optional[float] = None
    evidence_consistency: Optional[float] = None
    demand_component: Optional[float] = None
    intent_component: Optional[float] = None
    gap_component: Optional[float] = None
    evidence_component: Optional[float] = None
    opportunity_score: float = 0.0
    is_final: int = 0
    final_rank: Optional[int] = None
    status: str = "pool"
    created_at: str = ""

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in OPPORTUNITY_V11_COLUMNS)


# ---------------------------------------------------------------------------
# V2 商品机会池（product_opportunities_v2：跨 level/周期 的市场机会池，后台全量）
# 定义: “市场数据中具有一定需求/购买意向/供给机会，经关键词语义归并后的具体商品方向”。
#   - 同一 canonical_product_key 归并；禁止仅因中文名相似合并
#   - group 需求 = 最强关键词证据，禁止搜索量求和
#   - 月证据(market base/稳定性) 与 周证据(近期动量) 分别保存，不发明综合公式
#   - V1.1 score 继续作主要机会评分；不截断成 9（9 是未来 UI 展示上限假设）
#   - 正式旧结果 product_opportunities(47) 不被本表影响，可随时对比
# ---------------------------------------------------------------------------
OPPORTUNITY_POOL_COLUMNS = [
    "run_id",                # 本次池计算批次
    "opportunity_id",        # 如 OP-0001（本批内唯一）
    "market",
    "level_1_category",      # 一级类目(时尚配件)
    "level_2_category",      # 二级类目(平价饰品)；一级数据为 NULL
    "level_3_category",      # 三级类目 = 语义商品子类(如 项链/发夹)；缺省回退 product_category
    "canonical_product_name",
    "canonical_product_key",
    "score",                 # 主要机会评分（V1.1 公式，基于 primary evidence 最强关键词）
    "demand_score",          # best demand_pct
    "purchase_intent_score", # best purchase_intent
    "supply_score",          # best supply_pct
    "opportunity_gap",       # best opportunity_gap
    "evidence_count",        # 该机会全部支持关键词数（跨周期去重）
    "source_keywords",       # JSON: 全部来源关键词（跨周期去重）
    "weekly_evidence",       # JSON: 周数据各期证据摘要数组（无周数据则 []）
    "monthly_evidence",      # JSON: 月数据各期证据摘要数组（无月数据则 []）
    "first_seen",            # 最早出现周期 period_start
    "last_seen",             # 最近出现周期 period_start
    "category_status",       # CORE / ADJACENT / EXCLUDE / REVIEW
    "created_at",
    "updated_at",
]


@dataclass
class OpportunityPoolRecord:
    """V2 商品机会池一行（product_opportunities_v2）。"""
    run_id: str
    opportunity_id: str
    market: str
    level_1_category: str
    level_2_category: Optional[str] = None
    level_3_category: Optional[str] = None
    canonical_product_name: str = ""
    canonical_product_key: str = ""
    score: float = 0.0
    demand_score: Optional[float] = None
    purchase_intent_score: Optional[float] = None
    supply_score: Optional[float] = None
    opportunity_gap: Optional[float] = None
    evidence_count: int = 0
    source_keywords: str = "[]"
    weekly_evidence: str = "[]"
    monthly_evidence: str = "[]"
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    category_status: str = "REVIEW"
    created_at: str = ""
    updated_at: str = ""

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in OPPORTUNITY_POOL_COLUMNS)


# ---------------------------------------------------------------------------
# V2.1 分析层（独立于机会池：归纳 ≠ 删除，聚类 ≠ 合并，总结 ≠ 覆盖原始）
#   opportunity_analysis  : 每个机会的类型/属性/风格/人群/证据状态/标签
#   opportunity_clusters  : 机会簇(同一方向内按商品类型聚合；成员机会保持独立)
#   opportunity_directions: 高层市场机会方向(用户视角的少量方向)
# 证据状态/数值/优先级全部由确定性规则计算；DeepSeek 仅可作受限命名/语义辅助
# （默认 rule-based，calls=0）。原始 product_opportunities/v2 一律不修改。
# ---------------------------------------------------------------------------
OPPORTUNITY_DIRECTION_COLUMNS = [
    "run_id", "direction_id", "market", "direction_name", "description",
    "opportunity_count", "core_count", "adjacent_count", "review_count",
    "weekly_supported_count", "monthly_supported_count",
    "priority",              # 透明规则: 成员机会 score 均值(可解释)
    "evidence_summary",      # JSON {weekly, monthly, multi_period, mean_score}
    "risk_summary",          # JSON {review_keys, exclude_in_direction, note}
    "member_keys",           # JSON [canonical_product_key ...]
    "created_at", "updated_at",
]

OPPORTUNITY_CLUSTER_COLUMNS = [
    "run_id", "cluster_id", "direction_id", "market", "cluster_name",
    "cluster_type",          # PRODUCT/ATTRIBUTE/STYLE/CATEGORY/ADJACENT/REVIEW/UNKNOWN
    "description", "opportunity_count", "core_count", "adjacent_count",
    "weekly_supported_count", "monthly_supported_count",
    "priority",              # 成员机会 score 均值
    "evidence_summary", "risk_summary",
    "member_keys", "created_at", "updated_at",
]

OPPORTUNITY_ANALYSIS_COLUMNS = [
    "run_id", "analysis_id", "opportunity_id", "market", "canonical_product_key",
    "cluster_id", "direction_id",
    "product_type", "attributes", "style", "target_group",   # JSON 数组/字符串(可空)
    "evidence_state",        # STABLE/RISING/RECENT/WEAK_EVIDENCE/REVIEW/UNKNOWN
    "reason_tags",           # JSON 枚举
    "risk_tags",             # JSON 枚举
    "analysis_text",         # 规则生成中文说明(非营销)
    "model",                 # 'rule-based-v2.1' / 记录实际 AI 模型
    "created_at", "updated_at",
]


@dataclass
class OpportunityDirectionRecord:
    run_id: str
    direction_id: str
    market: str
    direction_name: str = ""
    description: str = ""
    opportunity_count: int = 0
    core_count: int = 0
    adjacent_count: int = 0
    review_count: int = 0
    weekly_supported_count: int = 0
    monthly_supported_count: int = 0
    priority: float = 0.0
    evidence_summary: str = "{}"
    risk_summary: str = "{}"
    member_keys: str = "[]"
    created_at: str = ""
    updated_at: str = ""

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in OPPORTUNITY_DIRECTION_COLUMNS)


@dataclass
class OpportunityClusterRecord:
    run_id: str
    cluster_id: str
    direction_id: str
    market: str
    cluster_name: str = ""
    cluster_type: str = "PRODUCT"
    description: str = ""
    opportunity_count: int = 0
    core_count: int = 0
    adjacent_count: int = 0
    weekly_supported_count: int = 0
    monthly_supported_count: int = 0
    priority: float = 0.0
    evidence_summary: str = "{}"
    risk_summary: str = "{}"
    member_keys: str = "[]"
    created_at: str = ""
    updated_at: str = ""

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in OPPORTUNITY_CLUSTER_COLUMNS)


@dataclass
class OpportunityAnalysisRecord:
    run_id: str
    analysis_id: str
    opportunity_id: str
    market: str
    canonical_product_key: str = ""
    cluster_id: str = ""
    direction_id: str = ""
    product_type: str = ""
    attributes: str = "[]"
    style: str = "[]"
    target_group: str = "[]"
    evidence_state: str = "UNKNOWN"
    reason_tags: str = "[]"
    risk_tags: str = "[]"
    analysis_text: str = ""
    model: str = "rule-based-v2.1"
    created_at: str = ""
    updated_at: str = ""

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in OPPORTUNITY_ANALYSIS_COLUMNS)


# ---------------------------------------------------------------------------
# collection_log 表的列顺序（采集日志：每次采集尝试一条记录）
# ---------------------------------------------------------------------------
COLLECTION_LOG_COLUMNS = [
    "market",                # 市场，如 TH=泰国
    "level_1_category",      # 规范化一级类目
    "level_2_category",      # 规范化二级类目（一级数据为 None）
    "ranking_type",          # 热门搜索关键词 / 飙升关键词 / 高潜力关键词
    "period_granularity",    # month / week（该 ranking_type 的预期粒度）
    "period_start",          # Excel 实际日期（验证通过后填入）
    "period_end",            # Excel 实际日期
    "ui_period",             # UI 选择的候选周期（追溯用）
    "source_file",           # 原始 Excel 文件名
    "file_hash",             # 源文件 sha256 前 16 位
    "file_size_bytes",       # 文件大小
    "n_records",             # Excel 数据行数
    "inserted_rows",         # 入库 keyword_data 新增行数
    "skipped_duplicates",    # 因自然键重复跳过的行数
    "status",                # SUCCESS / DUPLICATE / COLLECTION_FAILED / EXPORT_FAILED / DOWNLOAD_FAILED / PARSE_FAILED / VALIDATION_FAILED
    "error",                 # 失败原因（非 SUCCESS 时填入）
    "mapping_table",         # JSON: 候选周期探测映射表（追溯用）
    "source",                # 数据来源: excel_export / page_top20
    "batch_id",              # 批次唯一标识（page_top20 一个完整 6 字段批次共用一个 batch_id）
    "collected_at",          # 采集时间戳
    "imported_at",           # 入库时间（未入库为 None）
]


@dataclass
class CollectionLogRecord:
    """一条采集日志（collection_log 一行）。"""
    market: str
    level_1_category: str
    ranking_type: str
    period_granularity: str
    collected_at: str
    level_2_category: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    ui_period: Optional[str] = None
    source_file: Optional[str] = None
    file_hash: Optional[str] = None
    file_size_bytes: Optional[int] = None
    n_records: Optional[int] = None
    inserted_rows: Optional[int] = None
    skipped_duplicates: Optional[int] = None
    status: str = ""
    error: Optional[str] = None
    mapping_table: str = "{}"
    source: str = "excel_export"
    batch_id: Optional[str] = None
    imported_at: Optional[str] = None

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in COLLECTION_LOG_COLUMNS)


# ---------------------------------------------------------------------------
# Page TOP20 页面原生排序采集数据（独立模型，不混入 keyword_data）
# ---------------------------------------------------------------------------

PAGE_TOP20_COLUMNS = [
    "market",                # 市场，如 TH=泰国
    "level_1_category",      # 规范化一级类目
    "level_2_category",      # 规范化二级类目（一级数据为 None）
    "ranking_type",          # 热门搜索关键词 / 飙升关键词
    "period_granularity",    # month / week
    "period_start",          # 页面实际周期开始日期
    "period_end",            # 页面实际周期结束日期
    "ranking_field",         # 排序指标: search_volume / product_clicks / sku_sales_index / on_sale_products / ctr_index / ctor_score
    "rank",                  # 该指标下的排名 1..20
    "keyword",               # 关键词原文（泰文原样）
    "display_value",         # 页面原始显示值，如 "7.47K" / "962.75"，绝不改写
    "numeric_value",         # 仅用于排序方向校验的可比数值，不作为展示依据
    "source_file",           # 溯源 TXT 文件名（如保留 TXT 输出）
    "batch_id",              # 批次 ID（同一 batch 6 个 ranking_field 共享）
    "collected_at",          # 采集时间戳
]


@dataclass
class PageTop20Record:
    """某周期某榜单某排序指标下的一个 TOP20 关键词行。

    重要语义约束：
    - 一行只代表一个 ranking_field 下的一个排名
    - 同一关键词出现在 6 个 ranking_field -> 6 行，不跨字段合并
    - display_value 必须保存 TikTok 页面原始显示值（如 "7.47K"、"962.75"）
    - numeric_value 仅用于排序方向校验，不能替代 display_value 作为展示
    - 某关键词只在一个指标的 TOP20 中，其他指标不生成记录（不补 0 不补 NULL）
    """
    market: str
    level_1_category: str
    ranking_type: str
    period_granularity: str
    period_start: str
    period_end: str
    ranking_field: str
    rank: int
    keyword: str
    display_value: str
    level_2_category: Optional[str] = None
    numeric_value: float = 0.0
    source_file: Optional[str] = None
    batch_id: Optional[str] = None
    collected_at: str = ""

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in PAGE_TOP20_COLUMNS)
