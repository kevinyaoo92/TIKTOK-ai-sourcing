# -*- coding: utf-8 -*-
"""1688 货源查找器：从关键词搜索 → 筛选 → 返回 1-3 个优质货源。

设计原则（产品规则，不可绕过）：
- 先严后宽：严格 → 中等 → 宽松 → 最宽，逐级降级
- 找到 ≥1 个即返回（不强制 3 个）
- 最宽级为底线（理论上不会返回 0）
"""
import json
import re
from urllib.parse import quote
from pathlib import Path
from typing import Callable, Optional

import subprocess
import time
import socket

from playwright.sync_api import sync_playwright


CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_USER_DATA = r"D:\tiktok-ai-sourcing\.chrome_1688"


def _is_port_open(port: int = 9222, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except Exception:
        return False


def _ensure_chrome_debug():
    if _is_port_open():
        return True
    # 只杀 .chrome_1688 用户目录启动的 Chrome，不碰用户日常浏览器
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -like '*chrome_1688*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    time.sleep(2)
    try:
        subprocess.Popen([CHROME_PATH,
                          "--remote-debugging-port=9222",
                          f"--user-data-dir={CHROME_USER_DATA}"])
    except Exception as e:
        print(f"[FINDER] Chrome 启动失败: {e}", flush=True)
        return False
    for _ in range(15):
        time.sleep(1)
        if _is_port_open():
            print("[FINDER] Chrome 调试模式已自动启动", flush=True)
            return True
    print("[FINDER] Chrome 调试端口 15 秒内未就绪", flush=True)
    return False

# ========== 配置 ==========
MAX_RESULTS = 3
MAX_DETAIL = 40
CDP_ENDPOINT = "http://127.0.0.1:9222"

# SKU 阈值固定 10，任何降级都不放宽
SKU_MAX = 8
STOCK_MIN = 2000   # 任一 SKU 库存 < 2000 直接排除
MOQ_MAX = 1        # 起批量必须 = 1（一件代发）

LEVELS = [
    {"name": "严格", "rate": 60, "rate24": 60, "sku": SKU_MAX, "stock": 2000},
    {"name": "中等", "rate": 50, "rate24": 40, "sku": SKU_MAX, "stock": 2000},
    {"name": "宽松", "rate": 40, "rate24": 20, "sku": SKU_MAX, "stock": 1000},
    {"name": "最宽", "rate": 30, "rate24": 0,  "sku": SKU_MAX, "stock": 1000},
]

SHOP_IMG_SIGS = ["4815-2-tps-232-56", "1330-2-tps-264-64"]


def _has_biz_tag(shop_imgs):
    for img in shop_imgs:
        for sig in SHOP_IMG_SIGS:
            if sig in img:
                return True
    return False


def _match(c, d, level):
    """判定单个商品是否符合指定级别。任一不过返回 False, 并打印原因。"""
    _oid = (d.get("offer_id") or (c.get("offer_id") if isinstance(c, dict) else None) or "?")

    if c["rate"] >= 0 and c["rate"] <= level["rate"]:
        print(f"[MATCH][FAIL] {_oid} rate {c['rate']:.0f} <= {level['rate']}", flush=True)
        return False
    if d.get("rate24") is not None and d["rate24"] <= level["rate24"]:
        print(f"[MATCH][FAIL] {_oid} rate24 {d['rate24']:.0f} <= {level['rate24']}", flush=True)
        return False
    if d.get("sku_fetch_failed"):
        print(f"[MATCH][FAIL] {_oid} sku_fetch_failed", flush=True)
        return False

    dims = d.get("sku_dimensions") or {}
    for name, n in dims.items():
        if n > level["sku"]:
            print(f"[MATCH][FAIL] {_oid} SKU {name}={n} > {level['sku']}", flush=True)
            return False

    min_stock = d.get("min_stock")
    stock_min = level.get("stock", STOCK_MIN)
    if min_stock is not None and min_stock < stock_min:
        print(f"[MATCH][FAIL] {_oid} stock {min_stock} < {stock_min}", flush=True)
        return False

    moq = d.get("moq")
    if moq is not None and moq > MOQ_MAX:
        print(f"[MATCH][FAIL] {_oid} moq {moq} > {MOQ_MAX}", flush=True)
        return False

    return True


def _fetch_detail(context, offer_id):
    """打开详情页，抓标题 + 24h揽收率 + SKU维度 + 库存 + 起批量"""
    pc_url = f"https://detail.1688.com/offer/{offer_id}.html"
    detail = context.new_page()
    try:
        detail.goto(pc_url, wait_until="domcontentloaded", timeout=60000)
        detail.wait_for_timeout(5000)

        # ===== 检查重定向/空白 =====
        actual_url = detail.url or ""
        if "offer" not in actual_url:
            print(f"[FINDER][WARN] {offer_id} 重定向到 {actual_url[:80]}", flush=True)
            return {"rate24": None, "color_count": None, "min_stock": None,
                    "moq": None, "title": "", "sku_dimensions": {},
                    "error": f"redirected"}

        body_text = detail.evaluate("() => document.body.innerText || ''")
        if len(body_text) < 500:
            print(f"[FINDER][WARN] {offer_id} 页面空白 len={len(body_text)}", flush=True)
            return {"rate24": None, "color_count": None, "min_stock": None,
                    "moq": None, "title": "", "sku_dimensions": {},
                    "error": f"blank_{len(body_text)}"}

        # ===== 标题 =====
        page_title = (detail.title() or "").strip()
        page_title = re.sub(r"\s*[-—]\s*阿里巴巴.*$", "", page_title).strip()

        # ===== 24h 揽收率 =====
        m24 = re.search(r"24h揽收率\s*([\d.]+)\s*%", body_text)
        rate24 = float(m24.group(1)) if m24 else None

        # ===== SKU 维度（严格按页面从上到下顺序）=====
        # ===== SKU: 优先从 skuInfoMap 读（覆盖全部组合）=====
        sku_data = detail.evaluate("""() => {
            const get = (o, k) => (o && typeof o === 'object') ? o[k] : undefined;
            const model = get(get(get(get(get(get(window, 'context'), 'result'), 'data'), 'Root'), 'fields'), 'dataJson');
            const skuModel = model && model.skuModel;
            if (!skuModel) return { found: false };

            const props = skuModel.skuProps || [];
            const infoMap = skuModel.skuInfoMap || {};

            const dims = {};
            for (let i = 0; i < props.length; i++) {
                const p = props[i];
                const name = p.prop || ('dim' + i);
                const cnt = (p.value || []).length;
                if (cnt > 0) dims[name] = cnt;
            }

            const stocks = [];
            const items = [];
            const keys = Object.keys(infoMap);
            for (let i = 0; i < keys.length; i++) {
                const k = keys[i];
                const v = infoMap[k];
                const n = v && v.canBookCount;
                if (typeof n === 'number') {
                    stocks.push(n);
                    items.push({ combo: k, stock: n, specId: v.specId });
                }
            }
            return { found: true, dims: dims, stocks: stocks, items: items,
                     total_combos: keys.length };
        }""") or {}

        if sku_data.get("found"):
            sku_dimensions = sku_data.get("dims") or {}
            stock_list = sku_data.get("stocks") or []
            sku_items = sku_data.get("items") or []
            stock_el_count = len(stock_list)
            min_stock = min(stock_list) if stock_list else None
            sku_fetch_failed = not bool(sku_dimensions) and not bool(stock_list)
            print(f"[SKU] skuInfoMap 覆盖 {len(stock_list)} 组合, min_stock={min_stock}", flush=True)
        else:
            # ===== 兜底: 老的 DOM 抓法 =====
            print(f"[SKU][WARN] skuInfoMap 未找到, 回退到 DOM 抓取", flush=True)
            sku_dimensions = detail.evaluate("""() => {
                const result = {};
                function countItems(fi) {
                    let items = fi.querySelectorAll('button.sku-filter-button');
                    if (items.length > 0) return items.length;
                    items = fi.querySelectorAll('.expand-view-item');
                    if (items.length > 0) return items.length;
                    items = fi.querySelectorAll('.transverse-filter button');
                    if (items.length > 0) return items.length;
                    items = fi.querySelectorAll('[class*="sku-item"]');
                    return items.length;
                }
                document.querySelectorAll('.feature-item').forEach(fi => {
                    const h3 = fi.querySelector('h3');
                    if (!h3) return;
                    const label = (h3.textContent || '').trim();
                    if (!label || label.length > 20) return;
                    const n = countItems(fi);
                    if (n > 0 && !(label in result)) result[label] = n;
                });
                return result;
            }""") or {}

            stock_el_count = detail.evaluate("""() => {
                return document.querySelectorAll('[i18n="sku-stock"]').length;
            }""") or 0

            sku_fetch_failed = False
            if stock_el_count > 1 and len(sku_dimensions) == 0:
                sku_fetch_failed = True
            elif stock_el_count == 0 and len(sku_dimensions) == 0:
                sku_fetch_failed = True

            stock_list = detail.evaluate("""() => {
                const out = [];
                document.querySelectorAll('[i18n="sku-stock"]').forEach(el => {
                    const txt = (el.textContent || '').trim();
                    if (txt.includes('库存不足') || txt.includes('无货') || txt.includes('缺货') || txt.includes('售罄')) {
                        out.push(0);
                        return;
                    }
                    const m = txt.match(/([0-9]+)/);
                    if (m) out.push(parseInt(m[1], 10));
                    else out.push(0);
                });
                return out;
            }""") or []
            min_stock = min(stock_list) if stock_list else None
            sku_items = []

        color_count = sku_dimensions.get("颜色") or (max(sku_dimensions.values()) if sku_dimensions else None)

        # ===== "展开已售罄商品" → 直接判死 =====
        has_soldout_expand = "展开已售罄商品" in body_text
        if has_soldout_expand:
            min_stock = 0

        # ===== 起批量（多种触发词 + 多种单位）=====
        moq = None
        UNIT = r"[件箱个包袋套双条盒支张片瓶罐桶斤克升卷装桶袋提盒袋ml]"
        TRIGGER = r"(?:起批|混批|起订|起售|起发)"
        for line in body_text.split("\n"):
            line = line.strip()
            if len(line) > 80:
                continue
            if not any(k in line for k in ["起批", "混批", "起订", "起售", "起发"]):
                continue
            m = re.search(r"([0-9]+)\s*" + UNIT + r".{0,4}" + TRIGGER, line)
            if not m:
                m = re.search(TRIGGER + r"[^0-9]{0,6}([0-9]+)", line)
            if m:
                n = int(m.group(1))
                if 1 <= n < 100000:
                    moq = n
                    break
        # 兜底：≥N 模式
        if moq is None:
            m = re.search(r"[≥>=]{1,2}\s*([0-9]+)\s*" + UNIT, body_text)
            if m:
                moq = int(m.group(1))
        return {"rate24": rate24, "color_count": color_count,
                "sku_dimensions": sku_dimensions, "min_stock": min_stock,
                "stock_list": stock_list, "moq": moq,
                "has_soldout_expand": has_soldout_expand,
                "stock_el_count": stock_el_count,
                "sku_fetch_failed": sku_fetch_failed,
                "title": page_title}
    except Exception as e:
        return {"rate24": None, "color_count": None, "min_stock": None,
                "moq": None, "title": "", "sku_dimensions": {},
                "error": str(e)[:120]}
    finally:
        detail.close()




def _ai_filter_cards(cards, keyword):
    """AI 剔除明显不属于搜索词类目的卡片。加主商品判定 + 赠品剔除 + 兜底补足。不排序。"""
    import requests
    from app import config
    api_key = getattr(config, "DEEPSEEK_API_KEY", "") or ""
    if not api_key or not cards:
        print(f"[FINDER][AI] 无 key 或无卡片, 跳过", flush=True)
        return cards

    sys_prompt = """你是1688商品类目判定助手。用户给搜索词和一批商品卡片标题，你剔除明显不属于搜索词类目的卡片。

【主商品判定】
标题里最后一个商品名词是主商品，前面的都是描述/修饰词。
例: 新款耐高温防滑隔热加厚迷你手持熨烫板挂烫防烫手套 -> 主商品是防烫手套，不是烫衣板
例: 女士连衣裙【送发夹】 -> 主商品是连衣裙，发夹是赠品

【赠品剔除规则】
【】()[]里的词、买X送Y、赠、附赠、送 里的词一律不算主商品。

【剔除条件】
- 搜索词指向的类目与标题主商品不一致 -> 剔除
- 搜索词是女士/女装/女款，标题明确含童装词(儿童/宝宝/童装/女童/男童/婴幼儿/中大童/小童/大童) -> 剔除
- 搜索词是男士/男装/男款，标题明确含童装词 -> 剔除
- 搜索词是女士，标题明确是纯男装(不含女士/女/男女/中性/情侣/通款) -> 剔除
- 搜索词是男士，标题明确是纯女装(不含男士/男/男女/中性/情侣/通款) -> 剔除

【保留反例】
- 搜 女士连衣裙，标题 女士连衣裙【送发夹】 -> 保留(主商品匹配)
- 搜 茶具，标题 茶具套装 送茶叶罐 -> 保留
- 搜 手机壳，标题 手机壳 硅胶保护套 -> 保留

【剔除反例】
- 搜 烫衣板，标题 迷你手持熨烫板挂烫 防烫手套 -> 剔除(主商品是手套)
- 搜 手机壳，标题 数据线保护套 手机壳颜色随机 -> 剔除(主商品是保护套)

【关键约束】
- 拿不准的一律保留
- 宁可少剔除，不可多剔除
- 只返回 JSON，不要解释
格式: {"rejected": [{"id": "xxx", "reason": "简短理由"}]}
若无明显错的返回 {"rejected": []}"""



    lines = [f"搜索词: {keyword}", "", f"共 {len(cards)} 张卡片:"]
    for c in cards:
        lines.append(f"- id={c.get('offer_id','')} 标题={(c.get('title') or c.get('text','') or '')[:80]}")
    user_prompt = "\n".join(lines)

    try:
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            },
            timeout=25
        )
        if r.status_code != 200:
            print(f"[FINDER][AI] HTTP {r.status_code}, 跳过", flush=True)
            return cards
        content = r.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        rejected = parsed.get("rejected", []) or []
        rejected_ids = set()
        for x in rejected:
            if isinstance(x, dict):
                rid = str(x.get("id", "")).strip()
                if rid: rejected_ids.add(rid)

        out = [c for c in cards if str(c.get("offer_id", "")) not in rejected_ids]
        rejected_cards = [c for c in cards if str(c.get("offer_id", "")) in rejected_ids]

        fallback_used = False

        for x in rejected[:5]:
            if isinstance(x, dict):
                rid = x.get("id", "")
                t = next((c.get("title") or c.get("text", "")[:50] for c in cards if str(c.get("offer_id")) == str(rid)), "?")
                print(f"[FINDER][AI][剔除] {rid} | {t[:50]} | {x.get('reason', '')}", flush=True)

        fb = " [兜底补足]" if fallback_used else ""
        print(f"[FINDER][AI] 剔除 {len(rejected_ids)} 张, 保留 {len(out)} / {len(cards)}{fb}", flush=True)
        return out
    except Exception as e:
        print(f"[FINDER][AI] 异常: {str(e)[:100]}, 跳过", flush=True)
        return cards


def _title_matches_keyword(title, keyword):
    """卡片/详情层性别一致性过滤。搜索词是女装->标题必须含女装词; 男装同理。
    含童装词、男女同款、情侣、中性的, 女装/男装搜索时一律 pass。
    """
    if not title:
        return False
    t = title.lower()
    k = keyword.lower()

    female_words = ["女士", "女装", "女款", "女式", "女性", "女", "woman", "women", "female", "ladies", "lady"]
    male_words = ["男士", "男装", "男款", "男式", "男性", "男", "man", "men", "male"]
    kids_words = ["儿童", "童装", "宝宝", "婴幼", "婴童", "男童", "女童", "幼儿", "婴儿", "kids", "baby", "children", "中大童", "小童", "大童"]
    unisex_words = ["男女同款", "男女通用", "情侣", "中性", "unisex", "for couple"]

    kw_has_kids = any(w in k for w in kids_words)
    kw_has_female = (not kw_has_kids) and any(w in k for w in female_words)
    kw_has_male = (not kw_has_kids) and any(w in k for w in male_words)

    t_has_kids = any(w in t for w in kids_words)
    t_has_female = (not t_has_kids) and any(w in t for w in female_words)
    t_has_male = (not t_has_kids) and any(w in t for w in male_words)
    t_has_unisex = any(w in t for w in unisex_words)

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


def find_suppliers(keyword: str, on_progress: Optional[Callable] = None) -> dict:
    print(f"[FINDER] 收到搜索词: {keyword!r}", flush=True)

    if not _ensure_chrome_debug():
        return {"status": "failed", "error": "Chrome 调试模式未就绪", "results": []}

    def report(pct, phase, extra=None):
        if on_progress:
            try:
                on_progress(pct, phase, extra or {})
            except Exception:
                pass

    EXTRACT_CARDS_JS = """() => {
        const els = document.querySelectorAll('[data-offer-grid-cell="true"]');
        return Array.from(els).map((el, i) => {
            const offer_id = el.getAttribute('data-offer-expose-id') || '';
            const linkEl = el.querySelector('a');
            const link = linkEl ? linkEl.href : '';
            const mainImg = el.querySelector('img');
            const imgSrc = mainImg ? mainImg.src : '';
            const shopRow = el.querySelector('[class*="shopRow"]');
            const shopImgs = [];
            let shopName = '';
            if (shopRow) {
                shopRow.querySelectorAll('img').forEach(img => {
                    const r = img.getBoundingClientRect();
                    if (r.width > 30 && r.width < 120 && r.height > 10 && r.height < 30) {
                        shopImgs.push(img.src);
                    }
                });
                shopName = shopRow.innerText.replace(/\\n+/g, ' ').trim().slice(0, 60);
            }
            let price = '';
            const p1el = el.querySelector('[class*="price-item"]');
            if (p1el) price = (p1el.textContent || '').trim();
            if (!price) {
                const p2el = el.querySelector('[class*="price"]');
                if (p2el) price = (p2el.textContent || '').trim();
            }
            return { idx: i, offer_id, link, img: imgSrc, price,
                     shop_name: shopName, shop_imgs: shopImgs,
                     text: (el.innerText || '').replace(/\\n+/g, ' | ') };
        });
    }"""

    def fetch_page_cards(page, kw, page_num):
        url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={quote(kw.encode('gbk'))}"
        if page_num > 1:
            url += f"&beginPage={page_num}"
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        return page.evaluate(EXTRACT_CARDS_JS) or []

    def filter_biz(cards):
        biz = [c for c in cards if _has_biz_tag(c["shop_imgs"])]
        for c in biz:
            m = re.search(r"\u56de\u5934\u7387\s*(\d+(?:\.\d+)?)\s*%", c.get("text") or "")
            c["rate"] = float(m.group(1)) if m else -1
        # 卡片层性别过滤: 标题不合的直接剔除, 不打开详情
        before = len(biz)
        biz = [c for c in biz if _title_matches_keyword(c.get("text", "") or c.get("title", ""), keyword)]
        print(f"[FINDER][CARD] 卡片层性别过滤 {before} -> {len(biz)}", flush=True)
        biz.sort(key=lambda x: -x["rate"])
        return biz

    report(5, "正在搜索 1688 商品...")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        try:
            cards = fetch_page_cards(page, keyword, 1)
        except Exception as e:
            page.close()
            return {"status": "failed", "error": f"搜索页打开失败: {str(e)[:100]}", "results": []}

        if not cards:
            page.close()
            return {"status": "empty", "level": None, "keyword": keyword,
                    "found": 0, "results": [], "checked_details": 0,
                    "note": "1688 搜索无结果"}

        report(10, f"找到 {len(cards)} 个候选商品，正在初筛...")
        biz_cards = filter_biz(cards)
        biz_cards = _ai_filter_cards(biz_cards, keyword)
        report(15, f"初步筛选出 {len(biz_cards)} 个商家，开始逐个检查...")

        cache = {}
        detail_count = 0
        final = []
        matched_level = None
        page2_biz = None

        def match_loop(cards_list, lv):
            nonlocal detail_count
            results = []
            min_rate = lv["rate"]
            for c in cards_list:
                if len(results) >= MAX_RESULTS:
                    break
                # 档位回头率初筛（不满足不进详情页）
                card_rate = c.get("rate", -1)

                if card_rate >= 0 and card_rate <= min_rate:

                    print(f"[FINDER][SKIP] {c.get('offer_id')} rate={card_rate} <= {min_rate}", flush=True)

                    continue
                oid = c["offer_id"]
                if not oid:
                    continue
                if oid not in cache:
                    if detail_count >= MAX_DETAIL:
                        continue
                    pct = 15 + int(80 * min(detail_count / MAX_DETAIL, 1.0))
                    report(pct, f"已查询 {detail_count + 1} 个商品...", {
                        "checked": detail_count + 1,
                        "current_level": lv["name"]
                    })
                    cache[oid] = _fetch_detail(context, oid)
                    detail_count += 1
                d = cache[oid]
                if _match(c, d, lv):
                    _title = (d.get("title") or c.get("title") or "").strip()
                    if not _title_matches_keyword(_title, keyword):
                        print(f"[FINDER][SKIP] {oid} 性别不匹配: {_title[:50]}", flush=True)
                        continue
                    results.append({
                        "offer_id": oid,
                        "title": d.get("title") or c.get("title") or "",
                        "img": c["img"],
                        "price": c["price"],
                        "shop_name": c["shop_name"],
                        "rate": c["rate"],
                        "rate24": d["rate24"],
                        "color_count": d["color_count"],
                        "sku_dimensions": d.get("sku_dimensions") or {},
                        "min_stock": d.get("min_stock"),
                        "moq": d.get("moq"),
                        "link": f"https://detail.1688.com/offer/{oid}.html"
                    })
            return results

        # ============ 分档 + 分页 + 缓存复用 ============
        # 规则：
        #   每档：第1页整页遍历，结果≥1 就输出；=0 则翻第2页，≥1 输出，=0 降级下一档
        #   每档独立从第1页开始，但详情页数据全局缓存、跨档复用
        page2_biz = None  # 懒加载

        def _ensure_page2():
            nonlocal page2_biz
            if page2_biz is not None:
                return page2_biz
            try:
                report(50, "第1页未命中，翻第2页...")
                p2_cards = fetch_page_cards(page, keyword, 2)
                page2_biz = filter_biz(p2_cards)
                print(f"[FINDER] 第2页 {len(p2_cards)} 候选 / {len(page2_biz)} 有商家图", flush=True)
            except Exception as e:
                print(f"[FINDER] 第2页失败: {e}", flush=True)
                page2_biz = []
            return page2_biz

        for lv in LEVELS:
            print(f"[FINDER] === {lv['name']} 档 ===", flush=True)
            tier_results = []

            # 第1页
            r1 = match_loop(biz_cards, lv)
            tier_results.extend(r1)

            # 第1页 0 个 → 翻第2页
            if not tier_results:
                p2 = _ensure_page2()
                if p2:
                    r2 = match_loop(p2, lv)
                    tier_results.extend(r2)

            if tier_results:
                final = tier_results
                matched_level = lv["name"]
                print(f"[FINDER] {lv['name']} 档命中 {len(final)} 个", flush=True)
                break
            print(f"[FINDER] {lv['name']} 档 0 命中，降级下一档", flush=True)

        page.close()

    status = "success" if final else "empty"
    report(100, "匹配完成", {"status": status})

    return {
        "status": status,
        "level": matched_level,
        "keyword": keyword,
        "found": len(final),
        "max_results": MAX_RESULTS,
        "checked_details": detail_count,
        "results": final
    }
