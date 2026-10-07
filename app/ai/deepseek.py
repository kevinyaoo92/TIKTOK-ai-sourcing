# -*- coding: utf-8 -*-
"""DeepSeek 语义接口（HTTP，确定性封装；密钥只走环境变量）。

第二阶段状态（AI 理解商品需求，本模块是唯一 AI 入口）：
- 允许的唯一任务 analyze_keywords_batch：批量(20~50词)完成
  泰语关键词语义理解 → 商品需求判断/商品分类/同义词归类 → 生成 1688 中文搜索词。
- AI_ENABLED=false 或缺少 DEEPSEEK_API_KEY 时，一切调用被 DeepSeekDisabledError 拒绝
  （AI Guard，第一阶段保留），防止"悄悄调用 AI"。
- AI 只做语义：不承担浏览器、数值计算、排序、数据库（全部由 Python 完成）。

后续阶段（1688 匹配等）经产品老板确认后，以新任务名加入 ALLOWED_TASKS 白名单。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app import config

logger = logging.getLogger("deepseek")

# 业务白名单：AI 只能执行本阶段允许的语义任务（防止范围蔓延）
ALLOWED_TASKS = (
    "analyze_keywords_batch",   # 泰语关键词批量语义解析 → 含义/分类/1688搜索词
    "analyze_opportunity",      # 单个商品机会语义分析（Top N 机会；选品专家）
)

# 关键词批量语义解析的 system 提示词（AI 只输出 JSON，禁止解释/寒暄）
_SYSTEM_SEMANTIC_PROMPT = """你是泰国 TikTok 跨境电商商品需求分析助手。用户会给你一批已通过市场机会初筛的 TikTok 搜索关键词（来自泰国站，多为时尚配件/饰品/发饰/帽饰/腰带/眼镜等类目，含泰语/英语/缅甸语/数字/促销短语）。

对每个关键词做如下语义理解：
1. intent_status：判断该词的"购买意图类型"，三选一，不要用其它值：
   - PRODUCT：明确指向可采购/可生产的具体商品（如 สร้อยคอ 项链、แมสก์ 口罩）。
   - NON_PRODUCT：平台活动/促销/优惠券/物流/支付/索取免费样品等非商品搜索意图
     （如 ราคา0.01บาท、ขอสินค้าฟรี、ပို့ခfree、包邮、比索促销），即使它搜索量很高。
   - AMBIGUOUS：无法确定是否为商品、或词义过短/过泛/可商品可非商品时，一律给 AMBIGUOUS；
     不要为了"有用"而强行判断成 PRODUCT，也不要强行否定。
2. is_product：与 intent_status 严格一致——PRODUCT→1，NON_PRODUCT→0；AMBIGUOUS 时可不给或给 0/1（服务端按 AMBIGUOUS 处理）。
3. language：关键词的主要语言（ISO 639-1，如 th/en/zh/my；缅甸语用 my）。
4. meaning_zh：简洁中文含义（说明这是商品还是什么搜索意图）。
5. product_category / product_subcategory：PRODUCT 时用中文电商常用类目词
   （如 饰品(项链/耳饰/戒指/手链/胸针…)、发饰(发夹/发圈/假发/发箍…)、帽子/腰带/眼镜/围巾/手套/手表…；
   无法归入常用类目用"其他"+子类说明）。NON_PRODUCT/AMBIGUOUS 可给空字符串。
6. search_terms_1688：PRODUCT 时生成 2~5 个**面向 1688 供应链搜索**的中文词：
   - 覆盖材质/款式/用途/人群等供应链表达（如 "不锈钢项链" "韩版项链" "女士锁骨链"）
   - 不要营销文案、不要完整商品标题、不要编造不存在的品牌。
   NON_PRODUCT 给空数组 []；AMBIGUOUS 可给候选词或空数组。
7. canonical_product_name（PRODUCT 必填）：标准商品机会名称——用户可直接理解的具体商品名。
   正确示例："女士发圈"、"女士腰带"、"防晒帽"、"假刘海"、"发箍"。
   错误示例："女性配饰"、"时尚配件"、"发饰"、"美妆用品"、"夏季用品"（过泛大类，不是具体商品）。
   NON_PRODUCT/AMBIGUOUS 给空字符串。
8. canonical_product_key（PRODUCT 必填）：商品机会归一化键——供 Python 聚类的稳定标准键。
   - 用英文小写字母/数字/下划线（如 hair_tie、hair_clip、hair_band、sunglasses）
   - 同一商品机会（消费者需求对象+核心商品形态+1688 搜索对象一致）必须给**相同** key；
     不同商品形态必须给不同 key；**禁止仅因一级分类相同就合并**。
   - 示例：发圈/时尚发圈/扎头发橡皮筋 → hair_tie；发夹/女士发夹 → hair_clip；发箍/女士发箍 → hair_band。
   - 必须根据真实关键词判断，不要套用示例。
   NON_PRODUCT/AMBIGUOUS 给空字符串。
9. confidence：你对本次理解的置信度，0~1。**置信度低也照常输出该条目**，不要省略、不要因低置信度丢弃。
   不确定就同时把 intent_status 设为 AMBIGUOUS 并给较低 confidence。

输出要求（严格遵守）：
- 只输出一个 JSON 数组，不要输出任何其它文字/markdown。
- 数组元素按输入顺序对应，每项必须含：
  {"index": 输入序号, "original_keyword": 原词回显, "language": "...",
   "intent_status": "PRODUCT|NON_PRODUCT|AMBIGUOUS", "is_product": 1 或 0,
   "meaning_zh": "...", "product_category": "...", "product_subcategory": "...",
   "canonical_product_name": "...", "canonical_product_key": "...",
   "search_terms_1688": ["...", "..."], "confidence": 0.0}
- 数组长度必须与输入关键词数量一致；不确定的项也要输出，不要省略。"""


# 商品机会分析（Stage 5）：选品专家提示词模板（用户指定，只做语义判断）
_SYSTEM_OPPORTUNITY_PROMPT = """你是泰国 TikTok Shop 选品专家。请根据以下关键词数据，输出商品机会分析。

输入：
- 类目：{level1} > {level2}
- 周期：{period}
- 代表关键词：{keyword}
- 搜索量：{search_volume}（类目内 {demand_pct}）
- CTOR：{ctor_score}（类目内 {intent_pct}）
- SKU销售指数：{sku_sales_index}（类目内 {sales_pct}）
- 在售商品：{on_sale_products}（类目内 {competition_pct}）
- CTR：{ctr_index}（仅参考）
- 机会分：{opportunity_score}，等级：{rating}

请输出 JSON：
{{
  "normalized_product_name_cn": "中文商品方向名",
  "product_intent": "商品/非商品/不确定",
  "market_judgment": "市场需求判断，1-2句",
  "competition_judgment": "竞争判断，1-2句",
  "suitable_for_tiktok_th": "是否适合泰国TikTok Shop，1-2句",
  "reasons": ["推荐理由1", "推荐理由2", "推荐理由3"],
  "risks": ["风险1", "风险2"],
  "search_terms_1688": ["搜索词1", "搜索词2", "搜索词3"],
  "confidence": 0.86
}}

要求：
- 只做语义理解和判断，不计算分数、不排序、不给百分位。
- 不承诺爆款，不写"保证""稳赚"。
- 泰语关键词需要准确理解。
- 1688搜索词用中文，3-8个。禁止包含地域词（泰国、泰式、东南亚、跨境、海外、Thai 等）；如核心词本身含"泰国"（如"泰国大象裤"），改用通用词（如"大象裤"）；如商品本身只在泰国本地销售（如泰国学生课本、校服、教师制服、泰国东北部民族服饰），search_terms_1688 给空数组 []。
- 只输出 JSON，不要输出任何其它文字/markdown。"""


class DeepSeekDisabledError(RuntimeError):
    """AI 未启用（默认）或缺少 API Key。"""


class DeepSeekError(RuntimeError):
    """调用 DeepSeek 失败（网络/限流/返回异常/JSON 非法）。"""


def _require_enabled() -> None:
    if not config.AI_ENABLED:
        raise DeepSeekDisabledError(
            "AI 未启用（config.AI_ENABLED=false，默认关闭）。"
            "如需第二阶段语义分析，请在 .env 设置 AI_ENABLED=true 与 DEEPSEEK_API_KEY。"
        )
    if not config.DEEPSEEK_API_KEY:
        raise DeepSeekDisabledError("缺少 DEEPSEEK_API_KEY（请在 .env 配置，勿写死在代码）。")


def _check_task(task: str) -> None:
    if task not in ALLOWED_TASKS:
        raise ValueError(f"任务 {task} 不在 AI 语义任务白名单: {ALLOWED_TASKS}")


def chat_completion(
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 800,
) -> str:
    """调用 DeepSeek chat completions，返回回复文本。"""
    _require_enabled()
    import requests  # 懒加载，保持本模块 import 零副作用

    try:
        resp = requests.post(
            f"{config.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
            json={
                "model": config.DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout=config.DEEPSEEK_TIMEOUT,
        )
    except requests.RequestException as e:
        raise DeepSeekError(f"请求 DeepSeek 失败: {e}") from e
    if resp.status_code != 200:
        raise DeepSeekError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise DeepSeekError(f"DeepSeek 返回结构异常: {data}") from e


def _json_reply(system_prompt: str, user_payload: dict, *, temperature: float = 0.1) -> object:
    """带 JSON 输出约定的语义调用；非 JSON 回复抛 DeepSeekError（由上层记录重试）。"""
    content = chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        temperature=temperature,
        max_tokens=config.DEEPSEEK_MAX_TOKENS,
    )
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise DeepSeekError(
            f"AI 返回非法 JSON（无法解析）：{content[:200]!r}"
        ) from e


def analyze_keywords_batch(keywords: list[str], *, temperature: float = 0.1) -> list[dict]:
    """批量语义解析一批关键词（20~50 个为宜；JSON 结构化输出）。

    输入：关键词列表（纯文本，来自 keyword_data，Python 侧已取好，非 DOM/截图）。
    返回：list[dict]，每项含 index/original_keyword/language/meaning_zh/
          product_category/product_subcategory/search_terms_1688/is_product/confidence。

    说明：
    - 单批内个别项异常由上层(service)隔离处理，不影响整批入库；
    - 整批解析失败抛 DeepSeekError，由上层记录 attempts 后供下次重试；
    - 本函数只做语义理解，绝不算任何市场数字。
    """
    _check_task("analyze_keywords_batch")
    if not keywords:
        return []
    if len(keywords) > config.AI_BATCH_MAX:  # 硬上限(30)，见 config
        raise ValueError(f"单批最多 {config.AI_BATCH_MAX} 个关键词，收到 {len(keywords)}（请分批）")

    payload = {"words": keywords}
    data = _json_reply(_SYSTEM_SEMANTIC_PROMPT, payload, temperature=temperature)
    if not isinstance(data, list):
        raise DeepSeekError(f"AI 返回不是 JSON 数组: {str(data)[:200]!r}")
    return data


# ---------------------------------------------------------------------------
# Stage 5：商品机会分析（选品专家）
# ---------------------------------------------------------------------------
def analyze_opportunity(opp_input: dict, *, temperature: float = 0.1,
                        max_retries: int = 2) -> dict:
    """对单个 Top N 机会调用 DeepSeek 选品专家分析，返回结构化 JSON。

    输入 opp_input（由 Python 侧构造，含 level1/level2/period/keyword/
    search_volume/ctor_score/sku_sales_index/on_sale_products/
    ctr_index/demand_pct/intent_pct/sales_pct/competition_pct/opportunity_score/rating）。

    返回 dict（用户规定字段）：
      normalized_product_name_cn / product_intent(商品|非商品|不确定) /
      market_judgment / competition_judgment / suitable_for_tiktok_th /
      reasons / risks / search_terms_1688 / confidence

    失败策略：重试 max_retries 次（每次间隔递增），仍失败抛 DeepSeekError
    （由上层记录 ai_analysis_log 并使用占位文本，不阻塞流程）。
    """
    _check_task("analyze_opportunity")

    prompt = _SYSTEM_OPPORTUNITY_PROMPT.format(
        level1=opp_input.get("level1", ""),
        level2=opp_input.get("level2", ""),
        period=opp_input.get("period", ""),
        keyword=opp_input.get("keyword", ""),
        search_volume=opp_input.get("search_volume", "—"),
        demand_pct=_fmt_pct(opp_input.get("demand_pct")),
        ctor_score=opp_input.get("ctor_score", "—"),
        intent_pct=_fmt_pct(opp_input.get("intent_pct")),
        sku_sales_index=opp_input.get("sku_sales_index", "—"),
        sales_pct=_fmt_pct(opp_input.get("sales_pct")),
        on_sale_products=opp_input.get("on_sale_products", "—"),
        competition_pct=_fmt_pct(opp_input.get("competition_pct")),
        ctr_index=opp_input.get("ctr_index", "—"),
        opportunity_score=opp_input.get("opportunity_score", "—"),
        rating=opp_input.get("rating", "—"),
    )
    payload = {
        "opportunity": {
            "level1": opp_input.get("level1", ""),
            "level2": opp_input.get("level2", ""),
            "period": opp_input.get("period", ""),
            "keyword": opp_input.get("keyword", ""),
            "metrics": {
                "search_volume": opp_input.get("search_volume"),
                "demand_pct": opp_input.get("demand_pct"),
                "ctor_score": opp_input.get("ctor_score"),
                "intent_pct": opp_input.get("intent_pct"),
                "sku_sales_index": opp_input.get("sku_sales_index"),
                "sales_pct": opp_input.get("sales_pct"),
                "on_sale_products": opp_input.get("on_sale_products"),
                "competition_pct": opp_input.get("competition_pct"),
                "ctr_index": opp_input.get("ctr_index"),
            },
            "opportunity_score": opp_input.get("opportunity_score"),
            "rating": opp_input.get("rating"),
        }
    }

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            data = _json_reply(prompt, payload, temperature=temperature)
            if not isinstance(data, dict):
                raise DeepSeekError(f"AI 返回不是 JSON 对象: {str(data)[:200]!r}")
            return data
        except DeepSeekDisabledError:
            raise  # 配置缺失，不重试
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("analyze_opportunity 第 %d 次失败: %s", attempt + 1, e)
            if attempt < max_retries:
                import time as _time
                _time.sleep(2 * (attempt + 1))
    raise DeepSeekError(f"analyze_opportunity 重试 {max_retries} 次仍失败: {last_err}")


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(v)
