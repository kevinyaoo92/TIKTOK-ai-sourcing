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