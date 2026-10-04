# -*- coding: utf-8 -*-
"""1688 匹配模块 V0.1

真实单机会闭环：机会 -> 1688 搜索词 -> 商品详情 -> 6 项供应商条件 -> JSON

技术路线（已验证）：
- 搜索：browser_navigate + &charset=UTF-8 URL（避免 GBK 乱码）
- 提取：browser_evaluate 读取页面 innerText + 正则匹配
- 验证码：暂停等待人工通过
- 超时：browser_navigate 超时不等于失败，用 browser_evaluate 确认实际状态
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus


@dataclass
class SupplierCheck:
    """6 项供应商条件检查结果。"""
    product_repurchase_raw: Optional[str] = None
    sku_count_raw: Optional[str] = None
    merchant_type_raw: Optional[str] = None
    merchant_deposit_raw: Optional[str] = None
    pickup_24h_raw: Optional[str] = None
    shop_repurchase_raw: Optional[str] = None
    product_repurchase: str = "UNKNOWN"
    sku_count: str = "UNKNOWN"
    merchant_type: str = "UNKNOWN"
    merchant_deposit: str = "UNKNOWN"
    pickup_24h: str = "UNKNOWN"
    shop_repurchase: str = "UNKNOWN"
    # 证据类型: exact / estimated / unavailable
    product_repurchase_et: str = "unavailable"
    sku_count_et: str = "unavailable"
    merchant_type_et: str = "unavailable"
    merchant_deposit_et: str = "unavailable"
    pickup_24h_et: str = "unavailable"
    shop_repurchase_et: str = "unavailable"
    evidence_page: str = ""
    evidence_url: str = ""

    @property
    def final_status(self) -> str:
        checks = [
            self.product_repurchase, self.sku_count, self.merchant_type,
            self.merchant_deposit, self.pickup_24h, self.shop_repurchase,
        ]
        if any(c == "REJECT" for c in checks):
            return "REJECT"
        if any(c == "UNKNOWN" for c in checks):
            return "UNKNOWN"
        return "PASS"


@dataclass
class ProductMatch:
    search_term: str = ""
    product_name: str = ""
    product_url: str = ""
    merchant_name: str = ""
    merchant_url: str = ""
    offer_id: str = ""
    check: SupplierCheck = field(default_factory=SupplierCheck)
    captcha_encountered: bool = False
    navigation_timeout: bool = False
    error: Optional[str] = None


MERCHANT_TYPE_WHITELIST = {"源头旗舰", "实力商家", "超级工厂"}


def _parse_merchant_type(text: str) -> set:
    if not text:
        return set()
    found = set()
    for t in MERCHANT_TYPE_WHITELIST:
        if t in text:
            found.add(t)
    return found


def _count_skus_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    return text.count("¥")


def _is_necklace_product(product_name: str) -> bool:
    if not product_name:
        return False
    necklace_keywords = ["项链", "锁骨链", "颈链", "项饰", "毛衣链"]
    has_necklace = any(k in product_name for k in necklace_keywords)
    exclude_keywords = ["镊子", "药品", "贴纸", "玩具", "充电器", "鼠标", "风扇", "拖鞋", "泳衣", "马甲", "床边", "餐具", "手镯", "手链", "耳环", "胸针", "钥匙扣"]
    is_excluded = any(k in product_name for k in exclude_keywords)
    return has_necklace and not is_excluded


def apply_supplier_checks(body_text: str) -> SupplierCheck:
    check = SupplierCheck()

    # 1. 商品回头率 >40%
    # 识别优先级:
    #   (a) "商品回头率" 确切字段 → exact
    #   (b) 厂家悬停卡片左下角无前缀"回头率" → estimated (用户图示指引位置)
    #   (c) 仅有"留货率" → unavailable (明确非目标字段，禁止替代)
    m_exact = re.search(r"商品回头率[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
    if m_exact:
        check.product_repurchase_raw = m_exact.group(0)
        val = float(m_exact.group(1))
        check.product_repurchase = "PASS" if val > 40 else "REJECT"
        check.product_repurchase_et = "exact"
    else:
        # 排除"店铺回头率"和"留货率"后，查找无前缀的"回头率"
        # 使用负向断言: 前面不能是"店铺"或"留货"
        m_card = re.search(r"(?<![店铺留货])回头率[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
        has_liuhuo = re.search(r"留货率[^\d]*(\d+(?:\.\d+)?)\s*%", body_text) is not None
        if m_card:
            check.product_repurchase_raw = m_card.group(0) + " (厂家卡片回头率，无商品前缀)"
            val = float(m_card.group(1))
            check.product_repurchase = "PASS" if val > 40 else "REJECT"
            check.product_repurchase_et = "estimated"
        elif has_liuhuo:
            m_lh = re.search(r"留货率[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
            check.product_repurchase_raw = m_lh.group(0) + " (留货率，非商品回头率)"
            check.product_repurchase = "UNKNOWN"
            check.product_repurchase_et = "unavailable"
        else:
            check.product_repurchase_raw = None
            check.product_repurchase_et = "unavailable"

    # 2. SKU 数量 <=10 (优先颜色/规格，降级才用¥符号估算)
    color_match = re.search(r"颜色[:：\s]*([^\n]{1,200})", body_text)
    if color_match:
        colors = re.split(r"[、,，\s]+", color_match.group(1))
        colors = [c for c in colors if c.strip()]
        cnt = len(colors)
        check.sku_count_raw = f"{cnt} (颜色SKU: {color_match.group(1)[:50]})"
        check.sku_count = "PASS" if cnt <= 10 else "REJECT"
        check.sku_count_et = "exact"
    else:
        sku_count = _count_skus_from_text(body_text)
        if sku_count is not None and sku_count > 0:
            check.sku_count_raw = f"~{sku_count} (¥符号计数，估算值)"
            check.sku_count = "PASS" if sku_count <= 10 else "REJECT"
            check.sku_count_et = "estimated"
        else:
            check.sku_count_raw = None
            check.sku_count_et = "unavailable"

    # 3. 商家类型 (严格匹配白名单: 源头旗舰/实力商家/超级工厂)
    found_types = _parse_merchant_type(body_text)
    supply_chain = re.search(r"(\d+星)供应链", body_text)
    if found_types:
        check.merchant_type_raw = ",".join(found_types)
        check.merchant_type = "PASS"
        check.merchant_type_et = "exact"
    elif supply_chain:
        check.merchant_type_raw = supply_chain.group(0) + " (五星供应链，非白名单目标字段)"
        check.merchant_type = "UNKNOWN"
        check.merchant_type_et = "unavailable"
    else:
        check.merchant_type_raw = None
        check.merchant_type_et = "unavailable"

    # 4. 商家保证金 >10000 (支持千分位逗号 ¥15,987.74)
    m = re.search(r"保证金[^\d]*([\d,]+(?:\.\d+)?)", body_text)
    if m:
        # 去除千分位逗号再转 float
        num_str = m.group(1).replace(",", "")
        val = float(num_str)
        check.merchant_deposit_raw = f"保证金 {val}"
        check.merchant_deposit = "PASS" if val > 10000 else "REJECT"
        check.merchant_deposit_et = "exact"
    else:
        check.merchant_deposit_raw = None
        check.merchant_deposit_et = "unavailable"

    # 5. 24小时揽收率 >60% (严格区分 24h 与 48h)
    m24 = re.search(r"24[小时hH]*[揽攬][收率]+[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
    if m24:
        check.pickup_24h_raw = m24.group(0)
        val = float(m24.group(1))
        check.pickup_24h = "PASS" if val > 60 else "REJECT"
        check.pickup_24h_et = "exact"
    else:
        # 只找到 48h 揽收率 → 非目标字段，标记 UNKNOWN
        m48 = re.search(r"48[小时hH]*[揽攬][收率]+[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
        if m48:
            check.pickup_24h_raw = m48.group(0) + " (48h揽收率，非24h揽收率)"
            check.pickup_24h = "UNKNOWN"
            check.pickup_24h_et = "unavailable"
        else:
            check.pickup_24h_raw = None
            check.pickup_24h_et = "unavailable"

    # 6. 店铺回头率 >60%
    m = re.search(r"店铺回头率[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
    if m:
        check.shop_repurchase_raw = m.group(0)
        val = float(m.group(1))
        check.shop_repurchase = "PASS" if val > 60 else "REJECT"
        check.shop_repurchase_et = "exact"
    else:
        m = re.search(r"回头率[^\d]*(\d+(?:\.\d+)?)\s*%", body_text)
        if m:
            check.shop_repurchase_raw = m.group(0) + " (无店铺前缀，可能非店铺回头率)"
            val = float(m.group(1))
            check.shop_repurchase = "PASS" if val > 60 else "REJECT"
            check.shop_repurchase_et = "estimated"
        else:
            check.shop_repurchase_raw = None
            check.shop_repurchase_et = "unavailable"

    return check


def parse_search_page_merchants(body_text: str) -> list:
    """扫描 1688 搜索结果页 innerText，按商品卡片提取商家类型徽章。

    在搜索结果页，每个商品卡片左下角会显示商家类型徽章:
      - 红色徽章: 源头旗舰 / 实力商家 / 实力旗舰
      - 紫色徽章: 超级工厂
    用户人工搜索逻辑: 在搜索页快速扫描徽章，筛选带白名单徽章的商品再进入详情。

    返回: [{"merchant_type": "源头旗舰", "merchant_name": "...", "context": "..."}]
    """
    results = []
    # 匹配"源头旗舰 / 实力商家 / 超级工厂 / 实力旗舰"等徽章关键字
    # 1688 搜索页卡片格式: 商品标题 ... ¥价格 ... 回头率 XX% 商家名 [徽章] 旺旺在线
    for kw in MERCHANT_TYPE_WHITELIST:
        # 找出所有出现该徽章的位置
        idx = 0
        while True:
            pos = body_text.find(kw, idx)
            if pos == -1:
                break
            # 提取上下文
            ctx_start = max(0, pos - 80)
            ctx_end = min(len(body_text), pos + 120)
            context = body_text[ctx_start:ctx_end].replace("\n", " ").strip()
            results.append({
                "merchant_type": kw,
                "context": context,
                "position": pos
            })
            idx = pos + len(kw)
    return results


def build_search_url(keyword: str) -> str:
    return f"https://s.1688.com/selloffer/offer_search.htm?keywords={quote_plus(keyword)}&charset=UTF-8"


def build_product_url(offer_id: str) -> str:
    return f"https://detail.1688.com/offer/{offer_id}.html"


def save_result(result: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
