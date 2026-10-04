# -*- coding: utf-8 -*-
"""TikTok 关键词榜单整行采集器（多榜单合并版，Python + Playwright）

与 tiktok_page_top20.py（逐字段排序 TOP20）不同：
- 不点击排序器，直接抓取当前榜单表格每一行的完整指标（整行提取）。
- 支持一次采集多个榜单（--source-types hot,rising）：
  月度热门搜索关键词 / 月度飙升关键词
- 每个榜单按默认排序、每页 page_size 行、只取第一页。
- 多榜单结果按 keyword 去重合并，只保留一行（热门榜优先，飙升榜补充）。
- 每行含 7 指标：keyword / rank / 搜索量 / 商品点击数 / SKU销售指数 /
  在售商品 / CTR指数 / CTOR评分。**不采集 Price（平均价格）**，保留 CTR。
- 输出 TXT：data/TH/<L1>/<L2>/monthly/TH_<L1>_<L2>_月度_热门+飙升__fullrow.txt

登录态：
- 复用已有 Chrome Persistent Profile 登录态（PROFILE）。
- 登录态过期时暂停并提示用户手动登录，不要自动重试（防封号）。

用法:
  python scripts/collect/tiktok_page_fullrow.py --level1 美妆个护 --level2 美妆 \
      --period 2026-08 --page-size 30 --source-types hot,rising
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# 项目根入 path（兼容直接运行本脚本）
_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.collect.tiktok_page_top20 import (
    PROJECT_ROOT, PROFILE, BASE_URL, KEYWORD_URL,
    safe_goto, dismiss_chat_popup, check_login,
    switch_ranking_tab, pick_cascader_item,
    _ensure_picker_in_month_view, read_available_months, click_month_cell,
    read_date_range, get_current_category, get_current_tab,
    get_header_column_map, normalize_text,
    save_txt_atomic,
)

# 榜单类型映射：--source-types 取值 -> 页面 tab 名称
SOURCE_TABS = {
    "hot": "热门搜索关键词",
    "rising": "飙升关键词",
}

# 整行抓取的目标列：TXT 字段名 -> 页面表头候选。
# 注意：**不采集 Price（平均价格）**；保留 CTR 与全部 6 个评分/展示指标。
FULL_ROW_FIELDS = [
    ("排名",       ["排名"]),
    ("关键词",     ["关键词"]),
    ("搜索量",     ["搜索量"]),
    ("商品点击数", ["商品点击指数", "商品点击数"]),
    ("SKU销售指数", ["SKU销量指数", "SKU 销量指数", "SKU销售指数", "SKU 销售指数"]),
    ("在售商品",   ["在售商品"]),
    ("CTR指数",    ["CTR指数", "CTR 指数"]),
    ("CTOR评分",   ["CTOR评分", "CTOR 评分"]),
]

# 入库/分析侧使用的指标中文名（不含 Price）
METRIC_FIELDS = ["搜索量", "商品点击数", "SKU销售指数", "在售商品", "CTR指数", "CTOR评分"]

CN_MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
             "七月", "八月", "九月", "十月", "十一月", "十二月"]


def set_page_size(page, target: int = 30, max_wait: float = 6.0) -> bool:
    """尝试把分页器的每页条数切换为 target（默认 30）。

    TikTok Seller Center 使用 core/pulse 组件（非标准 arco），分页条数选择器
    需先点击展开，选项在全局 portal 中，文本形如 "30/Page"。
    找不到时返回 False，调用方按当前页面实际行数继续（不视为致命错误）。
    """
    # 1) 找到分页区的条数选择器并点击展开下拉
    try:
        clicked = page.evaluate("""() => {
            const pagArea = document.querySelector('[class*="pagination"], .arco-pagination, .theme-arco-pagination');
            if (!pagArea) return false;
            const sel = pagArea.querySelector('[class*="select"], .arco-select, [class*="page-size"], [class*="pagesize"]');
            if (!sel) return false;
            sel.click();
            return true;
        }""")
        if not clicked:
            return False
    except Exception:
        return False

    time.sleep(1.0)

    # 2) 在全局 portal 中查找叶子选项（children.length===0），
    #    用数字提取匹配，避免误点父容器（如 "20/Page30/Page"）
    try:
        result = page.evaluate("""(target) => {
            const popup = document.querySelector('[class*="select-popup"], [class*="select-dropdown"], [class*="select-list"], .arco-select-popup, .arco-select-dropdown');
            if (!popup) return {found: false};
            var candidates = Array.from(popup.querySelectorAll('[class*="option"], [role="option"], li, div, span'))
                .filter(function(el) { return el.children.length === 0; });
            var texts = candidates.map(function(el) { return (el.textContent || '').trim(); }).filter(function(t) { return t; }).slice(0, 10);
            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                var txt = (el.textContent || '').trim();
                if (!txt) continue;
                var nums = txt.match(/\\d+/g);
                if (nums && nums.indexOf(String(target)) !== -1) {
                    el.click();
                    return {found: true, clicked: true, text: txt, all_texts: texts};
                }
            }
            return {found: true, clicked: false, all_texts: texts};
        }""", target)
        if result and result.get("clicked"):
            print(f"[OK] set_page_size: 已切换到 {target}/页（选项: {result.get('text', '')}）")
            time.sleep(1.5)
            return True
    except Exception:
        pass

    return False


def extract_full_rows(page, col_indices: dict, max_rows: int = 30) -> list:
    """从表格提取整行数据（兼容 arco / core / pulse 组件）。"""
    result = page.evaluate(
        """({cols, max}) => {
            const counts = {
                tbody_tr: document.querySelectorAll('tbody tr').length,
                core_tr: document.querySelectorAll('tr.core-table-tr').length,
                arco_tr: document.querySelectorAll('tr.arco-table-tr').length,
                any_tr: document.querySelectorAll('tr').length,
                tables: document.querySelectorAll('table').length,
                tbodys: document.querySelectorAll('tbody').length,
            };

            let rows = null;
            let used = '';
            for (const [sel, name] of [
                ['tbody tr', 'tbody_tr'],
                ['tr.core-table-tr', 'core_tr'],
                ['tr.arco-table-tr', 'arco_tr'],
                ['table tr', 'table_tr'],
                ['tr', 'any_tr'],
            ]) {
                const r = document.querySelectorAll(sel);
                const dataRows = Array.from(r).filter(x => !x.closest('thead'));
                if (dataRows.length > 0) {
                    rows = dataRows;
                    used = name + '(' + dataRows.length + ')';
                    break;
                }
            }

            if (!rows) {
                return {rows: [], counts, used: 'NONE'};
            }

            const out = [];
            for (let i = 0; i < Math.min(rows.length, max); i++) {
                const cells = rows[i].querySelectorAll('td');
                if (cells.length < 2) continue;
                const row = {rank: i + 1};
                for (const [name, idx] of Object.entries(cols)) {
                    row[name] = (cells[idx]?.textContent || '').trim();
                }
                out.push(row);
            }
            return {rows: out, counts, used};
        }""",
        {"cols": col_indices, "max": max_rows}
    )
    if isinstance(result, dict):
        counts = result.get('counts', {})
        used = result.get('used', '')
        print(f"  [DEBUG extract] 选择器诊断: {counts}, 使用: {used}")
        return result.get('rows', [])
    return result


def merge_by_keyword(buckets: dict[str, list[dict]]) -> list[dict]:
    """多榜单合并，按 keyword 去重。

    顺序：先出现的榜单（热门榜）优先；后出现的榜单补充新词。
    同一关键词只保留一行（首次出现的那行）。
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    for source_name, rows in buckets.items():
        for r in rows:
            kw = (r.get("关键词") or "").strip()
            if not kw:
                continue
            if kw not in merged:
                merged[kw] = dict(r)
                merged[kw]["_source"] = source_name
                order.append(kw)
    return [merged[k] for k in order]


def build_fullrow_txt(date_start, date_end, rows, level1_label, level2_label,
                      col_order, source_label="热门+飙升") -> str:
    lines = [f"TikTok 泰国｜{level1_label}｜{level2_label}｜{source_label}",
             f"日期：{date_start} - {date_end}",
             "=" * 50]
    header = "|".join(col_order)
    lines.append(header)
    for r in rows:
        vals = [str(r.get(c, "") or "") for c in col_order]
        # 关键词中避免出现 |（泰文一般没有，但防御）
        vals[1] = vals[1].replace("|", " ")
        lines.append("|".join(vals))
    return "\n".join(lines)


def _resolve_header_indices(page, required=("排名", "关键词")) -> dict | None:
    """建立表头列映射；关键词列缺失或任一指标列缺失时返回 None。"""
    col_map = get_header_column_map(page)
    col_indices = {}
    missing = []
    for fname, variants in FULL_ROW_FIELDS:
        idx = None
        for v in variants:
            if normalize_text(v) in col_map:
                idx = col_map[normalize_text(v)]
                break
        if idx is None:
            missing.append(fname)
        else:
            col_indices[fname] = idx
    if "关键词" not in col_indices or missing:
        return None
    return col_indices


def collect_one_tab(page, tab_name: str, col_indices: dict, page_size: int) -> list[dict]:
    """切换到指定榜单并抓取第一页整行。失败返回 []。"""
    print(f"\n[榜单] 切换到 {tab_name}")
    if not switch_ranking_tab(page, tab_name):
        print(f"[ERROR] 无法切换到 {tab_name}")
        return []
    time.sleep(4)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    time.sleep(3)
    # 每页条数设置为 page_size（尽力而为）
    ok = set_page_size(page, page_size)
    if not ok:
        print(f"[WARN] set_page_size 未能切换到每页 {page_size} 条，按页面默认行数继续")
    time.sleep(2)
    rows = extract_full_rows(page, col_indices, max_rows=page_size)
    print(f"  [OK] {tab_name}: 抓到 {len(rows)} 行")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TikTok 关键词榜单整行采集器（多榜单合并）")
    ap.add_argument("--country", default="泰国")
    ap.add_argument("--level1", default="美妆个护")
    ap.add_argument("--level2", default="美妆")
    ap.add_argument("--page-level1", default=None, help="页面级联中的 L1 名（默认=--level1；ALIAS 任务用页面实际名）")
    ap.add_argument("--page-level2", default=None, help="页面级联中的 L2 名（默认=--level2；ALIAS 任务用页面实际名）")
    ap.add_argument("--period", default="2026-08", help="周期，如 2026-08（用于标注）")
    ap.add_argument("--page-size", type=int, default=30, help="每页行数（默认30，只取第一页）")
    ap.add_argument("--source-types", default="hot,rising", help="榜单类型：hot,rising 逗号分隔")
    ap.add_argument("--output-dir", default=None, help="自定义输出根目录（默认 data/TH）")
    args = ap.parse_args(argv)

    country = args.country
    level_1 = args.level1
    level_2 = args.level2
    page_level_1 = args.page_level1 or level_1
    page_level_2 = args.page_level2 or level_2
    period = args.period
    page_size = max(10, min(args.page_size, 100))
    sources = [s.strip() for s in args.source_types.split(",") if s.strip() in SOURCE_TABS]
    if not sources:
        print(f"[ERROR] --source-types 无效: {args.source_types}（可选: hot,rising）")
        return 2
    source_label = "+".join(SOURCE_TABS[s] for s in sources).replace("关键词", "")
    source_label = "热门+飙升" if len(sources) == 2 else SOURCE_TABS[sources[0]]

    print("=" * 70)
    print("TikTok 关键词榜单整行采集器（多榜单合并）")
    print(f"目标: TH / {level_1} / {level_2} / 月度 / {source_label} / {period}")
    print(f"Profile: {PROFILE}")
    print("=" * 70)

    with sync_playwright() as p:
        print("\n[1] 启动浏览器")
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, channel="chrome", headless=False,
            viewport={"width": 1680, "height": 950}, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
            ignore_default_args=["--enable-automation"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("[2] 打开页面")
        safe_goto(page, BASE_URL, 6)
        time.sleep(4)
        if not ctx.pages:
            print("[ERROR] 浏览器无可用页面")
            ctx.close()
            return 3
        page = ctx.pages[0]
        if not check_login(page):
            ctx.close()
            print("[ERROR] 登录态已过期。请手动登录 TikTok Seller Center 后重跑（不要自动重试，防封号）。")
            return 3
        print("[OK] 登录状态正常")

        safe_goto(page, KEYWORD_URL, 8)
        time.sleep(8)
        dismiss_chat_popup(page)
        print(f"[DEBUG] 当前URL: {page.url}")

        try:
            page.wait_for_selector(".theme-arco-table, [role='tab']", timeout=30000)
            print("[OK] 页面元素已加载")
        except Exception:
            print("[WARN] 等待页面元素超时，继续尝试...")

        # 第一个榜单用于初始化类目/日期；后续榜单只切 tab
        first_tab = SOURCE_TABS[sources[0]]
        print(f"[3] 切换到 {first_tab}")
        if not switch_ranking_tab(page, first_tab):
            ctx.close()
            return 1

        print(f"[4] 选择类目(页面): {page_level_1} -> {page_level_2}")
        if not pick_cascader_item(page, page_level_1, page_level_2):
            ctx.close()
            print("[ERROR] 类目选择失败（请确认页面类目名称与映射一致）")
            return 1

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

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(8)

        date_start, date_end = read_date_range(page)
        if not date_start or not date_end:
            ctx.close()
            print("[ERROR] 无法读取页面日期范围")
            return 1
        print(f"[OK] 实际日期范围: {date_start} ~ {date_end}")

        actual_category = get_current_category(page)
        actual_tab = get_current_tab(page)
        print(f"[OK] 当前类目: {actual_category}, 当前榜单: {actual_tab}")

        print("[6] 建立表头列映射")
        col_indices = _resolve_header_indices(page)
        if col_indices is None:
            ctx.close()
            print("[ERROR] 列映射缺失（需含: 排名/关键词/搜索量/商品点击数/SKU销售指数/在售商品/CTR指数/CTOR评分）")
            return 1
        print(f"[OK] 整行列索引: {col_indices}")

        # [7] 依次采集各榜单（每榜第一页 page_size 行）
        print(f"[7] 采集榜单: {[SOURCE_TABS[s] for s in sources]}（每页 {page_size} 行，只取第一页）")
        buckets: dict[str, list[dict]] = {}
        for s in sources:
            tab_name = SOURCE_TABS[s]
            rows = collect_one_tab(page, tab_name, col_indices, page_size)
            if rows:
                buckets[s] = rows

        # [8] 合并去重
        all_rows = merge_by_keyword(buckets)
        if not all_rows:
            print("[EMPTY] 该榜单/周期无数据（不视为失败）")
            all_rows = []
        print(f"[8] 合并去重: {sum(len(v) for v in buckets.values())} 原始行 → {len(all_rows)} 唯一关键词")

        # [9] 生成 TXT（覆盖旧文件）
        print(f"[9] 生成 TXT（共 {len(all_rows)} 行）")
        col_order = [f[0] for f in FULL_ROW_FIELDS]
        content = build_fullrow_txt(date_start, date_end, all_rows, level_1, level_2,
                                    col_order, source_label=source_label)
        filename = f"TH_{level_1}_{level_2}_月度_{source_label}__fullrow.txt"
        if args.output_dir:
            output_base = Path(args.output_dir)
        else:
            output_base = PROJECT_ROOT / "data" / "TH" / level_1 / level_2
        filepath = output_base / "monthly" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        print(f"[OK] 目标文件: {filepath}")
        if not save_txt_atomic(content, filepath):
            ctx.close()
            return 1
        print(f"[OK] 文件已保存: {filepath}")

        if filepath.exists():
            print(f"[OK] 文件存在, 大小: {filepath.stat().st_size} bytes")
        else:
            print("[ERROR] 文件不存在!")

        ctx.close()
        print("=" * 70)
        print("整行采集成功!")
        print(f"行数: {len(all_rows)} | 文件: {filepath}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
