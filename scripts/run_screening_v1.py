# -*- coding: utf-8 -*-
"""V1 初期关键词筛选脚本（keyword_data → 清洗 → 非商品过滤 → 通道 → 评分 → TopN）。

用法:
  python scripts/run_screening_v1.py                    # 默认口径: TH/时尚配件/热门搜索关键词/月/最新周期
  python scripts/run_screening_v1.py --top-n 80        # 候选数(默认 80, V1 假设值)
  python scripts/run_screening_v1.py --replace-run     # 入库前清理同基准旧筛选批次
  python scripts/run_screening_v1.py --no-store        # 只计算不入库(预览)
  python scripts/run_screening_v1.py --print-top 20

规则与权重全部配置在 app/config.py（V1_WEIGHTS / V1_PURCHASE_INTENT_WEIGHTS /
V1_LOW_DEMAND_PCT / V1_HIGH_INTENT_CTOR_PCT / V1_TOP_N / 非商品黑名单），
可用环境变量/.env 覆盖。纯 Python 计算，不调用 AI。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行（项目根加入模块路径）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import config
from app.analysis.screening import V1Screener
from app.database import ScreeningRepo, open_db


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _pct(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def _fmt(v, nd=0) -> str:
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if not (isinstance(v, float) and v == int(v)) else str(int(v))


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()

    ap = argparse.ArgumentParser(description="V1 初期关键词筛选层")
    ap.add_argument("--market", default=config.DEFAULT_MARKET)
    ap.add_argument("--category", default=config.DEFAULT_CATEGORY)
    ap.add_argument("--level1", dest="level_1", default=None,
                    help="规范化一级类目(如 时尚配件)；提供后按标准化层读取")
    ap.add_argument("--level2", dest="level_2", default=None,
                    help="规范化二级类目(如 平价饰品)；不传=一级数据(level_2 IS NULL)")
    ap.add_argument("--keyword-type", default=config.DEFAULT_KEYWORD_TYPE)
    ap.add_argument("--period-type", default=config.DEFAULT_PERIOD_TYPE)
    ap.add_argument("--period-start", default=None, help="指定基准周期(默认最新)")
    ap.add_argument("--top-n", type=int, default=config.V1_TOP_N,
                    help=f"候选数量(默认 {config.V1_TOP_N}；V1 假设值，未经回测)")
    ap.add_argument("--print-top", type=int, default=20, help="打印前 N 名(0=全部)")
    ap.add_argument("--no-store", action="store_true", help="只计算，不写 screening_v1")
    ap.add_argument("--replace-run", action="store_true", help="入库前清理同基准旧批次")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn, _kw, _opp = open_db(args.db)
    if args.replace_run and not args.no_store:
        # 清理同基准(口径+周期)的旧筛选批次
        from app.database import KeywordRepo as _KR
        periods = _KR(conn).snapshot_periods(
            args.market, args.category, args.keyword_type, args.period_type)
        if periods:
            base = args.period_start or periods[-1]
            deleted = ScreeningRepo(conn).delete_run(
                args.market, args.category, args.keyword_type, args.period_type, base)
            print(f"[replace-run] 已清理 {base} 的旧筛选记录 {deleted} 条")

    screener = V1Screener(
        conn,
        market=args.market, category=args.category, keyword_type=args.keyword_type,
        period_type=args.period_type, period_start=args.period_start,
        level_1_category=args.level_1, level_2_category=args.level_2,
        top_n=args.top_n, store_results=not args.no_store,
    )
    result = screener.run()

    ctx = result["ctx"]
    print(f"筛选口径: 市场[{ctx['market']}] 类目[{ctx['category']}] "
          f"榜单[{ctx['keyword_type']}] 粒度[{ctx['period_type']}] 周期[{ctx['period_start']}]")
    print(f"样本 {result['cohort_size']} 词 | 清洗后 {result['cleaned']} | "
          f"入库 {result['stored'] if not args.no_store else '(no-store)'}")
    print(f"淘汰统计: {result['reject_stats']}")
    print(f"通过: 主通道 {result['main']} + 保护通道 {result['protection']} = {result['passed']} 词")
    print(f"Top N 候选: {result['top_n_cap']} 词（配置 top_n={result['top_n_config']}，"
          f"V1 假设值未经回测）")
    if args.no_store:
        print("(no-store 预览：未写入 screening_v1)")

    show = result["candidates"][: args.print_top] if args.print_top else result["candidates"]
    print(f"\n候选 Top {len(show)}（demand=搜索量分位 gap=Demand-Supply intent=0.7CTOR+0.3SKU）")
    print("-" * 120)
    for d in show:
        print(
            f"{d.top_rank:>3}. {str(d.keyword)[:30]:<30} "
            f"demand {_pct(d.demand_pct):>5} supply {_pct(d.supply_pct):>5} "
            f"gap {_pct(d.opportunity_gap):>6} intent {_pct(d.purchase_intent):>5} "
            f"score {d.opportunity_score:>7.3f} [{d.channel}]"
        )
    print("-" * 120)
    print("查看完整记录: sqlite3 database/opportunity.db "
          "\"SELECT keyword, passed, channel, reject_reason, demand_pct, supply_pct, "
          "opportunity_gap, ctor_pct, sku_pct, purchase_intent, opportunity_score, top_rank "
          "FROM screening_v1 WHERE run_id=(SELECT MAX(run_id) FROM screening_v1);\"")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
