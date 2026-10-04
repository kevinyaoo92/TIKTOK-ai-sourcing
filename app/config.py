# -*- coding: utf-8 -*-
"""全局配置：路径、默认参数、机会评分权重与可调规则。

原则：
- 所有可调参数集中在此，便于"产品老板"后续调整业务规则。
- 支持环境变量/.env 覆盖（密钥只走环境变量，绝不写死在代码）。
- 机会评分是确定性 Python 计算，不调用任何 AI。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录 = 本文件(app/config.py)上两级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")  # 有 .env 则加载；没有也能跑（全走默认值）


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
DATA_RAW_DIR = Path(_env("DATA_RAW_DIR", str(PROJECT_ROOT / "data" / "raw")))
# 原始导出原样保存目录（不改动、不删字段）——泰国站 TikTok 导出文件
DATA_RAW_TIKTOK_THAILAND = DATA_RAW_DIR / "tiktok" / "thailand"
# 处理后产物（如机会列表 CSV）
DATA_PROCESSED_DIR = Path(_env("DATA_PROCESSED_DIR", str(PROJECT_ROOT / "data" / "processed")))
# SQLite 机会库
DATABASE_PATH = Path(_env("DATABASE_PATH", str(PROJECT_ROOT / "database" / "opportunity.db")))

# 已有、已验证的确定性导出自动化脚本（Playwright，无 AI），浏览器采集模式复用
TIKTOK_EXPORT_SCRIPT = PROJECT_ROOT / "project" / "automation" / "tiktok" / "tiktok_keyword_export.py"
TIKTOK_EXPORT_RESULT_JSON = PROJECT_ROOT / ".run" / "export_result.json"

# TikTok Shop Seller Center 固定入口（泰国跨境后台；seller.tiktok.com 会路由美区，不对）
SELLER_CENTER_URL = "https://seller.tiktokshopglobalselling.com/"
# 关键词榜单页（数据分析 → 商品卡 → 关键词榜单；热门/飙升/高潜力/月/类目在此页）
KEYWORD_RANK_URL = "https://seller.tiktokshopglobalselling.com/compass/search-analytics/keyword-rank?shop_region=TH"


# ---------------------------------------------------------------------------
# 数据采集默认值（第一阶段：热门搜索关键词 / 月 / 时尚配件 / 泰国）
# ---------------------------------------------------------------------------
DEFAULT_MARKET = _env("TIKTOK_MARKET", "TH")                 # 市场：TH=泰国
DEFAULT_CATEGORY = _env("TIKTOK_CATEGORY", "时尚配件")         # 类目（以页面实际为准）
DEFAULT_KEYWORD_TYPE = _env("TIKTOK_KEYWORD_TYPE", "热门搜索关键词")
DEFAULT_PERIOD_TYPE = _env("TIKTOK_PERIOD_TYPE", "month")     # month 月 / week 周


# ---------------------------------------------------------------------------
# 机会评分规则（可解释、透明、可调整）
# 权重相加后归一化；某因素数据不可用时剔除该因素并对其余重新归一。
# ---------------------------------------------------------------------------
SCORE_WEIGHTS = {
    "demand": 0.25,       # 搜索需求：搜索量在当期同类目词中的分位
    "growth": 0.20,       # 最近增长：环比(月比月/周比周)增长率线性映射
    "click": 0.15,        # 商品点击承接：商品点击数/搜索量 的分位
    "sales": 0.20,        # SKU销售指数分位（销售信号）
    "competition": 0.15,  # 竞争：在售商品数越低分越高（分位反转）
    "price": 0.05,        # 价格带：落在目标价格区间内才得分（可配）
}

GROWTH_BASE_SCORE = 50.0      # 增长率 0% 时的基准分
GROWTH_SCORE_PER_UNIT = 100.0 # score = clamp(基准 + 增长率 * 系数, 0, 100)；+10% 涨约 +10 分
NEW_KEYWORD_GROWTH_SCORE = 65.0  # 上期榜单无此词（新上榜）：无法算环比，给中性偏高默认

# 目标价格区间(泰铢 THB)。为 None 表示第一阶段不启用价格带过滤/计分。
# 示例: PRICE_BAND_THB = (10.0, 500.0)  → 平均价格落在该区间内得满分，越远越低。
PRICE_BAND_THB: tuple | None = None
if _env("PRICE_BAND_THB_MIN", "") and _env("PRICE_BAND_THB_MAX", ""):
    PRICE_BAND_THB = (float(os.environ["PRICE_BAND_THB_MIN"]), float(os.environ["PRICE_BAND_THB_MAX"]))

# 机会分 → 等级标签（分数越高越靠前命中；可调整）
LABEL_BANDS = (
    (80.0, "高机会"),
    (60.0, "中机会"),
    (0.0, "低机会"),
)

# 周/月数据综合标签规则（第二阶段接入周数据后启用；第一阶段仅月数据时不用此表）
# 说明见 README「周数据与月数据综合」，规则在代码中做成可调整数据结构。
WEEK_MONTH_LABELS = {
    ("月高", "周涨"): "稳定热门",
    ("月中", "周涨"): "新兴机会",
    ("月低", "周高"): "短期异常/观察中",
    ("月高", "周跌"): "热门回落观察",
    ("月中", "周平"): "平稳观察",
}


# ---------------------------------------------------------------------------
# DeepSeek / AI（第一阶段默认关闭，只预留接口，绝不承担浏览器/计算工作）
# 调用 AI 的唯一职责边界：泰语关键词语义理解、需求理解、1688搜索词生成、语义匹配。
# ---------------------------------------------------------------------------
AI_ENABLED = _env("AI_ENABLED", "false").lower() == "true"
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT = int(_env("DEEPSEEK_TIMEOUT", "60"))
# 单批 30 词的 JSON 输出约 6k~8k tokens；4096 会截断 → 默认 8192（仅输出预算，非语义改动）
DEEPSEEK_MAX_TOKENS = int(_env("DEEPSEEK_MAX_TOKENS", "8192"))

# ---------------------------------------------------------------------------
# AI 语义分析参数（第二阶段：TikTok 关键词 → 商品需求理解 → 1688 中文搜索词）
# 批量策略：每批最多 30 个关键词（默认 30），JSON 结构化返回；
# 失败自动重试、单条失败不影响整批、已成功关键词默认不重复调用。
# ---------------------------------------------------------------------------
AI_BATCH_SIZE = int(_env("AI_BATCH_SIZE", "30"))        # 每批关键词数(默认 30，硬上限 30)
AI_BATCH_MIN, AI_BATCH_MAX = 20, 30                     # 产品区间；AI_BATCH_MAX 为硬上限
AI_MAX_ATTEMPTS = int(_env("AI_MAX_ATTEMPTS", "3"))     # 单个关键词最多尝试次数
SEMANTIC_DEFAULT_LANGUAGE = _env("SEMANTIC_LANGUAGE", "th")  # 泰国市场关键词默认语言

# 进入 AI 语义层的候选上限 = screening_v1 中 passed 且 top_rank<=本值的词。
# 【V1 假设：默认 80，未经回测验证，不代表最优值；待多期数据后校准】
AI_CANDIDATE_LIMIT = int(_env("AI_CANDIDATE_LIMIT", "80"))

# DeepSeek 真实调用总批数硬预算（V1.1 额度控制，最高优先级）：30 + 30 + 20 = 最多 3 批。
# preflight 计算预计批数 > 本值时立即停止，禁止执行真实调用。
AI_MAX_BATCHES = int(_env("AI_MAX_BATCHES", "3"))

# ---------------------------------------------------------------------------
# V1.1 商品机会归一化层配置
#
# 流程: V1 Top80 → DeepSeek 一次性语义(含商品归一化) → Python 聚类(canonical_product_key)
#       → Python 商品机会评分 → 最终 ≤ FINAL_OPPORTUNITY_LIMIT 个商品机会
#
# 规则:
#   - 聚类/评分/排序/选择全部 Python，禁止再次调用 DeepSeek。
#   - 禁止把同机会多关键词搜索量相加（同一需求的不同表达）；用"最强关键词 +
#     相关词数量(有限证据) + 购买信号 + 供需"作证据。
#   - 商品机会最小单位 = 消费者需求对象 + 核心商品形态 + 1688 搜索对象基本一致。
# ---------------------------------------------------------------------------
# 最终用户可见的商品机会上限：最多 9 个；不足 9 个就少展示，禁止降质凑数。
# 测试阶段与正式产品使用同一规则。
FINAL_OPPORTUNITY_LIMIT = int(_env("V11_FINAL_LIMIT", "9"))

# 商品机会评分（V1.1 第一版暂定）：
#   Score = 0.35×市场需求(best DemandPct)
#         + 0.30×购买意图(best PurchaseIntent)
#         + 0.20×供需机会(best OpportunityGap)
#         + 0.15×需求证据一致性(EvidenceConsistency)
V11_SCORE_WEIGHTS = {
    "demand": 0.35,             # 市场需求：组内最高质量关键词的 DemandPct
    "purchase_intent": 0.30,    # 购买意图：复用 V1 PurchaseIntent
    "opportunity_gap": 0.20,    # 供需机会：复用 V1 OpportunityGap
    "evidence_consistency": 0.15,  # 需求证据一致性（有限增强，防同义词数量作弊）
}

# EvidenceConsistency：不能简单用 keyword_count 线性加分；同义词不能制造虚假需求。
# evidence_score = min(cap, base + step × (min(count, max_count) - 1))
#  → 1 词=base(基础分) / 2 词 / ≥3 词到 cap，超过 max_count 不再增加（防作弊）。
V11_EVIDENCE = {
    "base": 0.4,        # 单关键词基础分
    "step": 0.3,        # 每多一个相关关键词增加
    "cap": 1.0,         # 证据分上限
    "max_count": 3,     # 超过 3 个相关词不再加分
}


# ---------------------------------------------------------------------------
# V1 初期关键词筛选层配置（第一阶段筛选漏斗；所有阈值/权重/TopN 集中于此可调）
#
# 数据流: keyword_data → 数据清洗 → 非商品意图过滤 → 高需求主通道
#         → 高购买意图保护通道 → Opportunity Score → Top N candidate
#
# 计分规则（依据 V1 数据分析报告与产品规则，全部 Python 计算，不调 AI）：
#   demand_pct        = 搜索量在同期同类目词中的百分位(0~1)   ← 需求簇唯一代表
#                        （搜索量与商品点击数 ρ=0.975，二者不得同时进主评分；
#                          商品点击数仅作展示，不进公式）
#   supply_pct        = 在售商品百分位(0~1)，越高=供给越饱和
#   opportunity_gap   = demand_pct - supply_pct（可为负；不用"在售<X"做硬判断）
#   ctor_pct / sku_pct = CTOR评分 / SKU销售指数 百分位(0~1)
#   purchase_intent   = 0.70×ctor_pct + 0.30×sku_pct（CTOR=核心，SKU=辅助）
#   opportunity_score = 0.40×demand_pct + 0.30×purchase_intent + 0.30×opportunity_gap
#   趋势(环比)不进 V1：目前数据不足以支持趋势模型。
# ---------------------------------------------------------------------------
# 默认候选数量 80 —— 【V1 假设，未经历史回测验证，不代表最优值；待多期数据后校准】
V1_TOP_N = int(_env("V1_TOP_N", "80"))

V1_WEIGHTS = {
    "demand": 0.40,             # 需求(搜索量分位)
    "purchase_intent": 0.30,    # 购买意图
    "opportunity_gap": 0.30,    # DemandPct - SupplyPct
}
V1_PURCHASE_INTENT_WEIGHTS = {
    "ctor_percentile": 0.70,    # CTOR = 购买意图核心指标
    "sku_percentile": 0.30,     # SKU销售指数 = 购买意图辅助指标
}

# 通道规则
V1_LOW_DEMAND_PCT = 0.25        # 搜索量分位 < P25 → 默认低需求通道(淘汰)
V1_HIGH_INTENT_CTOR_PCT = 0.75  # CTOR 分位 ≥ P75 → 高购买意图(低需求词可经保护通道放行)

# 非商品意图过滤：精确黑名单(可扩展，产品老板可增删) + 模式规则(见 screening.py)
# 来源：V1 报告人工复核的约 30 个高置信非商品/促销/跨区垃圾词；
# 已知误报词(ของ 前缀等真实商品)不在此列。额外黑名单文件见下方 V1_NONPRODUCT_BLACKLIST_FILE。
V1_NONPRODUCT_EXACT_BLACKLIST = (
    "6767", "1.บ.", "1•บาท", "1 บาทส่งฟรี", "1 บาทลูกค้าใหม่", "1บ.ลูกค้าใหม่",
    "ราคา0.01บาท", "0.00ลูกค้าใหม่", "0 01ลูกค้าใหม่ฟรี", "0•01ลูกค้าฟรี",
    "0บาทส่งฟรี", "0.01ลูกค้าใหม่ส่งฟรี 1บาท", "00•01บาท", "0•001บาท",
    "ขอ0.01บาท", "ขอสินค้าฟรีจาก tiktok 2026", "ขอสินค้าฟรีจาก tiktok ล่าสุด",
    "ขอสินค้าฟรีจาก tiktok ยังไง", "ขอสินค้าตัวอย่างฟรี tiktok",
    "สินค้า 1 บาทส่งฟรีตอนนี้", "ส่งฟรีขั้นต่ำ 0.-", "ส่งฟรีขั้นต่ำ0บาท",
    "1 piso all items sa tiktok shop", "1 piso items sa tiktok shop live now",
    "1 piso items sa tiktok shop order", "1 ₱ item today all item",
    "1 ₱ cash on delivery free shipping", "1 ₱ all items free shipping legit 2026",
    "1 peso free shipping available today", "cash delivery tiktok shop",
    "tiktokshoppaydayคูปองลด50", "สินค้ายอดฮิตในตอนนี้ tiktok",
    "สินค้าที่อนุมัติ pay laterวงเงิน4500",
)
# 可扩展黑名单文件（可选）：每行一个关键词(UTF-8)，与内置黑名单合并；空则只用内置。
V1_NONPRODUCT_BLACKLIST_FILE = _env("V1_NONPRODUCT_BLACKLIST_FILE", "")


def ensure_dirs() -> None:
    """创建运行所需目录（幂等）。"""
    for d in (DATA_RAW_TIKTOK_THAILAND, DATA_PROCESSED_DIR, DATABASE_PATH.parent):
        d.mkdir(parents=True, exist_ok=True)
