# -*- coding: utf-8 -*-
"""TikTok 关键词榜单采集脚本（第一阶段主入口）。

用法:
  python scripts/collect_tiktok.py                     # 导入原始目录全部导出文件(默认)
  python scripts/collect_tiktok.py --file <xlsx路径>    # 导入指定文件
  python scripts/collect_tiktok.py --mode browser       # 浏览器导出(确定性Playwright)→ 导入
  python scripts/collect_tiktok.py --db <db路径>         # 指定库（默认 database/opportunity.db）

说明: 原始导出文件只读不改，历史快照只增不覆盖；重复导入自动跳过(自然键去重)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许直接 `python scripts/collect_tiktok.py` 运行（把项目根加入模块路径）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.database import open_db
from app.models import KeywordRecord  # noqa: F401  (确保模型注册/便于调试)
from app.tiktok.collector import (
    LoginRequiredError,
    import_all,
    import_export_file,
    run_browser_export,
)


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文安全输出
    except Exception:
        pass


def _print_reports(reports: list[dict]) -> int:
    total_ins = total_dup = 0
    errors = 0
    for r in reports:
        if r.get("error"):
            print(f"  [失败] {r['file']}: {r['error']}")
            errors += 1
            continue
        print(
            f"  [入库] {r['file']}\n"
            f"          周期 {r['period_start']} ~ {r['period_end']} ({r['period_type']})\n"
            f"          类目[{r['category']}] 榜单[{r['keyword_type']}]\n"
            f"          解析 {r['n_records']} 条 → 新增 {r['inserted']}，重复跳过 {r['skipped_duplicates']}"
        )
        total_ins += r["inserted"]
        total_dup += r["skipped_duplicates"]
    if not errors:
        print(f"\n完成: 新增 {total_ins} 条，重复跳过 {total_dup} 条。")
    else:
        print(f"\n完成(部分失败): 新增 {total_ins} 条，跳过 {total_dup} 条，失败 {errors} 个文件。")
    return errors


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()

    ap = argparse.ArgumentParser(description="TikTok 关键词榜单 → SQLite（第一阶段）")
    ap.add_argument("--mode", choices=["import", "browser"], default="import",
                    help="import=导入已导出文件(默认)；browser=浏览器自动化导出后导入")
    ap.add_argument("--file", default=None, help="指定单个导出文件(import 模式)")
    ap.add_argument("--market", default=None, help=f"市场，默认 {config.DEFAULT_MARKET}")
    ap.add_argument("--category", default=None, help=f"榜单类目原文，默认 {config.DEFAULT_CATEGORY}")
    ap.add_argument("--level1", dest="level_1", default=None,
                    help="规范化一级类目（如 时尚配件）；缺省=category")
    ap.add_argument("--level2", dest="level_2", default=None,
                    help="规范化二级类目（如 平价饰品）；一级数据不传")
    ap.add_argument("--keyword-type", default=None,
                    help=f"榜单类型，默认 {config.DEFAULT_KEYWORD_TYPE}")
    ap.add_argument("--db", default=None, help="SQLite 路径，默认 database/opportunity.db")
    ap.add_argument("--probe-max-months", type=int, default=3,
                    help="browser 模式：从最新 UI 月份向前探测的最大次数（实际数据周期判定）")
    ap.add_argument("--no-save", action="store_true",
                    help="browser 模式：只探测不落盘到 SAVE_DIR（调试用）")
    args = ap.parse_args()

    # 先确认库可写、表结构就绪
    conn, _kw, _opp = open_db(args.db)
    conn.close()

    kwargs = dict(market=args.market, category=args.category,
                  keyword_type=args.keyword_type,
                  level_1_category=args.level_1, level_2_category=args.level_2)

    if args.mode == "browser":
        print("[1/3] 浏览器导出 + 实际数据周期探测...")
        try:
            result = run_browser_export(
                level_1=args.level_1 or kwargs.get("level_1_category"),
                level_2=args.level_2,
                keyword_type=args.keyword_type or config.DEFAULT_KEYWORD_TYPE,
                probe_max_months=args.probe_max_months,
                no_save=args.no_save,
            )
        except LoginRequiredError as e:
            print(f"需要手动登录: {e}")
            return 3
        # 打印探测映射表
        print(f"  UI 可用月份: {result.get('available_ui_months')}")
        print(f"  探测次数: {result.get('probed_count')}")
        for row in result.get("mapping_table", []):
            ui = row.get("ui_month_cn")
            if row.get("valid"):
                print(f"    UI={ui} → Excel period={row['period_start']}~{row['period_end']} (n={row['n_records']}, hash={row['file_hash'][:8]})")
            else:
                print(f"    UI={ui} → 失败: {row.get('error')}")
        latest = result.get("latest_period") or {}
        print(f"  最新实际周期: {latest.get('period_start')}~{latest.get('period_end')} (UI={latest.get('ui_month_cn')})")
        if not latest:
            print("  没有任何有效导出周期，无法入库。")
            return 2
        final_file = result.get("final_file")
        print(f"  最终文件: {final_file}")
        if args.no_save:
            print("[2/3] --no-save 模式，跳过正式落盘；不导入。")
            return 0
        print("[2/3] 导入最新周期文件...")
        # 使用 latest_period 的真实周期作为入库血缘（而非依赖 UI 选择）
        kwargs_latest = dict(kwargs)
        # 不强制覆盖 period_start/end——parser 也会读 Excel 元信息；这里仅保证 level1/level2 一致
        reports = [import_export_file(final_file, args.db, **kwargs_latest)]
        print("[3/3] 完成。")
    elif args.file:
        reports = [import_export_file(args.file, args.db, **kwargs)]
    else:
        print(f"扫描原始目录: {config.DATA_RAW_TIKTOK_THAILAND}")
        reports = import_all(raw_dir=config.DATA_RAW_TIKTOK_THAILAND, db_path=args.db, **kwargs)

    return _print_reports(reports)


if __name__ == "__main__":
    sys.exit(main())
