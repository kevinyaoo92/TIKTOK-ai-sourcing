# -*- coding: utf-8 -*-
"""市场机会分析脚本（第一阶段主输出）。

用法:
  python scripts/analyze_opportunities.py                 # 默认: TH / 时尚配件 / 热门搜索关键词 / 月
  python scripts/analyze_opportunities.py --limit 30
  python scripts/analyze_opportunities.py --explain <关键词>   # 看某词完整中文理由
  python scripts/analyze_opportunities.py --replace-run        # 清理同基准旧批次后重写
  python scripts/analyze_opportunities.py --export-csv <路径>
  python scripts/analyze_opportunities.py --period-type week   # 有周快照后可切周口径

输出: 机会列表(降序) + 每条可解释理由(存 opportunity_data.reasons)。
评分全部为确定性 Python 计算；本脚本不调用任何 AI。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

# 允许直接 `python scripts/analyze_opportunities.py` 运行（把项目根加入模块路径）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.analysis import opportunity as opp
from app.database import open_db


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _fmt_num(v: Optional[float], nd: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:.{nd}f}"


def _print_table(recs, start: int = 1) -> int:
    i = start
    print("-" * 108)
    print(f"{'#':>3}  {'关键词':<36} {'上期→本期搜索量':>20} {'在售':>8} {'均价฿':>8} {'机会分':>7}  标签")
    print("-" * 108)
    for rec in recs:
        vol = (
            f"{_fmt_num(rec.search_volume_prev)}→{_fmt_num(rec.search_volume)}"
            if rec.search_volume is not None
            else "-"
        )
        print(
            f"{i:>3}. {str(rec.keyword)[:36]:<36} {vol:>20} "
            f"{_fmt_num(rec.on_sale_products):>8} {_fmt_num(rec.avg_price):>8} "
            f"{rec.opportunity_score:>7.1f}  {rec.label}"
        )
        i += 1
    print("-" * 108)
    return i


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()

    ap = argparse.ArgumentParser(description="市场机会计算（可解释评分，纯 Python）")
    ap.add_argument("--market", default=config.DEFAULT_MARKET)
    ap.add_argument("--category", default=config.DEFAULT_CATEGORY)
    ap.add_argument("--keyword-type", default=config.DEFAULT_KEYWORD_TYPE)
    ap.add_argument("--period-type", default=config.DEFAULT_PERIOD_TYPE)
    ap.add_argument("--limit", type=int, default=20, help="展示前 N 条（默认 20，0=全部）")
    ap.add_argument("--min-score", type=float, default=None, help="只展示机会分≥该值的词")
    ap.add_argument("--explain", default=None, metavar="关键词", help="打印该词全部中文理由")
    ap.add_argument("--export-csv", default=None, help="本次结果导出 CSV")
    ap.add_argument("--replace-run", action="store_true", help="入库前清理同基准旧批次")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn, _kw, _opp = open_db(args.db)
    result = opp.analyze(
        conn,
        market=args.market, category=args.category,
        keyword_type=args.keyword_type, period_type=args.period_type,
    )

    if not result["records"]:
        print(f"无数据可分析：{args.market}/{args.category}/{args.keyword_type}/"
              f"{args.period_type} 尚无快照。\n请先运行: python scripts/collect_tiktok.py")
        conn.close()
        return 1

    # --explain 是查询动作：直接打印理由，不入库
    if args.explain:
        target = args.explain.strip()
        hit = next((r for r in result["records"] if r.keyword == target), None)
        if hit is None:
            print(f"\n基准周期内未找到关键词: {target}")
        else:
            print(f"\n『{hit.keyword}』 机会分 {hit.opportunity_score:.1f}/100 → {hit.label}")
            for line in json.loads(hit.reasons):
                print(f"  - {line}")
        conn.close()
        return 0

    # 入库（默认追加历史批次；--replace-run 先清同基准旧批次）
    n_stored = opp.store(conn, result, replace_run=args.replace_run)
    conn.commit()

    print(
        f"分析口径: 市场[{result['market']}] 类目[{result['category']}] "
        f"榜单[{result['keyword_type']}] 粒度[{result['period_type']}]"
    )
    print(
        f"基准周期: {result['baseline']}（类目词数 {result['cohort_size']}）"
        f" | 上期(环比): {result['previous'] or '无——暂无历史快照'}"
    )

    rows = result["records"]
    if args.min_score is not None:
        rows = [r for r in rows if r.opportunity_score >= args.min_score]
    show = rows if args.limit == 0 else rows[: args.limit]

    print(f"\n机会列表（共 {len(rows)} 词，展示 {len(show)}）——为什么入选见 --explain")
    _print_table(show)
    print(f"已写入 opportunity_data {n_stored} 条（computed_at={result['records'][0].computed_at}）。")
    print("查看某词入选理由: python scripts/analyze_opportunities.py --explain <关键词>")

    if args.export_csv:
        path = args.export_csv
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["keyword", "opportunity_score", "label", "search_volume",
                        "search_volume_prev", "growth_rate", "avg_price",
                        "on_sale_products", "reasons"])
            for rec in result["records"]:
                w.writerow([rec.keyword, rec.opportunity_score, rec.label,
                            rec.search_volume, rec.search_volume_prev,
                            rec.growth_rate, rec.avg_price, rec.on_sale_products,
                            rec.reasons])
        print(f"已导出 CSV: {path}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
