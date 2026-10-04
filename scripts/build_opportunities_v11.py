# -*- coding: utf-8 -*-
"""V1.1 商品机会构建脚本（Python：聚类 → 评分 → 最终 ≤9；不调用 AI）。

流程:
    ai_semantic_results（success & PRODUCT，属 screening_v1 pass=1 top_rank<=80 候选）
      → 按 canonical_product_key Python 聚类
      → 组内取"最强关键词"V1 指标（禁止搜索量相加）
      → Opportunity Score = 0.35×Demand + 0.30×Intent + 0.20×Gap + 0.15×Evidence(封顶)
      → 排序去重 → 最终 ≤ FINAL_OPPORTUNITY_LIMIT(9)（不足 9 不补，禁止降质凑数）
      → 写入 product_opportunities（每 run 一批）

用法:
  python scripts/build_opportunities_v11.py            # 构建并入库 + 打印最终结果
  python scripts/build_opportunities_v11.py --no-store # 只计算预览
  python scripts/build_opportunities_v11.py --report-only  # 读最近批次打印报告(不重算)

说明: 本脚本不做任何 DeepSeek 调用；先运行 scripts/analyze_semantics.py 完成语义。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许直接运行（项目根加入模块路径）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import config
from app.analysis.opportunity_v11 import build_opportunities
from app.database import OpportunityV11Repo, open_db


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _label(v) -> str:
    """把 0~1 数值翻译成可解释档位（仅供报告文案，不参与计算）。"""
    if v is None:
        return "缺失"
    if v >= 0.75:
        return "高"
    if v >= 0.5:
        return "中"
    return "较低"


def _explain(o) -> str:
    """可解释推荐原因（Python 模板；禁止"保证爆款/100%准确/虚构销量"）。"""
    d, i, s, g = o.best_demand_pct, o.best_purchase_intent, o.best_supply_pct, o.best_opportunity_gap
    parts = []
    if d is not None and d >= 0.6:
        parts.append("TikTok 搜索需求较高")
    if i is not None and i >= 0.6:
        parts.append("购买意图较强")
    if g is not None and g < -0.1:
        parts.append("供给相对需求明显过剩")
    elif g is not None and g < 0.1:
        parts.append("供给与需求大体相当")
    elif g is not None:
        parts.append("供给相对需求未明显过剩")
    if o.keyword_count >= 2:
        parts.append(f"由 {o.keyword_count} 个相关关键词共同支撑(证据有限增强)")
    if not parts:
        parts.append("指标以当期快照为准，需结合后续周期复核")
    return "；".join(parts) + "。"


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()

    ap = argparse.ArgumentParser(description="V1.1 商品机会（聚类→评分→≤9）")
    ap.add_argument("--market", default="TH")
    ap.add_argument("--no-store", action="store_true", help="只计算预览，不写 product_opportunities")
    ap.add_argument("--report-only", action="store_true", help="只打印最近入库批次报告")
    ap.add_argument("--top-rank-max", type=int, default=config.AI_CANDIDATE_LIMIT)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn, _kw, _opp = open_db(args.db)

    if args.report_only:
        rows = OpportunityV11Repo(conn).latest(args.market, final_only=True)
        print(f"最近入库最终商品机会（product_opportunities，{len(rows)} 个）：")
        for r in rows:
            print(f"  #{r['final_rank']} {r['canonical_product_name']} "
                  f"score={r['opportunity_score']:.3f} keywords={r['keyword_count']}")
        conn.close()
        return 0

    result = build_opportunities(conn, market=args.market,
                                 top_rank_max=args.top_rank_max, store=not args.no_store)
    st = result["stats"]
    print(f"V1.1 商品机会归一化（run={result['run_id'] or '无'}）")
    print(f"匹配候选(语义成功∩V1候选): {st['matched_candidates']} | "
          f"机会组: {st['groups']}（评分 {st['scored_groups']} / 跳过 {st['skipped_groups']}）| "
          f"最终 ≤{config.FINAL_OPPORTUNITY_LIMIT}: {st['final_count']} 个")
    if result["skipped"]:
        print(f"跳过(安全失败，不崩): {len(result['skipped'])} 项")
        for kw, why in result["skipped"][:10]:
            print(f"  - {kw}: {why}")

    final = result["final"]
    print(f"\n=== 最终商品机会（最多 {config.FINAL_OPPORTUNITY_LIMIT} 个，"
          f"不足不补：{len(final)} 个）===")
    for o in final:
        src = json.loads(o.source_keywords)
        print(f"\n[{o.final_rank}] {o.canonical_product_name}  (key={o.canonical_product_key})")
        print(f"   机会分 {o.opportunity_score:.3f} = 需求 {o.demand_component:.3f} + "
              f"意图 {o.intent_component:.3f} + 供需 {o.gap_component:.3f} + "
              f"证据 {o.evidence_component:.3f}")
        print(f"   市场需求:{_label(o.best_demand_pct)} 购买意图:{_label(o.best_purchase_intent)} "
              f"供给:{_label(o.best_supply_pct)} 供需机会:{_label(o.best_opportunity_gap)}")
        print(f"   代表关键词: {o.best_keyword}")
        print(f"   来源关键词({o.keyword_count}): {' / '.join(src[:8])}"
              + (" …" if len(src) > 8 else ""))
        print(f"   推荐原因: {_explain(o)}")

    if args.no_store:
        print("\n(no-store 预览：未写入 product_opportunities)")
    else:
        print(f"\n已写入 product_opportunities {st['stored']} 行（run={result['run_id']}）。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
