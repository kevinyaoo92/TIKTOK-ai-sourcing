# -*- coding: utf-8 -*-
"""TikTok 关键词榜单页面 TOP20 采集器（Python + Playwright）

直接操作 TikTok Seller Center 页面排序功能，抓取页面排序后的 TOP20。
不下载 Excel、不调用 AI、不依赖 TRAE。

第一阶段：TH - 家居用品 - 家居收纳 - 月度 - 热门搜索关键词
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page

# === 路径配置 ===
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # scripts/collect/xx.py -> 项目根
PROFILE = str(PROJECT_ROOT / "project" / ".run" / "profile")

BASE_URL = "https://seller.tiktokshopglobalselling.com/"
KEYWORD_URL = "https://seller.tiktokshopglobalselling.com/compass/search-analytics/keyword-rank?shop_region=TH"

# === 采集目标 ===
COUNTRY = "TH"
LEVEL_1 = "家居用品"
LEVEL_2 = "家居收纳"
RANKING_TAB = "热门搜索关键词"
PERIOD = "month"  # month / week

# 输出目录
OUTPUT_BASE = PROJECT_ROOT / "data" / "TH" / "家居用品" / "家居收纳"

# 六个目标字段: (TXT显示名, 页面表头文本候选)
# 注意: 页面实际表头为「商品点击指数」「SKU销量指数」，保留旧名称作为兼容候选
TARGET_FIELDS = [
    ("搜索量",       ["搜索量"]),
    ("商品点击数",   ["商品点击指数", "商品点击数"]),
    ("SKU销售指数",  ["SKU销量指数", "SKU 销量指数", "SKU销售指数", "SKU 销售指数"]),
    ("在售商品",     ["在售商品"]),
    ("CTR指数",      ["CTR指数", "CTR 指数"]),
    ("CTOR评分",     ["CTOR评分", "CTOR 评分"]),
]

CN_MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
             "七月", "八月", "九月", "十月", "十一月", "十二月"]


# ===========================================================================
# 通用辅助函数（复用自 tiktok_keyword_export.py，保持一致性）
# ===========================================================================

def safe_goto(page: Page, url: str, sleep_s: float = 5):
    """导航到 URL，容忍 interrupted 异常和页面关闭。

    如果页面已关闭，尝试获取新页面重试。
    """
    try:
        page.goto(url, wait_until="commit", timeout=60000)
    except Exception as e:
        msg = str(e)
        if "interrupted" in msg:
            pass  # 正常，commit 后继续渲染被中断
        elif "Target page" in msg or "closed" in msg:
            # 页面关闭了，等待并尝试重新获取
            time.sleep(2)
            try:
                page.goto(url, wait_until="commit", timeout=60000)
            except Exception:
                pass
        else:
            raise
    time.sleep(sleep_s)


def wait_visible(page, locator, timeout_s: int = 15) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def dismiss_chat_popup(page: Page):
    try:
        ok = page.get_by_role("button", name="好的", exact=True)
        if ok.count() > 0 and ok.first.is_visible():
            ok.first.click(timeout=3000)
            time.sleep(1)
    except Exception:
        pass


def click_stable(page: Page, loc, tries: int = 3) -> bool:
    for _ in range(tries):
        try:
            loc.click(timeout=8000)
            return True
        except Exception:
            time.sleep(1.5)
    try:
        bb = loc.bounding_box()
        if bb:
            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            return True
    except Exception:
        pass
    return False


def normalize_text(text: str) -> str:
    """去除空格，用于表头匹配。"""
    return (text or "").replace(" ", "").replace("\u3000", "").strip()


# ===========================================================================
# 登录检查
# ===========================================================================

def check_login(page: Page) -> bool:
    url = page.url or ""
    if "account/login" in url or "/login" in url:
        return False
    return True


# ===========================================================================
# 类目级联选择（复用已有逻辑，校验放宽以接受"家居收纳用品"）
# ===========================================================================

def pick_cascader_item(page: Page, level_1: str, level_2: Optional[str]) -> bool:
    """选择类目。返回 True 成功。

    TikTok 页面使用 core-cascader-* 前缀（非 theme-arco-cascader-*）。
    """
    # 定位类目级联视图
    view = page.locator("[class*='cascader-view']")
    if not wait_visible(page, view, 10):
        view = page.get_by_role("combobox")
        if not wait_visible(page, view, 10):
            print("[ERROR] 找不到类目级联")
            return False

    view.first.click()
    time.sleep(3)

    # 一级类目
    l1_item = page.locator("[class*='cascader-list-item']", has_text=level_1)
    if not wait_visible(page, l1_item, 15):
        print(f"[ERROR] 找不到一级类目: {level_1}")
        return False
    clicked = False
    for i in range(l1_item.count()):
        try:
            it = l1_item.nth(i)
            if it.is_visible():
                it.hover()
                time.sleep(1.2)
                it.click()
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        print(f"[ERROR] 点击一级类目失败: {level_1}")
        return False
    time.sleep(3)

    # 二级类目
    if level_2:
        l2_clicked = False
        for _retry in range(3):
            l2_item = page.locator("[class*='cascader-list-item']", has_text=level_2)
            if wait_visible(page, l2_item, 8):
                for i in range(l2_item.count()):
                    try:
                        it = l2_item.nth(i)
                        if it.is_visible():
                            it.hover()
                            time.sleep(1.0)
                            it.click()
                            l2_clicked = True
                            break
                    except Exception:
                        pass
                if l2_clicked:
                    break
            # 重新 hover 一级以触发二级列
            try:
                l1_hover = page.locator("[class*='cascader-list-item']", has_text=level_1)
                if l1_hover.count() > 0:
                    l1_hover.first.hover()
                    time.sleep(2)
            except Exception:
                pass
        if not l2_clicked:
            print(f"[ERROR] 找不到/点击二级类目失败: {level_2}")
            return False
        time.sleep(3)

    # 校验：接受"实际值包含期望值"或"期望值包含实际值"
    vinp = page.locator("input[class*='cascader-view-input'], input[class*='cascader']")
    if vinp.count() and wait_visible(page, vinp, 5):
        actual = (vinp.first.input_value() or "").strip()
        expected = level_2 or level_1
        if expected in actual or actual in expected:
            print(f"[OK] 类目选择成功，页面实际显示: {actual}")
            return True
        else:
            print(f"[ERROR] 类目校验失败: 实际={actual}, 期望含={expected}")
            return False
    return True


# ===========================================================================
# 榜单 Tab 切换
# ===========================================================================

def switch_ranking_tab(page: Page, tab_name: str) -> bool:
    """切换榜单类型 tab（点击后校验，不成功自动换方法）。"""

    def _dump():
        return page.evaluate("""() => {
            const tabs = document.querySelectorAll('[role="tab"]');
            return Array.from(tabs).map(t => ({
                text: (t.textContent || '').trim(),
                selected: t.getAttribute('aria-selected') === 'true'
                          || (t.className || '').includes('selected')
            }));
        }""")

    def _wait_selected(timeout_ms=8000):
        try:
            page.wait_for_function(
                """(name) => {
                    const tabs = document.querySelectorAll('[role="tab"]');
                    for (const t of tabs) {
                        const text = (t.textContent || '').trim();
                        if (text === name) {
                            return t.getAttribute('aria-selected') === 'true'
                                || (t.className || '').includes('selected');
                        }
                    }
                    return false;
                }""",
                arg=tab_name,
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    print(f"  [DEBUG] 切换前 tab: {_dump()}")
    print(f"  [DEBUG] 目标: {tab_name!r}")

    # 方法1: role=tab
    try:
        loc = page.get_by_role("tab", name=tab_name, exact=True).first
        if loc.count() > 0 and loc.is_visible(timeout=5000):
            try:
                loc.click(timeout=5000)
            except Exception:
                loc.click(force=True, timeout=3000)
            if _wait_selected():
                dismiss_chat_popup(page)
                print(f"[OK] 已切换到: {tab_name} (方法1+校验)")
                return True
            print("  [WARN] 方法1点击后未生效，换方法2")
    except Exception as e:
        print(f"  [WARN] 方法1异常: {e}")

    # 方法2: has_text
    try:
        loc2 = page.locator("[role='tab']", has_text=tab_name).first
        if loc2.count() > 0 and loc2.is_visible(timeout=5000):
            try:
                loc2.click(timeout=5000)
            except Exception:
                loc2.click(force=True, timeout=3000)
            if _wait_selected():
                dismiss_chat_popup(page)
                print(f"[OK] 已切换到: {tab_name} (方法2+校验)")
                return True
            print("  [WARN] 方法2点击后未生效，换方法3")
    except Exception as e:
        print(f"  [WARN] 方法2异常: {e}")

    # 方法3: JS 点击（内层 + 外层都点）
    try:
        clicked = page.evaluate(
            """(name) => {
                const tabs = document.querySelectorAll('[role="tab"]');
                for (const t of tabs) {
                    const text = (t.textContent || '').trim();
                    if (text === name) {
                        const inner = t.querySelector('*');
                        if (inner) inner.click();
                        t.click();
                        return true;
                    }
                }
                return false;
            }""",
            tab_name,
        )
        if clicked and _wait_selected():
            dismiss_chat_popup(page)
            print(f"[OK] 已切换到: {tab_name} (方法3+校验)")
            return True
    except Exception as e:
        print(f"  [WARN] 方法3异常: {e}")

    print(f"[ERROR] 切换 tab 失败: {tab_name}")
    print(f"  [DEBUG] 当前页面 tab: {_dump()}")
    return False


def get_current_tab(page: Page) -> str:
    """读取当前选中的榜单 tab 名称。"""
    return page.evaluate("""
        () => {
            const tabs = document.querySelectorAll('[role="tab"]');
            for (const t of tabs) {
                if (t.getAttribute('aria-selected') === 'true' ||
                    (t.className || '').includes('selected')) {
                    return (t.textContent || '').trim();
                }
            }
            return '';
        }
    """)


def get_current_category(page: Page) -> str:
    """读取当前类目文本（兼容 core-cascader / theme-arco-cascader）。"""
    result = page.evaluate("""() => {
        const v = document.querySelector('.core-cascader-view-value');
        if (v) {
            const t = (v.textContent || '').trim();
            if (t) return t;
        }
        const pt = document.querySelector('.core-cascader-view-selector .pulse-overflow-text');
        if (pt) {
            const t = (pt.textContent || '').trim();
            if (t) return t;
        }
        const inp = document.querySelector("input[class*='cascader-view-input'], input[class*='cascader']");
        if (inp) return (inp.value || '').trim();
        return '';
    }""")
    return result or ""


# ===========================================================================
# 日期选择器：月度模式
# ===========================================================================

def switch_granularity(page: Page, label: str) -> bool:
    """点击粒度按钮（日/周/月）。"""
    seg = page.get_by_text(label, exact=True)
    for i in range(seg.count()):
        try:
            if seg.nth(i).is_visible():
                seg.nth(i).click(timeout=5000)
                time.sleep(2.5)
                return True
        except Exception:
            pass
    return False


def _ensure_picker_in_month_view(page: Page) -> bool:
    """确保日期选择器打开且处于月度视图。"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass

    # 尝试多种选择器定位日期选择器
    picker = page.locator("[class*='picker-range']")
    if not wait_visible(page, picker, 10):
        picker = page.locator("[class*='date-picker-range'], [class*='DatePicker']")
    if not wait_visible(page, picker, 10):
        # 调试：打印包含"picker"或"日期"或"周"或"月"的元素
        debug_info = page.evaluate("""
            () => {
                const result = [];
                document.querySelectorAll('[class*="picker"]').forEach(el => {
                    result.push({tag: el.tagName, cls: (el.className || '').substring(0, 80), text: (el.textContent || '').trim().substring(0, 50)});
                });
                // 也找包含"周"或"月:"标签的元素
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.children.length < 3) {
                        const t = (el.textContent || '').trim();
                        if ((t === '月:' || t === '周:' || t === '月' || t === '周') && t.length < 5) {
                            result.push({tag: el.tagName, cls: (el.className || '').substring(0, 80), text: t, parent: el.parentElement?.tagName + '.' + (el.parentElement?.className || '').substring(0, 60)});
                        }
                    }
                }
                return result.slice(0, 30);
            }
        """)
        print(f"  [DEBUG] picker/日期相关元素: {debug_info}")
        return False

    try:
        picker.first.click(timeout=8000)
    except Exception:
        return False
    time.sleep(2.5)

    # 切"月"粒度
    if not switch_granularity(page, "月"):
        # 调试：打印弹层中的粒度按钮
        gran_debug = page.evaluate("""
            () => {
                const result = [];
                document.querySelectorAll('[class*="mode-item"], [class*="granularity"], [class*="range-mode"]').forEach(el => {
                    result.push({cls: (el.className || '').substring(0, 80), text: (el.textContent || '').trim().substring(0, 30)});
                });
                // 也查找包含日/周/月文本的可点击元素
                document.querySelectorAll('div, span, button').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (['日','周','月'].includes(t) && el.children.length === 0) {
                        result.push({tag: el.tagName, cls: (el.className || '').substring(0, 80), text: t});
                    }
                });
                return result.slice(0, 30);
            }
        """)
        print(f"  [DEBUG] 粒度按钮: {gran_debug}")
        return False
    time.sleep(2.5)

    return read_available_months(page) != []


def read_available_months(page: Page) -> list:
    """读取月度视图下的可用月份，按从新到旧排序。"""
    cells = page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('[class*="picker-cell"]').forEach((c) => {
            const t = (c.innerText || '').trim();
            const cls = c.className || '';
            if (['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'].includes(t)
                && cls.includes('in-view') && !cls.includes('disabled')) {
              out.push(t);
            }
          });
          return out;
        }"""
    )
    seen = set()
    avail = []
    for m in cells:
        if m not in seen:
            seen.add(m)
            avail.append(m)
    avail.reverse()
    return avail


def click_month_cell(page: Page, cn_name: str) -> bool:
    """点击月份视图中的某个月份 cell。"""
    target = page.locator('[class*="picker-cell"]', has_text=cn_name).first
    return click_stable(page, target)


def read_date_range(page: Page) -> tuple:
    """从页面读取实际日期范围，返回 (start, end) 或 (None, None)。"""
    result = page.evaluate("""
        () => {
            const inputs = Array.from(document.querySelectorAll('input[readonly]'));
            const dateValues = inputs
                .map(i => i.value)
                .filter(v => v && v.match(/\\d{4}[\\/\\-]\\d{2}[\\/\\-]\\d{2}/));
            return dateValues;
        }
    """)
    if len(result) >= 2:
        start = result[0].replace("/", "-")
        end = result[1].replace("/", "-")
        return (start, end)
    return (None, None)


# ===========================================================================
# 周度视图支持
# ===========================================================================

def _ensure_picker_in_week_view(page: Page) -> bool:
    """确保日期选择器打开且处于周度视图。"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass

    picker = page.locator("[class*='picker-range']")
    if not wait_visible(page, picker, 10):
        picker = page.locator("[class*='date-picker-range'], [class*='DatePicker']")
    if not wait_visible(page, picker, 10):
        print("[ERROR] 找不到日期选择器")
        return False

    try:
        picker.first.click(timeout=8000)
    except Exception:
        return False
    time.sleep(2.5)

    # 切"周"粒度
    if not switch_granularity(page, "周"):
        print("[ERROR] 无法切换到周粒度")
        return False
    time.sleep(2.5)

    # 校验：周视图下应至少有一个非 disabled 的日期 cell
    weeks = read_available_week_cells(page)
    return bool(weeks)


def read_available_week_cells(page: Page) -> list:
    """读取周视图下所有非 disabled 的日期 cell，按 DOM 顺序返回。"""
    cells = page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('[class*="picker-cell"]').forEach((c) => {
            const t = (c.innerText || '').trim();
            const cls = c.className || '';
            // 只取纯数字日期 cell（1..31）
            if (t && /^\\d+$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 31) {
              const inView = cls.includes('in-view');
              const disabled = cls.includes('disabled');
              if (inView && !disabled) {
                out.push({day: parseInt(t), text: t});
              }
            }
          });
          return out;
        }"""
    )
    return cells


def select_latest_week(page: Page) -> bool:
    """在周视图中选择最新可用的完整周。

    逻辑：从最大 day 数开始，点击该 day cell，
    然后读取 date range 校验是否为完整 7 天范围。
    """
    cells = read_available_week_cells(page)
    if not cells:
        print("[ERROR] 周视图无可用日期 cell")
        return False

    # 按 day 降序，优先选择最大 day（通常对应最新周）
    cells_sorted = sorted(cells, key=lambda c: c["day"], reverse=True)
    print(f"[OK] 可用日期 cell (降序): {[c['day'] for c in cells_sorted[:10]]}")

    # 尝试每个候选 day，直到选中一个产生完整 7 天范围的周
    for cand in cells_sorted[:10]:
        target = page.locator('[class*="picker-cell"]', has_text=cand["text"]).first
        if not click_stable(page, target):
            continue
        time.sleep(3)
        # 关闭 picker 读取实际范围
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(2)
        start, end = read_date_range(page)
        if start and end:
            print(f"[OK] 选中周: day={cand['day']} -> {start} ~ {end}")
            return True
    print("[ERROR] 无法选中有效周")
    return False


# ===========================================================================
# 表头列映射（动态，非硬编码列号）
# ===========================================================================

def get_header_column_map(page: Page) -> dict:
    """从 thead 建立表头文本->列索引映射。"""
    headers = page.eval_on_selector_all("thead th", """
        els => els.map((el, i) => ({text: (el.textContent || '').trim(), idx: i}))
    """)
    m = {}
    for h in headers:
        norm = normalize_text(h["text"])
        if norm:
            m[norm] = h["idx"]
    return m


def resolve_column_index(col_map: dict, header_variants: list) -> Optional[int]:
    """根据字段名的多个文本变体，从列映射中找到列索引。"""
    for variant in header_variants:
        norm = normalize_text(variant)
        if norm in col_map:
            return col_map[norm]
    return None


# ===========================================================================
# 排序操作（核心：点击 sorter -> 验证降序）
# ===========================================================================

def wait_table_data_stable(page: Page, col_idx: int, timeout: float = 8.0) -> bool:
    """等待表格表体数据在排序后重排完成。

    TikTok Pulse 表格的表头排序状态先更新，表体行数据异步重排，
    直接读取可能拿到上一列排序的残留数据。此处轮询前 3 行目标列值，
    连续两次一致则认为重排完成。
    """
    deadline = time.time() + timeout
    prev = None
    while time.time() < deadline:
        try:
            vals = page.evaluate(
                """(idx) => {
                    const rows = document.querySelectorAll('tbody tr');
                    return Array.from(rows).slice(0, 3).map(r => {
                        const td = r.querySelectorAll('td')[idx];
                        return td ? td.innerText.trim() : '';
                    });
                }""",
                col_idx
            )
        except Exception:
            vals = None
        if prev is not None and vals == prev:
            return True
        prev = vals
        time.sleep(0.8)
    return True  # 超时也继续，后续 validate_field_data 会兜底校验


def verify_sort_descending(page: Page, col_index: int, debug: bool = False) -> bool:
    """检查指定列是否处于降序排序状态。

    TikTok 使用 Pulse 自定义表格，排序方向通过 th class 和 data 属性判断，
    并用实际数据降序校验兜底。
    """
    result = page.evaluate(
        """(idx) => {
            const ths = document.querySelectorAll('thead th');
            const th = ths[idx];
            if (!th) return {ok: false, reason: 'th not found'};
            const cls = th.getAttribute('class') || '';
            const dataAttrs = {};
            for (const attr of th.attributes) {
                if (attr.name.startsWith('data-') || attr.name.startsWith('aria-')) {
                    dataAttrs[attr.name] = attr.value;
                }
            }
            const isSorted = cls.includes('sorted');
            // 尝试从 class 中找方向
            let direction = 'unknown';
            if (cls.includes('desc') || cls.includes('down')) direction = 'desc';
            else if (cls.includes('asc') || cls.includes('up')) direction = 'asc';

            // 也检查 th 内部所有元素是否有方向标记
            const allEls = Array.from(th.querySelectorAll('*'));
            for (const el of allEls) {
                const c = el.getAttribute('class') || '';
                if (c.includes('desc') || c.includes('down')) { direction = 'desc'; break; }
                if (c.includes('asc') || c.includes('up')) { direction = 'asc'; break; }
            }

            return {
                ok: isSorted && direction === 'desc',
                thClass: cls,
                dataAttrs: dataAttrs,
                direction: direction,
                isSorted: isSorted
            };
        }""",
        col_index
    )
    if debug:
        print(f"  [DEBUG verify] {result}")
    if isinstance(result, dict):
        return bool(result.get("ok"))
    return bool(result)


def click_sort_descending(page: Page, col_index: int, value_col: int = -1, max_attempts: int = 6) -> bool:
    """点击排序器直到该列处于降序状态。

    TikTok Pulse 表格：直接点击 th 触发排序。
    对不同列可能需要不同的点击位置。
    """
    click_positions = [0.8, 0.5, 0.3, 0.9, 0.6, 0.2]

    for attempt in range(max_attempts):
        # 如果已经是降序，直接返回
        if verify_sort_descending(page, col_index, debug=(attempt == 0)):
            return True

        # 第一次尝试时打印 th 完整 HTML
        if attempt == 0:
            th_html = page.evaluate(
                """(idx) => {
                    const th = document.querySelectorAll('thead th')[idx];
                    if (!th) return 'th not found';
                    return th.outerHTML.substring(0, 500);
                }""",
                col_index
            )
            print(f"  [DEBUG] th outerHTML: {th_html}")

        # 滚动 th 到可见区域中心
        th = page.locator("thead th").nth(col_index)
        try:
            page.evaluate(
                """(idx) => {
                    const th = document.querySelectorAll('thead th')[idx];
                    if (th) th.scrollIntoView({block: 'center', inline: 'center'});
                }""",
                col_index
            )
        except Exception:
            pass
        time.sleep(0.5)

        pos = click_positions[attempt % len(click_positions)]

        # 方法1: 用 mouse.click 点击 th 的不同位置
        try:
            bb = th.bounding_box()
            if bb:
                click_x = bb["x"] + bb["width"] * pos
                click_y = bb["y"] + bb["height"] / 2
                page.mouse.click(click_x, click_y)
            else:
                th.click(timeout=5000)
        except Exception as e:
            print(f"  [WARN] 点击 th 失败 (attempt {attempt + 1}, pos={pos}): {e}")
            time.sleep(2)
            continue

        time.sleep(3)

        # 验证 class 方向
        if verify_sort_descending(page, col_index, debug=True):
            return True

        # 如果 class 方向无法判断，用实际数据校验
        if value_col >= 0:
            sort_info = page.evaluate(
                """(idx) => {
                    const th = document.querySelectorAll('thead th')[idx];
                    if (!th) return {sorted: false};
                    return {sorted: (th.getAttribute('class') || '').includes('sorted')};
                }""",
                col_index
            )
            if sort_info.get("sorted"):
                rows = extract_rows(page, 1, value_col, max_rows=5)
                values = [r["value"] for r in rows]
                if is_descending(values):
                    print(f"  [OK] 数据验证降序")
                    return True
                else:
                    print(f"  [WARN] 数据非降序，尝试再点击 (attempt {attempt + 1})")
                    continue

        # 最后尝试: 用 JS dispatch click 事件
        if attempt == max_attempts - 2:
            print(f"  [INFO] 尝试 JS dispatch click")
            page.evaluate(
                """(idx) => {
                    const th = document.querySelectorAll('thead th')[idx];
                    if (!th) return;
                    const rect = th.getBoundingClientRect();
                    const evt = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        clientX: rect.left + rect.width * 0.5,
                        clientY: rect.top + rect.height * 0.5
                    });
                    th.dispatchEvent(evt);
                }""",
                col_index
            )
            time.sleep(3)
            if verify_sort_descending(page, col_index, debug=True):
                return True
            if value_col >= 0:
                sort_info = page.evaluate(
                    """(idx) => {
                        const th = document.querySelectorAll('thead th')[idx];
                        if (!th) return {sorted: false};
                        return {sorted: (th.getAttribute('class') || '').includes('sorted')};
                    }""",
                    col_index
                )
                if sort_info.get("sorted"):
                    rows = extract_rows(page, 1, value_col, max_rows=5)
                    values = [r["value"] for r in rows]
                    if is_descending(values):
                        print(f"  [OK] JS click 后数据验证降序")
                        return True

        print(f"  [WARN] 排序方向不是降序 (attempt {attempt + 1}, pos={pos})")

    return False


# ===========================================================================
# 数据读取
# ===========================================================================

def get_page_row_count(page: Page) -> int:
    """获取 tbody 中的行数。"""
    try:
        return page.eval_on_selector("tbody", "el => el.querySelectorAll('tr').length")
    except Exception:
        return 0


def extract_rows(page: Page, keyword_col: int, value_col: int, max_rows: int = 20) -> list:
    """从 tbody 提取数据行。每行: {rank, keyword, value}。keyword 和 value 来自同一个 tr。"""
    data = page.evaluate(
        """({kwCol, valCol, max}) => {
            const rows = document.querySelectorAll('tbody tr');
            const data = [];
            for (let i = 0; i < Math.min(rows.length, max); i++) {
                const cells = rows[i].querySelectorAll('td');
                if (cells.length > Math.max(kwCol, valCol)) {
                    data.push({
                        rank: i + 1,
                        keyword: (cells[kwCol]?.textContent || '').trim(),
                        value: (cells[valCol]?.textContent || '').trim()
                    });
                }
            }
            return data;
        }""",
        {"kwCol": keyword_col, "valCol": value_col, "max": max_rows}
    )
    return data


# ===========================================================================
# 数值解析（仅用于降序校验，不用于存储）
# ===========================================================================

def parse_display_value(s: str) -> float:
    """将 TikTok 页面显示值转为可比数值（仅用于排序校验）。"""
    s = (s or "").strip().replace(",", "").replace(" ", "")
    if not s or s == "—":
        return float("-inf")
    mult = 1.0
    if s.upper().endswith("K"):
        mult = 1000.0
        s = s[:-1]
    elif s.upper().endswith("M"):
        mult = 1000000.0
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return float("-inf")


def is_descending(values: list) -> bool:
    """检查值列表是否降序。"""
    parsed = [parse_display_value(v) for v in values]
    for i in range(len(parsed) - 1):
        if parsed[i] < parsed[i + 1]:
            return False
    return True


# ===========================================================================
# 分页器检查
# ===========================================================================

def check_no_unseen_second_page(page: Page, row_count: int) -> bool:
    """当行数 < 20 时，检查是否有第二页可翻。"""
    if row_count >= 20:
        return True
    has_next = page.evaluate("""
        () => {
            const pager = document.querySelector('.theme-arco-pagination');
            if (!pager) return false;
            const items = pager.querySelectorAll('li');
            for (const li of items) {
                const text = (li.textContent || '').trim();
                if (text.includes('下一个') || text.includes('下一页')) {
                    return !li.classList.contains('theme-arco-pagination-item-disabled');
                }
            }
            return false;
        }
    """)
    if has_next:
        print(f"  [WARN] 行数={row_count} < 20 但有可用的下一页")
        return False
    return True


# ===========================================================================
# 数据校验
# ===========================================================================

def validate_field_data(rows: list, field_name: str,
                        expected_category: str, expected_tab: str,
                        actual_category: str, actual_tab: str,
                        page_row_count: int) -> tuple:
    """10 点校验。返回 (ok, errors)。"""
    errors = []

    # 1. 类目正确
    if expected_category not in actual_category and actual_category not in expected_category:
        errors.append(f"类目错误: 期望含'{expected_category}', 实际'{actual_category}'")

    # 2. 榜单正确
    if expected_tab != actual_tab:
        errors.append(f"榜单错误: 期望'{expected_tab}', 实际'{actual_tab}'")

    # 4. 降序
    values = [r["value"] for r in rows]
    if not is_descending(values):
        errors.append(f"{field_name} 数据非降序")

    # 5. 行数 > 0
    if len(rows) == 0:
        errors.append(f"{field_name} 无数据行")

    # 6. 每条有关键词
    for i, r in enumerate(rows):
        if not r["keyword"]:
            errors.append(f"第{i + 1}行关键词为空")
            break

    # 7. 每条有数值
    for i, r in enumerate(rows):
        if not r["value"]:
            errors.append(f"第{i + 1}行数值为空")
            break

    # 10. 页面行数与提取行数一致（行数<20时）
    if len(rows) < 20 and page_row_count != len(rows):
        errors.append(f"行数不一致: 页面{page_row_count} vs 提取{len(rows)}")

    return (len(errors) == 0, errors)


# ===========================================================================
# TXT 生成与保存
# ===========================================================================

def build_txt_content(date_start: str, date_end: str, fields_data: dict,
                      tab_name: str = "热门搜索关键词",
                      period_label: str = "月度",
                      category_label: str = LEVEL_2,
                      level1_label: str = LEVEL_1) -> str:
    """按已验证格式生成 TXT 内容。"""
    lines = []
    lines.append(f"TikTok 泰国｜{level1_label}｜{category_label}｜{tab_name}")
    lines.append(f"日期：{date_start} - {date_end}")
    lines.append("=" * 50)

    for field_name, rows in fields_data.items():
        n = len(rows)
        lines.append("")
        lines.append(f"【{field_name} TOP{n}｜高→低】")
        lines.append("")
        for r in rows:
            lines.append(f"{r['rank']}. {r['keyword']} — {r['value']}")
        lines.append("")

    return "\n".join(lines)


def save_txt_atomic(content: str, path: Path) -> bool:
    """原子保存：写临时文件 -> 全部校验通过后 os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".txt.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(str(tmp_path), str(path))
        return True
    except Exception as e:
        print(f"[ERROR] 保存失败: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False


# ===========================================================================
# 主流程
# ===========================================================================

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="TikTok 页面 TOP20 采集器")
    ap.add_argument("--period", choices=["month", "week"], default="month",
                    help="数据粒度: month(月度) / week(周度)")
    ap.add_argument("--tab", choices=["热门搜索关键词", "飙升关键词"], default="热门搜索关键词",
                    help="榜单类型")
    ap.add_argument("--level1", default=None,
                    help="一级类目名称（官方类目原文），默认使用模块常量 LEVEL_1")
    ap.add_argument("--level2", default=None,
                    help="二级类目名称（官方类目原文），默认使用模块常量 LEVEL_2")
    ap.add_argument("--level2-page-name", default=None,
                    help="二级类目页面显示名（官方名与页面显示名不一致时用于级联点击/校验；缺省=--level2）")
    ap.add_argument("--output-dir", default=None,
                    help="输出根目录（替代 data/TH/<level1>/<level2>），用于批次隔离保存，默认不传")
    args = ap.parse_args(argv)

    period = args.period
    ranking_tab = args.tab
    level_1 = args.level1 or LEVEL_1
    level_2 = args.level2 or LEVEL_2
    level_2_page = args.level2_page_name or level_2
    period_label = "月度" if period == "month" else "周度"

    print("=" * 70)
    print("TikTok 页面 TOP20 采集器 (Python + Playwright)")
    print(f"目标: {COUNTRY} / {level_1} / {level_2} / {period_label} / {ranking_tab}")
    if level_2_page != level_2:
        print(f"页面类目显示名: {level_2_page}（官方名: {level_2}）")
    print(f"Profile: {PROFILE}")
    print("=" * 70)

    with sync_playwright() as p:
        # 1. 启动浏览器
        print("\n[1] 启动浏览器")
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            channel="chrome",
            headless=False,
            viewport={"width": 1680, "height": 950},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
            ignore_default_args=["--enable-automation"]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 2. 打开页面 + 登录检查
        print("[2] 打开页面")
        safe_goto(page, BASE_URL, 6)
        time.sleep(4)
        # 刷新 page 引用（防止 page 被关闭/替换）
        if not ctx.pages:
            print("[ERROR] 浏览器无可用页面")
            ctx.close()
            return 3
        page = ctx.pages[0]
        if not check_login(page):
            ctx.close()
            print("[ERROR] 需要手动登录。请先在浏览器中登录 TikTok Seller Center，然后重跑。")
            return 3
        print("[OK] 登录状态正常")

        safe_goto(page, KEYWORD_URL, 8)
        time.sleep(8)
        dismiss_chat_popup(page)
        print(f"[DEBUG] 当前URL: {page.url}")

        # 等待表格区域出现
        try:
            page.wait_for_selector(".theme-arco-table, [role='tab']", timeout=30000)
            print("[OK] 页面元素已加载")
        except Exception:
            print("[WARN] 等待页面元素超时，继续尝试...")

        # 3. 切换榜单 tab
        print(f"[3] 切换到 {ranking_tab}")
        if not switch_ranking_tab(page, ranking_tab):
            ctx.close()
            return 1

        # 4. 选择类目
        print(f"[4] 选择类目: {level_1} -> {level_2_page}")
        if not pick_cascader_item(page, level_1, level_2_page):
            ctx.close()
            return 1

        # 5. 切换日期粒度，选最新周期
        if period == "month":
            print("[5] 切换日期选择器到月度")
            if not _ensure_picker_in_month_view(page):
                ctx.close()
                print("[ERROR] 无法进入月度视图")
                return 1

            months = read_available_months(page)
            if not months:
                ctx.close()
                print("[ERROR] 无可用月份")
                return 1
            print(f"[OK] 可用月份(从新到旧): {months}")

            latest_month = months[0]
            print(f"[OK] 选择最新月份: {latest_month}")
            if not click_month_cell(page, latest_month):
                ctx.close()
                print("[ERROR] 点击月份失败")
                return 1
            time.sleep(3)
        else:
            print("[5] 切换日期选择器到周度")
            if not _ensure_picker_in_week_view(page):
                ctx.close()
                print("[ERROR] 无法进入周度视图")
                return 1

            if not select_latest_week(page):
                ctx.close()
                print("[ERROR] 选择最新周失败")
                return 1

        # 关闭 picker，等待表格加载
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(8)

        # 6. 读取实际日期范围
        date_start, date_end = read_date_range(page)
        if not date_start or not date_end:
            ctx.close()
            print("[ERROR] 无法读取页面日期范围")
            return 1
        print(f"[OK] 实际日期范围: {date_start} ~ {date_end}")

        # 7. 读取当前类目和榜单（校验用）
        actual_category = get_current_category(page)
        actual_tab = get_current_tab(page)
        print(f"[OK] 当前类目: {actual_category}, 当前榜单: {actual_tab}")

        # 8. 建立表头列映射
        print("[6] 建立表头列映射")
        col_map = get_header_column_map(page)
        print(f"[OK] 列映射: {col_map}")

        # 找到关键词列
        kw_col = col_map.get(normalize_text("关键词"))
        if kw_col is None:
            ctx.close()
            print("[ERROR] 找不到关键词列")
            return 1
        print(f"[OK] 关键词列索引: {kw_col}")

        # 9. 逐字段排序 + 提取
        fields_data = {}
        fields_success = {}

        for field_name, header_variants in TARGET_FIELDS:
            print(f"\n[7] 处理字段: {field_name}")

            # 找列索引
            col_idx = resolve_column_index(col_map, header_variants)
            if col_idx is None:
                print(f"  [ERROR] 找不到字段列: {field_name}")
                fields_success[field_name] = False
                continue
            print(f"  [OK] {field_name} -> 列索引 {col_idx}")

            # 排序
            print(f"  [OK] 点击排序器...")
            if not click_sort_descending(page, col_idx, value_col=col_idx):
                print(f"  [ERROR] {field_name} 排序失败")
                fields_success[field_name] = False
                continue

            if not verify_sort_descending(page, col_idx):
                print(f"  [WARN] {field_name} class 验证未通过，但数据降序校验已通过")
                # 数据校验已通过即可
            print(f"  [OK] {field_name} 已降序排序")

            # 等待表体数据重排完成（表头先更新，行数据异步加载）
            wait_table_data_stable(page, col_idx)

            # 获取页面行数
            page_row_count = get_page_row_count(page)
            print(f"  [OK] 页面行数: {page_row_count}")

            # 提取数据
            rows = extract_rows(page, kw_col, col_idx, max_rows=20)
            print(f"  [OK] 提取 {len(rows)} 条数据")

            if len(rows) == 0:
                print(f"  [ERROR] {field_name} 无数据")
                fields_success[field_name] = False
                continue

            # 分页器检查
            check_no_unseen_second_page(page, len(rows))

            # 校验
            ok, errors = validate_field_data(
                rows, field_name,
                expected_category=level_2_page,
                expected_tab=ranking_tab,
                actual_category=actual_category,
                actual_tab=actual_tab,
                page_row_count=page_row_count
            )
            if not ok:
                print(f"  [ERROR] {field_name} 校验失败:")
                for e in errors:
                    print(f"    - {e}")
                fields_success[field_name] = False
                continue

            # 打印前 3 条预览
            for r in rows[:3]:
                print(f"    {r['rank']}. {r['keyword']} -- {r['value']}")

            fields_data[field_name] = rows
            fields_success[field_name] = True

        # 10. 检查全部成功
        success_count = sum(1 for v in fields_success.values() if v)
        print(f"\n[8] 采集完成: {success_count}/{len(TARGET_FIELDS)} 字段成功")

        if success_count != len(TARGET_FIELDS):
            failed = [k for k, v in fields_success.items() if not v]
            print(f"[ERROR] 失败字段: {failed}")
            ctx.close()
            return 1

        # 11. 生成并保存 TXT
        print("[9] 生成 TXT 文件")
        content = build_txt_content(date_start, date_end, fields_data,
                                    tab_name=ranking_tab, period_label=period_label,
                                    category_label=level_2, level1_label=level_1)

        period_dir = "monthly" if period == "month" else "weekly"
        filename = f"TH_{level_1}_{level_2}_{period_label}_{ranking_tab}_{date_start}_{date_end}.txt"
        if args.output_dir:
            output_base = Path(args.output_dir)
        else:
            output_base = PROJECT_ROOT / "data" / "TH" / level_1 / level_2
        filepath = output_base / period_dir / filename
        print(f"[OK] 目标文件: {filepath}")

        if not save_txt_atomic(content, filepath):
            ctx.close()
            return 1

        print(f"[OK] 文件已保存: {filepath}")

        # 12. 最终检查
        if filepath.exists():
            size = filepath.stat().st_size
            print(f"[OK] 文件存在, 大小: {size} bytes")
        else:
            print("[ERROR] 文件不存在!")
            ctx.close()
            return 1

        ctx.close()
        print("\n" + "=" * 70)
        print("采集成功!")
        print("=" * 70)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())


# =============================================================================
# 结构化采集接口（供 collection_manager / 其他模块调用）
# 复用全部已有页面操作函数，仅新增对外返回 PageTop20Record 的能力
# 原有 main() / TXT 输出 / CLI 入口完全不动
# =============================================================================

# 六个排序指标 → (表头变体列表, ranking_field 名称)
# ranking_field 使用英文标识，与 page_top20_data 表一致
SORT_FIELDS = [
    # (表头候选名称列表, ranking_field, 是否数值越大越好)
    (["搜索量", "搜索量指数"], "search_volume", True),
    (["商品点击指数", "商品点击数", "点击数", "商品点击"], "product_clicks", True),
    (["SKU销量指数", "SKU 销量指数", "SKU销售指数", "SKU 销售指数"], "sku_sales_index", True),
    (["在售商品", "在售商品数"], "on_sale_products", True),
    (["CTR指数", "CTR 指数", "CTR"], "ctr_index", True),
    (["CTOR评分", "CTOR 评分", "CTOR"], "ctor_score", True),
]


def collect_top20_batch(
    page: Page,
    market: str,
    level_1: str,
    level_2: Optional[str],
    ranking_type: str,
    period_granularity: str = "month",
    profile_path: Optional[str] = None,
) -> dict:
    """采集一个完整 batch（6 个 ranking_field 的 TOP20）。

    参数:
        page: 已登录 Seller Center 并停留在关键词榜单页面的 Playwright Page 对象
        market: 市场代码，如 "TH"
        level_1: 一级类目名称（页面原文）
        level_2: 二级类目名称（页面原文），None 表示只选一级
        ranking_type: 榜单类型，"热门搜索关键词" / "飙升关键词"
        period_granularity: "month" / "week"
        profile_path: Chrome Profile 路径（仅用于元信息记录）

    返回:
        dict 结构:
        {
            "success": bool,
            "error": str or None,
            "period_start": str,      # YYYY-MM-DD
            "period_end": str,        # YYYY-MM-DD
            "records": [PageTop20Record, ...],  # 结构化记录
            "fields_result": {       # 每个字段的采集结果
                ranking_field: {
                    "success": bool,
                    "count": int,
                    "error": str or None,
                    "display_values": [str, ...],  # 原始显示值（用于验证排序方向）
                }
            },
        }
    """
    from app.models import PageTop20Record

    result = {
        "success": False,
        "error": None,
        "period_start": "",
        "period_end": "",
        "records": [],
        "fields_result": {},
    }

    try:
        # 1. 选择类目
        logger.info(f"选择类目: {level_1} / {level_2}")
        if not pick_cascader_item(page, level_1, level_2):
            result["error"] = f"类目选择失败: {level_1} / {level_2}"
            return result
        time.sleep(3)

        # 2. 切换榜单 Tab
        logger.info(f"切换榜单: {ranking_type}")
        if not switch_ranking_tab(page, ranking_type):
            result["error"] = f"榜单切换失败: {ranking_type}"
            return result
        time.sleep(3)

        # 3. 切换周期粒度（月/周），并选择最新可用周期
        granularity_label = "月度" if period_granularity == "month" else "周度"
        logger.info(f"切换周期: {granularity_label}")
        if not switch_granularity(page, granularity_label):
            result["error"] = f"周期切换失败: {granularity_label}"
            return result
        time.sleep(2)

        if period_granularity == "month":
            months = read_available_months(page)
            if not months:
                result["error"] = "未读取到可用月份"
                return result
            # 选择最新月份（第一个）
            latest_month = months[0]
            logger.info(f"选择最新月份: {latest_month}")
            if not click_month_cell(page, latest_month):
                result["error"] = f"月份选择失败: {latest_month}"
                return result
        else:  # week
            if not select_latest_week(page):
                result["error"] = "周度选择失败"
                return result
        time.sleep(5)

        # 4. 读取实际日期范围
        date_start, date_end = read_date_range(page)
        if not date_start or not date_end:
            result["error"] = f"日期范围读取失败: {date_start} ~ {date_end}"
            return result
        result["period_start"] = date_start
        result["period_end"] = date_end
        logger.info(f"实际周期: {date_start} ~ {date_end}")

        # 5. 获取表头列映射
        col_map = get_header_column_map(page)
        if not col_map:
            result["error"] = "表头列映射获取失败"
            return result

        # 找关键词列索引
        kw_idx = resolve_column_index(col_map, ["关键词", "搜索关键词"])
        if kw_idx is None:
            result["error"] = "未找到关键词列"
            return result
        logger.info(f"关键词列索引: {kw_idx}")

        # 6. 逐个字段排序 + 提取
        batch_ts = time.strftime("%Y%m%d_%H%M%S")
        batch_id = f"top20_{market}_{level_1}_{level_2 or 'L1'}_{ranking_type}_{period_granularity}_{batch_ts}"
        # 替换掉可能导致路径问题的字符
        batch_id = batch_id.replace(" ", "_").replace("/", "_")

        all_success = True
        all_records: list[PageTop20Record] = []

        for header_variants, ranking_field, _descending in SORT_FIELDS:
            field_result = {
                "success": False,
                "count": 0,
                "error": None,
                "display_values": [],
            }
            result["fields_result"][ranking_field] = field_result

            try:
                # 找该指标的列索引
                col_idx = resolve_column_index(col_map, header_variants)
                if col_idx is None:
                    field_result["error"] = f"未找到列: {header_variants}"
                    all_success = False
                    continue

                logger.info(f"[{ranking_field}] 列索引={col_idx}，切换降序...")

                # 点击降序
                if not click_sort_descending(page, col_idx):
                    field_result["error"] = f"排序切换失败: {ranking_field}"
                    all_success = False
                    continue
                time.sleep(2)

                # 读取行数据
                rows = extract_rows(page, kw_idx, col_idx, max_rows=20)
                if not rows:
                    field_result["error"] = f"未读取到行数据: {ranking_field}"
                    all_success = False
                    continue

                # 验证降序
                values_for_verify = [parse_display_value(r[1]) for r in rows]
                if not is_descending(values_for_verify):
                    field_result["error"] = f"排序验证失败（非降序）: {ranking_field}"
                    all_success = False
                    continue

                # 转换为 PageTop20Record
                for rank_idx, (keyword, display_value) in enumerate(rows, start=1):
                    numeric_val = parse_display_value(display_value)
                    rec = PageTop20Record(
                        market=market,
                        level_1_category=level_1,
                        level_2_category=level_2,
                        ranking_type=ranking_type,
                        period_granularity=period_granularity,
                        period_start=date_start,
                        period_end=date_end,
                        ranking_field=ranking_field,
                        rank=rank_idx,
                        keyword=keyword,
                        display_value=display_value,
                        numeric_value=numeric_val,
                        batch_id=batch_id,
                    )
                    all_records.append(rec)
                    field_result["display_values"].append(display_value)

                field_result["success"] = True
                field_result["count"] = len(rows)
                logger.info(f"[{ranking_field}] 成功，{len(rows)} 行")

            except Exception as e:
                field_result["error"] = f"异常: {e}"
                all_success = False
                logger.exception(f"[{ranking_field}] 采集异常")

        result["records"] = all_records
        result["success"] = all_success
        if not all_success:
            failed_fields = [f for f, r in result["fields_result"].items() if not r["success"]]
            result["error"] = f"部分字段失败: {', '.join(failed_fields)}"

        return result

    except Exception as e:
        logger.exception("collect_top20_batch 总异常")
        result["error"] = f"总异常: {e}"
        return result
