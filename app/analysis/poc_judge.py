# -*- coding: utf-8 -*-
"""阶段 3B：单商品判断器。严格按四档规则判定。"""
from __future__ import annotations

LEVELS = [
    {"name": "严格", "rate": 60, "rate24": 60, "stock": 2000},
    {"name": "中等", "rate": 50, "rate24": 40, "stock": 2000},
    {"name": "宽松", "rate": 40, "rate24": 20, "stock": 1000},
    {"name": "最宽", "rate": 30, "rate24": 0,  "stock": 1000},
]

SKU_MAX = 8
MOQ_MAX = 1

_FEMALE_WORDS = ["女士", "女装", "女款", "女式", "女性", "女", "woman", "women", "female", "ladies", "lady"]
_MALE_WORDS = ["男士", "男装", "男款", "男式", "男性", "男", "man", "men", "male"]
_KIDS_WORDS = ["儿童", "童装", "宝宝", "婴幼", "婴童", "男童", "女童", "幼儿", "婴儿", "kids", "baby", "children", "中大童", "小童", "大童"]
_UNISEX_WORDS = ["男女同款", "男女通用", "情侣", "中性", "unisex", "for couple"]


def title_matches_keyword(title, keyword):
    if not title:
        return False
    t = title.lower()
    k = keyword.lower()
    kw_has_kids = any(w in k for w in _KIDS_WORDS)
    kw_has_female = (not kw_has_kids) and any(w in k for w in _FEMALE_WORDS)
    kw_has_male = (not kw_has_kids) and any(w in k for w in _MALE_WORDS)
    t_has_kids = any(w in t for w in _KIDS_WORDS)
    t_has_female = (not t_has_kids) and any(w in t for w in _FEMALE_WORDS)
    t_has_male = (not t_has_kids) and any(w in t for w in _MALE_WORDS)
    t_has_unisex = any(w in t for w in _UNISEX_WORDS)
    if kw_has_female:
        if t_has_unisex or t_has_kids or (t_has_male and not t_has_female):
            return False
    if kw_has_male:
        if t_has_unisex or t_has_kids or (t_has_female and not t_has_male):
            return False
    if kw_has_kids:
        if not t_has_kids:
            return False
    return True


def judge_single(product, keyword=None):
    main = product.get("main") or {}
    dom = product.get("dom") or {}

    dims = main.get("dims") or {}
    min_stock = main.get("min_stock")
    if min_stock is None:
        min_stock = dom.get("min_stock")

    rate = dom.get("rate")
    rate24 = dom.get("rate24")
    moq = dom.get("moq")
    title = dom.get("title") or ""

    for dim_name, n in dims.items():
        if n > SKU_MAX:
            return {"result": "reject", "tier": None,
                    "reason": "SKU " + dim_name + "=" + str(n) + " > " + str(SKU_MAX)}

    if moq is None:
        return {"result": "reject", "tier": None, "reason": "MOQ 未抓到"}
    if moq > MOQ_MAX:
        return {"result": "reject", "tier": None,
                "reason": "MOQ=" + str(moq) + " > " + str(MOQ_MAX)}

    if min_stock is None:
        return {"result": "reject", "tier": None, "reason": "库存未抓到"}

    if keyword:
        if not title_matches_keyword(title, keyword):
            return {"result": "reject", "tier": None, "reason": "性别不一致"}

    attempts = []
    for lv in LEVELS:
        if rate is None:
            rate_ok = False
        else:
            rate_ok = rate > lv["rate"]

        if rate24 is None:
            rate24_ok = True
        else:
            rate24_ok = rate24 > lv["rate24"]

        stock_ok = min_stock >= lv["stock"]

        attempts.append({
            "tier": lv["name"],
            "rate_ok": rate_ok,
            "rate24_ok": rate24_ok,
            "stock_ok": stock_ok,
        })

        if rate_ok and rate24_ok and stock_ok:
            return {"result": "pass", "tier": lv["name"], "reason": "",
                    "attempts": attempts}

    return {"result": "reject", "tier": None,
            "reason": "四档全部不通过", "attempts": attempts}
# ========== 阶段 4：卡片层过滤 ==========
import json as _json
import requests as _requests

SHOP_IMG_SIGS = ["4815-2-tps-232-56", "1330-2-tps-264-64"]

_AI_SYS_PROMPT = """你是1688商品类目判定助手。用户给搜索词和一批商品卡片标题，你剔除明显不属于搜索词类目的卡片。

【主商品判定】
标题里最后一个商品名词是主商品，前面的都是描述/修饰词。
例: 新款耐高温防滑隔热加厚迷你手持熨烫板挂烫防烫手套 -> 主商品是防烫手套，不是烫衣板
例: 女士连衣裙【送发夹】 -> 主商品是连衣裙，发夹是赠品

【赠品剔除规则】
【】()[]里的词、买X送Y、赠、附赠、送 里的词一律不算主商品。

【剔除条件】
- 搜索词指向的类目与标题主商品不一致 -> 剔除
- 搜索词是女士/女装/女款，标题明确含童装词 -> 剔除
- 搜索词是男士/男装/男款，标题明确含童装词 -> 剔除
- 搜索词是女士，标题明确是纯男装 -> 剔除
- 搜索词是男士，标题明确是纯女装 -> 剔除

【关键约束】
- 拿不准的一律保留
- 宁可少剔除，不可多剔除
- 只返回 JSON，不要解释
格式: {"rejected": [{"id": "xxx", "reason": "简短理由"}]}
若无明显错的返回 {"rejected": []}"""


def has_biz_tag(shop_imgs):
    for img in (shop_imgs or []):
        for sig in SHOP_IMG_SIGS:
            if sig in img:
                return True
    return False


def ai_filter_cards(cards, keyword):
    from app import config
    api_key = getattr(config, "DEEPSEEK_API_KEY", "") or ""
    if not api_key or not cards:
        return cards
    lines = ["搜索词: " + keyword, "", "共 " + str(len(cards)) + " 张卡片:"]
    for c in cards:
        title = (c.get("title") or c.get("text") or "")[:80]
        lines.append("- id=" + str(c.get("offer_id", "")) + " 标题=" + title)
    user_prompt = "\n".join(lines)
    try:
        r = _requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": _AI_SYS_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            },
            timeout=25
        )
        if r.status_code != 200:
            return cards
        content = r.json()["choices"][0]["message"]["content"]
        parsed = _json.loads(content)
        rejected = parsed.get("rejected", []) or []
        rejected_ids = set()
        for x in rejected:
            if isinstance(x, dict):
                rid = str(x.get("id", "")).strip()
                if rid:
                    rejected_ids.add(rid)
        return [c for c in cards if str(c.get("offer_id", "")) not in rejected_ids]
    except Exception:
        return cards


def card_filter(cards, keyword):
    n_input = len(cards)
    cards = [c for c in cards if has_biz_tag(c.get("shop_imgs"))]
    n_biz = len(cards)
    cards = [c for c in cards if title_matches_keyword(c.get("title") or c.get("text") or "", keyword)]
    n_gender = len(cards)
    cards = ai_filter_cards(cards, keyword)
    n_ai = len(cards)
    cards.sort(key=lambda x: -(x.get("rate") or 0))
    return {
        "cards": cards,
        "stats": {"input": n_input, "after_biz": n_biz, "after_gender": n_gender, "after_ai": n_ai}
    }
