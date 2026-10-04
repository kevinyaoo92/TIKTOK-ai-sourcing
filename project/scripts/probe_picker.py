# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright

PROFILE = r"D:\tiktok-ai-sourcing\.run\profile"
BASE = "https://seller.tiktokshopglobalselling.com/"
KW = "https://seller.tiktokshopglobalselling.com/compass/search-analytics/keyword-rank?shop_region=TH"

def norm(s):
    return " ".join((s or "").split())

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE, channel="chrome", headless=False,
        viewport={"width": 1680, "height": 950}, locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        ignore_default_args=["--enable-automation"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    def sg(url, s):
        try:
            page.goto(url, wait_until="commit", timeout=60000)
        except Exception as e:
            if "interrupted" not in str(e):
                raise
        time.sleep(s)
    sg(BASE, 5)
    sg(KW, 8)
    tab = page.get_by_role("tab", name="热门搜索关键词", exact=True)
    deadline = time.time() + 60
    while time.time() < deadline and not (tab.count() > 0 and tab.first.is_visible()):
        time.sleep(1.5)
    tab.first.click()
    time.sleep(4)
    out = {}
    picker = page.locator("div.theme-arco-picker-range")
    out["picker_count"] = picker.count()
    out["picker_text_before"] = norm(picker.first.inner_text()) if picker.count() else None
    picker.first.click()
    time.sleep(2.5)
    seg = page.get_by_text("月", exact=True)
    out["seg_month_count"] = seg.count()
    vis = []
    for i in range(seg.count()):
        try:
            if seg.nth(i).is_visible():
                vis.append(i)
        except Exception:
            pass
    out["seg_month_visible_idx"] = vis
    if vis:
        seg.nth(vis[0]).click()
        time.sleep(3)
    out["picker_text_after"] = norm(picker.first.inner_text()) if picker.count() else None
    months = ["一月","二月","三月","四月","五月","六月","七月","八月","九月","十月","十一月","十二月"]
    for m in months:
        loc = page.get_by_text(m, exact=True)
        out[m] = loc.count()
    # visible month cells
    out["visible_months"] = []
    for m in months:
        loc = page.get_by_text(m, exact=True)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    out["visible_months"].append(m)
                    break
            except Exception:
                pass
    # any arco popup visible?
    pop = page.locator(".theme-arco-picker-dropdown, .arco-picker-dropdown")
    out["popup_count"] = pop.count()
    try:
        out["popup_text"] = norm(pop.first.inner_text())[:300] if pop.count() else None
    except Exception:
        out["popup_text"] = None
    print(json.dumps(out, ensure_ascii=False))
    ctx.close()