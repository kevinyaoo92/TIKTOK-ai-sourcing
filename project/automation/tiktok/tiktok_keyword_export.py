# -*- coding: utf-8 -*-
"""TikTok Seller Center 关键词榜单导出（Phase 2+：通用采集 + 实际数据周期判定）。

设计原则（产品冻结）:
- 类目/二级类目/榜单类型/粒度全部参数化，禁止硬编码特定类目（如"时尚配件/家居收纳"）。
- 数据周期必须通过"真实导出验证"确定，禁止依据 UI 月份下拉框或经验推断：
    1. 读取 UI 当前可用的所有月份（按从新到旧排序）
    2. 对每个候选 UI 月份真实执行导出→捕获→读 Excel 元信息
    3. 以 Excel `[日期范围]` 为该数据集真实 period_start/period_end
    4. 继续探测更新的 UI 月份，直至发现"实际 Excel 日期更新的候选"或达到上限
    5. 最终选择真实导出的有效数据周期中日期最新的那个
- 保留 UI 选择周期与 Excel 实际周期的差异信息（不丢弃）
- 所有候选周期的导出暂存到 STAGE_DIR，最终只把"最新有效周期"文件保留到 SAVE_DIR
  （避免多次试探污染正式目录；非最新周期文件落档后不删除，保留为可追溯的探测样本）

DOM 结构（2026-09-09 实测）:
- 类目级联：.theme-arco-cascader-view 点击展开；一级列 .theme-arco-cascader-list-item；
  hover+click 触发第二列出现二级类目（同 class）。
- 粒度切换按钮（顶部）：<div class="theme-arco-space-item">，text=日/周/月，按钮宽度≈30px。
- 月份视图（点"月"后）：每月 cell .theme-arco-picker-cell，可用的无 disabled class；
  当前年 9~12 月 disabled。月度视图无年份翻页按钮——所有月份都一次性显示。
- 周度视图（点"周"后）：7 列布局，每天数字 text=1..31，theme-arco-picker-cell-selected
  标记当前选中的连续 7 天；未来日期 disabled。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

BASE = "https://seller.tiktokshopglobalselling.com/"
KEYWORD_URL = "https://seller.tiktokshopglobalselling.com/compass/search-analytics/keyword-rank?shop_region=TH"

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # project/automation/tiktok/xx.py -> 项目根
sys.path.insert(0, str(PROJECT_ROOT))  # 确保子进程能 import app
PROFILE = str(PROJECT_ROOT / ".run" / "profile")
SAVE_DIR = PROJECT_ROOT / "data" / "raw" / "tiktok" / "thailand"
STAGE_DIR = PROJECT_ROOT / ".run" / "stage"  # 探测过程中临时落盘目录
RESULT_FILE = PROJECT_ROOT / ".run" / "export_result.json"

CN_MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
             "七月", "八月", "九月", "十月", "十一月", "十二月"]
CN_MONTH_TO_NUM = {name: i + 1 for i, name in enumerate(CN_MONTHS)}
CN_NUM_TO_MONTH = {i + 1: name for i, name in enumerate(CN_MONTHS)}


def step_fail(step: str, msg: str):
    print(json.dumps({"ok": False, "step": step, "error": msg}, ensure_ascii=False))
    sys.exit(1)


def wait_visible(page, locator, timeout_s: int) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def dismiss_chat_popup(page):
    try:
        ok = page.get_by_role("button", name="好的", exact=True)
        if ok.count() > 0 and ok.first.is_visible():
            ok.first.click(timeout=3000)
            time.sleep(1)
    except Exception:
        pass


def click_stable(page, loc, tries: int = 3) -> bool:
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


def safe_goto(page, url: str, sleep_s: float):
    try:
        page.goto(url, wait_until="commit", timeout=60000)
    except Exception as e:
        if "interrupted" not in str(e):
            raise
    time.sleep(sleep_s)


# ---------------------------------------------------------------------------
# 粒度切换按钮（真实 class = theme-m4b-date-picker-range-mode-item；用 get_by_text
# 精确匹配"日/周/月"文本即可，无需坐标/class 硬编码）
# ---------------------------------------------------------------------------


def switch_granularity(page, label: str) -> bool:
    """点击粒度按钮（日/周/月）。用 get_by_text 精确匹配，遍历可见项点击。"""
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


# ---------------------------------------------------------------------------
# 类目级联选择
# ---------------------------------------------------------------------------
def pick_cascader_item(page, level_1: str, level_2: Optional[str]) -> None:
    view = page.locator(".theme-arco-cascader-view")
    if not wait_visible(page, view, 15):
        step_fail("选择类目", "找不到类目级联 .theme-arco-cascader-view")
    view.first.click()
    time.sleep(3)

    l1_item = page.locator(".theme-arco-cascader-list-item", has_text=level_1)
    if not wait_visible(page, l1_item, 15):
        step_fail("选择类目", f"类目级联中找不到一级类目[{level_1}]")
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
        step_fail("选择类目", f"点击一级类目[{level_1}]失败")
    time.sleep(3)

    if level_2:
        # Retry: second column may disappear after clicking level_1
        # Re-hover on level_1 to re-trigger second column appearance
        l2_clicked = False
        for _retry in range(3):
            l2_item = page.locator(".theme-arco-cascader-list-item", has_text=level_2)
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
            # Re-hover on level_1 to re-trigger second column
            try:
                l1_hover = page.locator(".theme-arco-cascader-list-item", has_text=level_1)
                if l1_hover.count() > 0:
                    l1_hover.first.hover()
                    time.sleep(2)
            except Exception:
                pass
        if not l2_clicked:
            step_fail("选择类目", f"类目级联中找不到二级类目[{level_2}]")
        time.sleep(3)

    vinp = page.locator("input.theme-arco-cascader-view-input")
    actual = (vinp.first.input_value() or "").strip() if vinp.count() else ""
    expected = level_2 or level_1
    if actual != expected:
        step_fail("校验类目", f"类目选择后值为[{actual}]，期望[{expected}]")


# ---------------------------------------------------------------------------
# 月份视图相关：从 UI 读可用月份、按"从新到旧"返回候选列表
# ---------------------------------------------------------------------------
def read_available_months(page) -> list[str]:
    """读取月份视图下的可用月份。

    DOM 顺序是 1→12 月（一月在前）。返回按"从新到旧"排序的可用月份名列表
    （如 ['八月','七月','六月',...]）—— 探测应优先验证最新的 UI 月份。
    """
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
    # 去重保序（DOM 顺序 = 一月→十二月）
    seen = set()
    avail = []
    for m in cells:
        if m not in seen:
            seen.add(m)
            avail.append(m)
    # 反转为"从新到旧"（最新月份优先探测）
    avail.reverse()
    return avail


def _ensure_picker_in_month_view(page) -> bool:
    """确保 picker 处于打开且"月"粒度状态。返回是否就绪。

    实现：每次调用都"无条件重建"——先 ESC 关闭任何残留弹层，再点 picker 打开弹层，
    再切"月"粒度，最后校验月份 cell 已渲染。
    不依赖"弹层是否已打开"的检测，因为：
    1) arco-design 的 picker 关闭后，月份 cell 在 DOM 中可能残留数十毫秒造成误判；
    2) 不同 tab(ranking_type) 切换后，picker 状态会重置为该 tab 的默认粒度（日/周），
       此时必须重新点 picker 触发弹层。
    """
    # 0. ESC 关闭可能残留的弹层
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass
    # 1. 点 picker range 触发弹层
    picker = page.locator("div.theme-arco-picker-range")
    if not wait_visible(page, picker, 10):
        return False
    try:
        picker.first.click(timeout=8000)
    except Exception:
        return False
    time.sleep(2.5)
    # 2. 切"月"粒度
    if not switch_granularity(page, "月"):
        return False
    time.sleep(2.5)
    # 3. 校验月份 cell 已渲染
    return read_available_months(page) != []


def click_month_cell(page, cn_name: str) -> bool:
    """点击月份视图中的某个月份 cell。"""
    target = page.locator('[class*="picker-cell"]', has_text=cn_name).first
    return click_stable(page, target)


# ---------------------------------------------------------------------------
# 周视图相关：从 UI 读可用周、按"从新到旧"返回候选列表
# ---------------------------------------------------------------------------
def _read_week_day_cells(page) -> list[dict]:
    """读取周视图下所有非 disabled 的日期 cell。

    Arco Design 周视图：7 列日历，每个 cell 含日期数字(1..31)。
    class 含 'picker-cell' + 'in-view'，disabled 的 cell 含 'disabled'。
    返回 [{day: int, element_text: str}, ...] 按 DOM 顺序。
    """
    cells = page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('[class*="picker-cell"]').forEach((c) => {
            const t = (c.innerText || '').trim();
            const cls = c.className || '';
            // 只取纯数字日期 cell（非空、纯数字、在 1..31 范围）
            if (t && /^\d+$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 31) {
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


def read_available_weeks(page) -> list[dict]:
    """读取周视图下的可用完整周（7 个连续非 disabled 日）。

    返回 [{label: "W{start}-{end}", start_day: int, end_day: int}, ...]
    按"从新到旧"排序（最大 start_day 优先）。
    """
    cells = _read_week_day_cells(page)
    if not cells:
        return []

    # 按日期数字排序
    days = sorted(set(c["day"] for c in cells))

    # 分组成连续 7 天的周
    weeks = []
    i = 0
    while i < len(days):
        # 尝试找从 days[i] 开始的连续 7 天
        start = days[i]
        consecutive = [start]
        j = i + 1
        while j < len(days) and days[j] == days[j-1] + 1:
            consecutive.append(days[j])
            j += 1
        if len(consecutive) >= 7:
            # 取前 7 天作为一个周
            w_start = consecutive[0]
            w_end = consecutive[6]
            weeks.append({
                "label": f"W{w_start}-{w_end}",
                "start_day": w_start,
                "end_day": w_end,
            })
        i = j if len(consecutive) >= 7 else i + 1

    # 从新到旧
    weeks.reverse()
    return weeks


def _ensure_picker_in_week_view(page) -> bool:
    """确保 picker 处于打开且"周"粒度状态。返回是否就绪。"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass
    picker = page.locator("div.theme-arco-picker-range")
    if not wait_visible(page, picker, 10):
        return False
    try:
        picker.first.click(timeout=8000)
    except Exception:
        return False
    time.sleep(2.5)
    if not switch_granularity(page, "周"):
        return False
    time.sleep(2.5)
    return read_available_weeks(page) != []


def click_week_cell(page, day_num: int) -> bool:
    """点击周视图中的某个日期 cell（点击任意日即选中该整个周）。

    用 JS 精确匹配 innerText==str(day_num) 的 picker-cell，避免 has_text 子串匹配。
    """
    clicked = page.evaluate(
        """(dayStr) => {
          const cells = document.querySelectorAll('[class*="picker-cell"]');
          for (const c of cells) {
            const t = (c.innerText || '').trim();
            const cls = c.className || '';
            if (t === dayStr && cls.includes('in-view') && !cls.includes('disabled')) {
              c.click();
              return true;
            }
          }
          return false;
        }""",
        str(day_num),
    )
    if clicked:
        time.sleep(1.5)
        return True
    # Fallback: Playwright locator with exact text
    target = page.locator('[class*="picker-cell"]', has_text=str(day_num))
    for i in range(target.count()):
        try:
            el = target.nth(i)
            if el.is_visible() and (el.inner_text() or '').strip() == str(day_num):
                el.click(timeout=5000)
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# 导出并下载（一个完整 cycle：点击导出 → 等下载按钮 → expect_download 捕获）
# ---------------------------------------------------------------------------
def _find_first_visible(loc) -> int:
    """在多个匹配元素中找第一个 visible 的，返回其 index；找不到返回 -1。

    加 try/except 容错：点击"导出数据"后 TikTok 可能短暂重绘/新开 tab 导致
    target 短暂失联（TargetClosedError），需在重试循环里吞掉这类瞬时错误。
    """
    try:
        n = loc.count()
    except Exception:
        return -1
    for i in range(n):
        try:
            if loc.nth(i).is_visible():
                return i
        except Exception:
            continue
    return -1


def export_and_download(page, ctx, timeout_dl: int = 180) -> Optional[Path]:
    """点击导出数据 → 触发下载 → 捕获 download URL → ctx.request.get 直接下载。

    TikTok 后台"导出数据"点击后：
    - "下载"按钮在 2~3 秒内出现（导出抽屉），随后 page 在 3~5 秒内被后台关闭
    - download 事件会携带真实文件 URL + 建议文件名
    - page 关闭后 dl.save_as / dl.path 均失效（TargetClosedError）
    - 因此必须在 page 存活窗口内：注册 download 监听 → 点导出 → 快速点下载 → 捕获 URL
      → 用 ctx.request.get(url) 直接 GET 文件（APIRequestContext 独立于 page）

    返回下载文件路径或 None。
    """
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

    captured = {"url": None, "suggested": None, "saved_path": None}

    def _on_download(dl):
        try:
            captured["url"] = dl.url
            captured["suggested"] = dl.suggested_filename
            # Immediately save the file in the callback (runs before context dies)
            try:
                _fname = os.path.basename(dl.suggested_filename or "export.xlsx") or "export.xlsx"
                _save_path = STAGE_DIR / _fname
                dl.save_as(str(_save_path))
                if _save_path.exists() and _save_path.stat().st_size > 0:
                    captured["saved_path"] = str(_save_path)
                    print(f"[export_and_download] dl.save_as 成功: {_save_path}, size={_save_path.stat().st_size}", flush=True)
            except Exception as se:
                print(f"[export_and_download] dl.save_as 失败: {type(se).__name__}: {str(se)[:100]}", flush=True)
        except Exception:
            pass

    try:
        page.on("download", _on_download)
    except Exception:
        pass

    export_btn = page.get_by_role("button", name="导出数据", exact=True)
    if not wait_visible(page, export_btn, 20):
        return None
    try:
        disabled = export_btn.first.is_disabled()
        if disabled:
            return None
    except Exception:
        pass

    # Capture cookies BEFORE clicking export (context may die after click)
    _cookies = []
    try:
        _cookies = ctx.cookies()
    except Exception:
        pass

    try:
        export_btn.first.click()
    except Exception:
        return None

    # 在 page 关闭前快速点击"下载"按钮（多次尝试，因为 page 关闭时机不稳定）
    # 与 diag_single_ctx.py 成功模式一致：循环 + page.on('download') 回调捕获 URL
    dl_btn = page.get_by_role("button", name="下载", exact=True)
    deadline = time.time() + 30
    debug = {"attempts": 0, "clicked_dl_idx": None}
    while time.time() < deadline and not captured["url"]:
        try:
            idx = _find_first_visible(dl_btn)
            if idx >= 0:
                debug["attempts"] += 1
                try:
                    dl_btn.nth(idx).click(timeout=5000)
                    debug["clicked_dl_idx"] = idx
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(1)

    url = captured["url"]
    if not url:
        print(f"[export_and_download] 未捕获 download URL: {debug}, captured={captured}", flush=True)
        return None

    print(f"[export_and_download] 捕获 download URL: {url[:120]}, attempts={debug['attempts']}", flush=True)

    fname = os.path.basename(captured["suggested"] or "export.xlsx") or "export.xlsx"
    dest = STAGE_DIR / fname

    # 优先检查 download 回调中 dl.save_as 是否已保存
    saved = captured.get("saved_path")
    if saved and Path(saved).exists() and Path(saved).stat().st_size > 0:
        print(f"[export_and_download] 使用 dl.save_as 保存的文件: {saved}", flush=True)
        return Path(saved)

    # 检查 STAGE_DIR 是否有 Chrome downloads_path 自动落盘的文件
    for _f in STAGE_DIR.glob("*.xlsx"):
        if _f.stat().st_size > 0:
            print(f"[export_and_download] 使用 STAGE_DIR 文件: {_f}, size={_f.stat().st_size}", flush=True)
            return _f

    # 倒数第二回退：用 ctx.request.get 直接下载（带 cookie，独立于 page）
    try:
        resp = ctx.request.get(url)
        if resp.status == 200 and resp.body():
            with open(dest, "wb") as f:
                f.write(resp.body())
            if dest.exists() and dest.stat().st_size > 0:
                print(f"[export_and_download] ctx.request.get 成功: {dest}, size={dest.stat().st_size}", flush=True)
                return dest
        else:
            print(f"[export_and_download] ctx.request.get 失败: status={resp.status if resp else 'no resp'}", flush=True)
    except Exception as e:
        print(f"[export_and_download] ctx.request.get 异常: {type(e).__name__}: {str(e)[:150]}", flush=True)

    # 最终回退：用 requests + captured cookies 下载（完全独立于 Playwright context）
    if _cookies:
        try:
            import requests as _requests
            ck = {c["name"]: c["value"] for c in _cookies}
            resp = _requests.get(url, cookies=ck, timeout=60, verify=False)
            if resp.status_code == 200 and resp.content:
                with open(dest, "wb") as f:
                    f.write(resp.content)
                if dest.exists() and dest.stat().st_size > 0:
                    print(f"[export_and_download] requests.get 成功: {dest}, size={dest.stat().st_size}", flush=True)
                    return dest
            else:
                print(f"[export_and_download] requests.get 失败: status={resp.status_code}", flush=True)
        except Exception as e:
            print(f"[export_and_download] requests.get 异常: {type(e).__name__}: {str(e)[:150]}", flush=True)
    return None


# ---------------------------------------------------------------------------
# 校验 Excel：解析 + 提取真实周期 + 行数 + 类目一致性 + 字段完整性
# ---------------------------------------------------------------------------
def validate_excel(
    path: Path,
    *,
    expected_level_1: Optional[str],
    expected_level_2: Optional[str],
    expected_keyword_type: str,
) -> dict:
    """使用 parser 解析并校验 Excel。

    返回 dict:
      valid: bool
      error: str (when invalid)
      period_start / period_end / period_type / n_records
      file_hash / source_file / level_1_in_file / level_2_in_file / category_in_file
    """
    from app.tiktok.parser import parse_export_file
    try:
        parsed = parse_export_file(
            path,
            market="TH",
            keyword_type=expected_keyword_type,
            level_1_category=expected_level_1,
            level_2_category=expected_level_2,
        )
    except Exception as e:
        return {"valid": False, "error": f"parse_export_file 异常: {type(e).__name__}: {e}", "file": str(path)}

    # 一致性校验
    if expected_level_2 and not parsed.get("level_2_category"):
        return {"valid": False, "error": f"二级类目与任务不符: parsed level_2={parsed.get('level_2_category')!r}",
                "file": str(path), **parsed_meta_summary(parsed)}

    if expected_level_1 and parsed.get("level_1_category") and parsed["level_1_category"] != expected_level_1:
        return {"valid": False, "error": f"一级类目与任务不符: parsed level_1={parsed['level_1_category']!r}",
                "file": str(path), **parsed_meta_summary(parsed)}

    if not parsed.get("period_start") or not parsed.get("period_end"):
        return {"valid": False, "error": "Excel 未读取到真实日期范围",
                "file": str(path), **parsed_meta_summary(parsed)}

    if not parsed.get("n_records") or parsed["n_records"] <= 0:
        return {"valid": False, "error": "Excel 无数据行",
                "file": str(path), **parsed_meta_summary(parsed)}

    return {"valid": True, **parsed_meta_summary(parsed)}


def parsed_meta_summary(p: dict) -> dict:
    """从 parse_export_file 返回的 dict 摘出摘要字段。"""
    return {
        "file": p.get("file_path"),
        "source_file": p.get("source_file"),
        "file_hash": p.get("file_hash"),
        "period_start": p.get("period_start"),
        "period_end": p.get("period_end"),
        "period_type": p.get("period_type"),
        "n_records": p.get("n_records"),
        "level_1_in_file": p.get("level_1_category"),
        "level_2_in_file": p.get("level_2_category"),
        "category_in_file": p.get("category"),
        "keyword_type": p.get("keyword_type"),
    }


# ---------------------------------------------------------------------------
# 核心：实际数据周期探测（通用机制）
# ---------------------------------------------------------------------------
def discover_latest_period(
    page,
    ctx,
    *,
    level_1: str,
    level_2: Optional[str],
    keyword_type: str,
    granularity: str = "month",  # 当前实现只支持 month（周度 rank 的发现机制后续接入）
    probe_max_months: int = 3,   # 最多向前试探几个月（保守值）
) -> dict:
    """在 UI 已处于关键词榜单页 + 类目/榜单已选好的前提下，执行：

    1. 打开时间选择器；
    2. 切换到指定粒度（month/week）；
    3. 读取 UI 当前可用月份（按从新到旧）；
    4. 从最新候选 UI 月份开始，依次真实导出：
       - 选 UI 月份 → 关闭弹层 → 点导出 → 捕获下载 → 校验 Excel → 读 [日期范围]
       - 记录 (UI 月份, Excel period_start, Excel period_end) 映射
    5. 停止条件：
       - 已连续试探 N 个月份（默认 probe_max_months=3）
       - 或某次导出失败（不污染 SAVE_DIR）
    6. 返回：
       latest_period: {period_start, period_end, ui_month_cn}
       mapping_table: [{ui_month_cn, period_start, period_end, valid, error, file, file_hash, n_records}]
       best_stage_file: Path (最新有效周期的暂存文件)
    """
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    # 清空 staging（每次 discover 一次完整流程）
    for f in STAGE_DIR.glob("*.xlsx"):
        try:
            f.unlink()
        except Exception:
            pass

    # 按粒度分支
    if granularity == "week":
        return _discover_weeks(page, ctx, level_1=level_1, level_2=level_2,
                               keyword_type=keyword_type, probe_max=probe_max_months)
    return _discover_months(page, ctx, level_1=level_1, level_2=level_2,
                            keyword_type=keyword_type, probe_max=probe_max_months)


def _discover_months(page, ctx, *, level_1, level_2, keyword_type, probe_max) -> dict:
    """月度周期探测（原 discover_latest_period 逻辑）。"""
    if not _ensure_picker_in_month_view(page):
        step_fail("打开 picker", "无法进入月粒度选择状态")
    avail = read_available_months(page)
    if not avail:
        step_fail("读月份", "月份视图无可用月份")
    print(f"[discover] UI 可用月份（按从新到旧）: {avail}", flush=True)

    mapping = []
    best = None

    for idx, ui_cn in enumerate(avail[:probe_max]):
        if not _ensure_picker_in_month_view(page):
            mapping.append({"ui_month_cn": ui_cn, "valid": False, "error": "无法恢复月视图"})
            print(f"[discover] UI={ui_cn} 月视图恢复失败", flush=True)
            continue
        if not click_month_cell(page, ui_cn):
            mapping.append({"ui_month_cn": ui_cn, "valid": False, "error": "点击月份失败"})
            print(f"[discover] UI={ui_cn} 点击月份失败", flush=True)
            continue
        time.sleep(3)
        page.keyboard.press("Escape")
        time.sleep(8)

        stage_path = export_and_download(page, ctx)
        if stage_path is None:
            mapping.append({"ui_month_cn": ui_cn, "valid": False, "error": "导出或下载失败"})
            print(f"[discover] UI={ui_cn} 导出失败", flush=True)
            continue

        result = validate_excel(stage_path, expected_level_1=level_1,
                                 expected_level_2=level_2, expected_keyword_type=keyword_type)
        result["ui_month_cn"] = ui_cn
        mapping.append(result)

        if result.get("valid"):
            ps = result["period_start"]
            pe = result["period_end"]
            print(f"[discover] UI={ui_cn} → Excel period={ps}~{pe}, n={result['n_records']}, hash={result['file_hash'][:8]}", flush=True)
            if best is None or (ps and best["period_start"] and ps > best["period_start"]):
                best = {"ui_month_cn": ui_cn, "period_start": ps, "period_end": pe,
                        "stage_file": stage_path, "file_hash": result["file_hash"],
                        "n_records": result["n_records"]}
        else:
            print(f"[discover] UI={ui_cn} 校验失败: {result.get('error')}", flush=True)
        time.sleep(3)

    return {"ok": True, "available_ui_months": avail, "probed_count": len(mapping),
            "mapping_table": mapping, "latest_period": best}


def _discover_weeks(page, ctx, *, level_1, level_2, keyword_type, probe_max) -> dict:
    """周度周期探测：切换周视图 → 读可用周 → 逐周真实导出 → 读 Excel 实际日期。"""
    if not _ensure_picker_in_week_view(page):
        step_fail("打开 picker", "无法进入周粒度选择状态")
    avail = read_available_weeks(page)
    if not avail:
        step_fail("读周", "周视图无可用周")
    print(f"[discover] UI 可用周（按从新到旧）: {[w['label'] for w in avail]}", flush=True)

    mapping = []
    best = None

    for idx, wk in enumerate(avail[:probe_max]):
        if not _ensure_picker_in_week_view(page):
            mapping.append({"ui_week": wk["label"], "valid": False, "error": "无法恢复周视图"})
            print(f"[discover] UI={wk['label']} 周视图恢复失败", flush=True)
            continue
        if not click_week_cell(page, wk["start_day"]):
            mapping.append({"ui_week": wk["label"], "valid": False, "error": "点击周失败"})
            print(f"[discover] UI={wk['label']} 点击周失败", flush=True)
            continue
        time.sleep(3)
        page.keyboard.press("Escape")
        time.sleep(8)

        stage_path = export_and_download(page, ctx)
        if stage_path is None:
            mapping.append({"ui_week": wk["label"], "valid": False, "error": "导出或下载失败"})
            print(f"[discover] UI={wk['label']} 导出失败", flush=True)
            continue

        result = validate_excel(stage_path, expected_level_1=level_1,
                                 expected_level_2=level_2, expected_keyword_type=keyword_type)
        result["ui_week"] = wk["label"]
        result["ui_month_cn"] = wk["label"]  # 兼容 collection_manager 读取
        mapping.append(result)

        if result.get("valid"):
            ps = result["period_start"]
            pe = result["period_end"]
            print(f"[discover] UI={wk['label']} → Excel period={ps}~{pe}, n={result['n_records']}, hash={result['file_hash'][:8]}", flush=True)
            if best is None or (ps and best["period_start"] and ps > best["period_start"]):
                best = {"ui_month_cn": wk["label"], "period_start": ps, "period_end": pe,
                        "stage_file": stage_path, "file_hash": result["file_hash"],
                        "n_records": result["n_records"]}
        else:
            print(f"[discover] UI={wk['label']} 校验失败: {result.get('error')}", flush=True)
        time.sleep(3)

    return {"ok": True, "available_ui_weeks": [w["label"] for w in avail],
            "available_ui_months": [w["label"] for w in avail],  # 兼容字段
            "probed_count": len(mapping), "mapping_table": mapping, "latest_period": best}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="TikTok 关键词榜单导出（通用采集 + 实际数据周期判定）")
    ap.add_argument("--level1", required=True)
    ap.add_argument("--level2", default=None)
    ap.add_argument("--keyword-type", default="热门搜索关键词",
                    help="热门搜索关键词 / 飙升关键词 / 高潜力关键词")
    ap.add_argument("--granularity", default=None,
                    help="数据粒度: month/week（默认按 keyword_type 自动推断）")
    ap.add_argument("--probe-max-months", type=int, default=3,
                    help="从最新 UI 候选周期向前试探的最大次数")
    ap.add_argument("--no-save", action="store_true",
                    help="只探测不落盘到 SAVE_DIR（调试用）")
    args = ap.parse_args()

    level_1 = args.level1
    level_2 = args.level2
    keyword_type = args.keyword_type
    probe_max = max(1, args.probe_max_months)
    # 粒度推断: 高潜力关键词→week, 其他→month
    _GRAN_MAP = {"高潜力关键词": "week", "飙升关键词": "month", "热门搜索关键词": "month"}
    granularity = args.granularity or _GRAN_MAP.get(keyword_type, "month")

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    result = {"ok": False, "level_1_category": level_1, "level_2_category": level_2,
              "keyword_type": keyword_type}

    try:
        with sync_playwright() as p:
            # STAGE_DIR 必须先存在，downloads_path 要求目录已创建
            STAGE_DIR.mkdir(parents=True, exist_ok=True)
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE, channel="chrome", headless=False,
                viewport={"width": 1680, "height": 950}, locale="zh-CN",
                accept_downloads=True,
                downloads_path=str(STAGE_DIR),  # Chrome 下载直接落盘到 STAGE_DIR（不依赖 dl.save_as）
                args=["--disable-blink-features=AutomationControlled",
                      "--no-first-run", "--no-default-browser-check"],
                ignore_default_args=["--enable-automation"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            safe_goto(page, BASE, 6)
            time.sleep(4)
            if "account/login" in page.url or "/login" in page.url:
                ctx.close()
                print(json.dumps({"ok": False, "error": "需要登录"}, ensure_ascii=False))
                sys.exit(3)
            print("[step] 登录 ok", flush=True)

            safe_goto(page, BASE, 5)
            safe_goto(page, KEYWORD_URL, 8)
            time.sleep(6)
            if "account/login" in page.url:
                ctx.close()
                print(json.dumps({"ok": False, "error": "需要登录"}, ensure_ascii=False))
                sys.exit(3)

            tab = page.get_by_role("tab", name=keyword_type, exact=True)
            if not wait_visible(page, tab, 60):
                step_fail("切换榜单类型", f"找不到tab: {keyword_type}")
            tab.first.click()
            time.sleep(4)
            dismiss_chat_popup(page)

            pick_cascader_item(page, level_1, level_2)

            # 核心：通用周期探测
            discovery = discover_latest_period(
                page,
                ctx,
                level_1=level_1,
                level_2=level_2,
                keyword_type=keyword_type,
                granularity=granularity,
                probe_max_months=probe_max,
            )
            result.update(discovery)

            # 落盘到 SAVE_DIR（最新有效周期的文件）
            best = discovery.get("latest_period")
            if best:
                stage_file: Path = best["stage_file"]
                if not args.no_save:
                    final_name = f"{stage_file.name}"  # 保留原文件名（带时间戳）
                    final_path = SAVE_DIR / final_name
                    if final_path.exists():
                        # 同名冲突时加一个时间戳后缀
                        final_path = SAVE_DIR / f"{stage_file.stem}_{int(time.time())}{stage_file.suffix}"
                    shutil.copy2(stage_file, final_path)
                    result["final_file"] = str(final_path)
                    result["final_filename"] = final_path.name
                    result["final_size_bytes"] = final_path.stat().st_size
                    print(f"[step] 已保存最新周期文件: {final_path}", flush=True)

            result["ok"] = True
            ctx.close()
    except SystemExit:
        raise
    except Exception as e:
        step_fail("执行异常", f"{type(e).__name__}: {str(e)[:500]}")

    RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()