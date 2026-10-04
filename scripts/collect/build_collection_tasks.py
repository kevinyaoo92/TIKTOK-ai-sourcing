# -*- coding: utf-8 -*-
"""从官方类目 TXT 生成 TikTok 采集任务清单。

用法：
  python scripts/collect/build_collection_tasks.py [--file 类目文件路径] [--market TH]
      [--period month|week] [--tab 热门搜索关键词|飙升关键词]
      [--level1 L1过滤] [--level2 L2过滤] [--dry-run] [--json 输出json路径]

本次默认只做「读取 + 解析 + 任务生成」，不启动真实 TikTok 页面采集。
--dry-run 表示只输出任务清单不采集（默认即 dry-run；真正接入定时采集时由
run_scheduled_collection.py 按清单执行，不在本脚本内发起页面操作）。

唯一类目来源：桌面「泰国TK官方类目.txt」（或 --file 显式指定），
类目名称原样保存，不翻译、不增删、不合并、不重新分类。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许直接以 python scripts/collect/build_collection_tasks.py 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.category_loader import load_catalog  # noqa: E402

DEFAULT_MARKET = "TH"
DEFAULT_PERIOD = "month"          # 沿用已验证配置：月度
DEFAULT_TAB = "热门搜索关键词"      # 沿用已验证配置：热门搜索关键词


def build_tasks(catalog, market: str, period: str, tab: str,
                filter_l1: str | None = None, filter_l2: str | None = None) -> list[dict]:
    """生成任务清单；支持按 L1/L2 过滤（用于 dry-run 单类目验证）。"""
    tasks = catalog.to_tasks(market=market, period=period, tab=tab)
    if filter_l1:
        tasks = [t for t in tasks if t["level1"] == filter_l1]
    if filter_l2:
        tasks = [t for t in tasks if t["level2"] == filter_l2]
    return tasks


def fmt_summary(catalog) -> list[str]:
    lines = [
        f"标题行: {catalog.title or '(无)'}",
        f"类目来源: {catalog.source}",
        f"L1 数量: {catalog.l1_count}",
        f"L2 总数量: {catalog.l2_total}",
        "各 L1 对应的 L2 数量:",
    ]
    for e in catalog.entries:
        lines.append(f"  - {e.level1}: {len(e.level2)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="读取官方类目 TXT 并生成采集任务清单（不启动真实采集）")
    ap.add_argument("--file", default=None, help="官方类目 TXT 路径（缺省用桌面唯一来源）")
    ap.add_argument("--market", default=DEFAULT_MARKET, help="市场代码，默认 TH")
    ap.add_argument("--period", choices=["month", "week"], default=DEFAULT_PERIOD,
                    help="周期，沿用已验证配置，默认 month")
    ap.add_argument("--tab", choices=["热门搜索关键词", "飙升关键词"], default=DEFAULT_TAB,
                    help="榜单，沿用已验证配置，默认 热门搜索关键词")
    ap.add_argument("--level1", default=None, help="只生成指定 L1 的任务（用于单类目 dry-run）")
    ap.add_argument("--level2", default=None, help="只生成指定 L2 的任务（用于单类目 dry-run）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅输出任务清单，不启动采集（默认行为即 dry-run）")
    ap.add_argument("--json", default=None, help="将任务清单写入 JSON 文件路径（可选）")
    args = ap.parse_args(argv)

    try:
        catalog = load_catalog(args.file)
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    print("=" * 60)
    for ln in fmt_summary(catalog):
        print(ln)

    # 校验：重复/空项/解析异常
    print("-" * 60)
    print("校验结果:")
    if catalog.warnings:
        for w in catalog.warnings:
            print(f"  [告警] {w}")
    else:
        print("  未发现重复 L1/L2、空名称或无法解析的行")

    dup_tasks = catalog.check_duplicate_tasks()
    if dup_tasks:
        for d in dup_tasks:
            print(f"  [告警] {d}")
    else:
        print("  未发现重复任务 (market+level1+level2)")

    tasks = build_tasks(catalog, args.market, args.period, args.tab,
                        args.level1, args.level2)

    print("-" * 60)
    print(f"任务清单（共 {len(tasks)} 条）:")
    for i, t in enumerate(tasks, start=1):
        print(f"  {i:>3}. market={t['market']} | level1={t['level1']} | "
              f"level2={t['level2']} | period={t['period']} | tab={t['tab']}")

    if args.level1 or args.level2:
        matched = len(tasks)
        total = catalog.l2_total
        print("-" * 60)
        print(f"过滤说明: 命中 {matched} / 全部 {total} 条（仅用于 dry-run 验证，本次不启动采集）")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"- 任务清单已写入: {out}")

    print("=" * 60)
    print("本次为 dry-run：只生成任务清单，未启动任何 TikTok 页面采集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
