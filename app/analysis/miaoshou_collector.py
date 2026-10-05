# -*- coding: utf-8 -*-
# [FROZEN 2026-10-05] Internal Validation / Reference Implementation.
# 不是 Frontharbor 最终生产执行架构。
# 保留，冻结，不继续扩展。
# 正式执行层方向：Chrome Extension + 用户自己的 Chrome。
# 详见 _state/ARCHITECTURE_FREEZE.md。
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.analysis.supplier_finder import _ensure_chrome_debug, CDP_ENDPOINT

MIAOSHOU_HOME = "https://erp.91miaoshou.com"
MIAOSHOU_COLLECT_URL = "https://erp.91miaoshou.com/common_collect_box/index?fetchType=linkCopy"

def open_login_page() -> dict:
    if not _ensure_chrome_debug():
        return {"status": "failed", "message": "Chrome 未就绪"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
            context = browser.contexts[0]
            page = context.new_page()
            page.goto(MIAOSHOU_HOME, wait_until="domcontentloaded", timeout=60000)
            return {"status": "success", "message": "已打开妙手登录页"}
    except Exception as e:
        return {"status": "failed", "message": str(e)[:200]}

def _is_logged_in(page) -> bool:
    try:
        return page.query_selector('textarea.jx-textarea__inner') is not None
    except Exception:
        return False

def check_login() -> dict:
    """静默检查妙手登录态：只读 Cookie，不打开任何页面。"""
    if not _ensure_chrome_debug():
        return {"logged_in": False, "error": "Chrome 未就绪"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
            context = browser.contexts[0]
            for c in context.cookies():
                if c.get("name") == "mserp" and "miaoshou.com" in (c.get("domain") or "").lower():
                    return {"logged_in": True}
            return {"logged_in": False}
    except Exception as e:
        return {"logged_in": False, "error": str(e)[:200]}


def collect_to_miaoshou(offer_id: str) -> dict:
    if not _ensure_chrome_debug():
        return {"status": "failed", "message": "Chrome 未就绪"}
    url = f"https://detail.1688.com/offer/{offer_id}.html"
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()
        try:
            page.goto(MIAOSHOU_COLLECT_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)

            ta = page.query_selector('textarea.jx-textarea__inner')
            if not ta:
                page.close()
                return {"status": "need_login", "message": "请先在浏览器中登录妙手ERP"}

            ta.click()
            ta.fill(url)
            page.wait_for_timeout(1000)

            # 等按钮启用
            btn_ready = False
            for _ in range(10):
                page.wait_for_timeout(500)
                state = page.evaluate("""() => {
                    for (const b of document.querySelectorAll('button')) {
                        const t = (b.innerText || '').trim();
                        if (t.includes('采集并自动认领')) {
                            return {disabled: b.disabled, aria: b.getAttribute('aria-disabled')};
                        }
                    }
                    return null;
                }""")
                if state and not state['disabled'] and state['aria'] != 'true':
                    btn_ready = True
                    break

            if not btn_ready:
                page.close()
                return {"status": "failed", "message": "采集按钮未启用，可能链接格式不对"}

            page.evaluate("""() => {
                for (const b of document.querySelectorAll('button')) {
                    const t = (b.innerText || '').trim();
                    if (t.includes('采集并自动认领')) { b.click(); return true; }
                }
                return false;
            }""")

            for _ in range(20):
                page.wait_for_timeout(1000)
                body = page.inner_text('body')
                if '成功' in body or '已采集' in body or '采集成功' in body or '提交成功' in body:
                    page.close()
                    return {"status": "success", "message": "商品已加入妙手公用采集箱"}
                if '失败' in body or '格式错误' in body:
                    page.close()
                    return {"status": "failed", "message": "采集失败，请检查链接"}

            page.close()
            return {"status": "success", "message": "已提交采集请求（未捕获成功提示，请到妙手后台确认）"}
        except Exception as e:
            try:
                page.close()
            except Exception:
                pass
            return {"status": "failed", "message": str(e)[:200]}