# -*- coding: utf-8 -*-
"""V2 商品机会池构建脚本（后台全量 Pool；DeepSeek calls = 0，只用已有语义结果）。

用法:
  python scripts/build_opportunity_pool.py                 # 默认一级类目 + 无二级(全量)
  python scripts/build_opportunity_pool.py --level2 平价饰品
  python scripts/build_opportunity_pool.py --level2 平价饰品 --no-store   # 预览
  python scripts/build_opportunity_pool.py --top-n 0       # 0=不截断候选(全量通过词)

说明:
  - 四类市场源(level_1/level_2 × week/month)各自独立读取、独立筛选，不混源；
    周/月证据分别保存为 weekly_evidence / monthly_evidence，不发明综合公式。
  - 写入 product_opportunities_v2（幂等整表重建）；正式 product_opportunities(47) 不动。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import config
from app.analysis.pool import build_pool, fit_category_status
from app.database import open_db


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _diff_vs_v1(conn, records) -> dict:
    """与正式 product_opportunities(47, 一级月) 的 key 集差异。"""
    old = {r["canonical_product_key"] for r in conn.execute(
        "SELECT DISTINCT canonical_product_key FROM product_opportunities")}
    new_l1 = {r.canonical_product_key for r in records
              if r.level_2_category is None and json.loads(r.monthly_evidence)}
    old_scores = {r["canonical_product_key"]: r["opportunity_score"] for r in conn.execute(
        "SELECT canonical_product_key, opportunity_score FROM product_opportunities")}
    new_scores = {r.canonical_product_key: r.score for r in records
                  if r.level_2_category is None and json.loads(r.monthly_evidence)}
    added = sorted(new_l1 - old)
    gone = sorted(old - new_l1)
    common = old & new_l1
    # 排名变化 = 同 key 在 v1(v1.1 score) 与 v2(pool score) 中相对位次差
    old_rank = {k: i for i, k in enumerate(sorted(old_scores, key=old_scores.get, reverse=True), 1)}
    new_rank = {k: i for i, k in enumerate(sorted(new_scores, key=new_scores.get, reverse=True), 1)}
    moves = sorted(((old_rank[k] - new_rank[k], k) for k in common if k in old_rank and k in new_rank),
                   key=lambda x: abs(x[0]), reverse=True)
    return {"old_keys": len(old), "new_level1_month_keys": len(new_l1),
            "added": len(added), "gone": len(gone), "common": len(common),
            "added_examples": added[:8], "gone_examples": gone[:8],
            "biggest_rank_moves": [(k, old_rank[k], new_rank.get(k)) for _, k in moves[:5]]}


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()
    ap = argparse.ArgumentParser(description="V2 商品机会池（后台全量）")
    ap.add_argument("--market", default="TH")
    ap.add_argument("--level1", default="时尚配件")
    ap.add_argument("--level2", default=None, help="二级类目，逗号分隔多个；缺省=只一级")
    ap.add_argument("--top-n", type=int, default=None,
                    help="V1 候选截断(默认 config.V1_TOP_N=80 假设；0=不截断)")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--max-periods", type=int, default=1,
                    help="每(源,粒度)取最近 N 期证据(默认 1)")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    l2s = tuple(x.strip() for x in (args.level2 or "").split(",") if x.strip())
    conn, _kw, _opp = open_db(args.db)

    top_n = args.top_n
    if top_n == 0:
        top_n = 10 ** 9        # 0 = 全量通过词进入池(候选不截断)
    result = build_pool(conn, market=args.market, level_1=args.level1,
                        level_2_categories=l2s, top_n=top_n,
                        max_periods=args.max_periods, store=not args.no_store)
    st = result["stats"]
    print(f"=== V2 商品机会池（run={result['run_id']}；DeepSeek calls={st['semantic']['deepseek_calls']}）===")
    print("--- 输入数据源 ---")
    for s in st["sources"]:
        if s["n_periods"] == 0:
            print(f"  [{s['level_1']}/{s['level_2'] or '(一级)'} {s['period_type']}] 无数据(源缺失)")
        else:
            print(f"  [{s['level_1']}/{s['level_2'] or '(一级)'} {s['period_type']} "
                  f"{s['period_start']}~{s['period_end']}] 关键词 {s['records']} 条 → 候选 {s['screening_candidates']}")
    print("--- V1 筛选(合计) ---")
    sc = st["screening"]
    print(f"  原始 {sc['raw']} | 非商品过滤 {sc['reject_non_product']} | 低需求过滤 "
          f"{sc['reject_low_demand']} | 保护 {sc['protection']} | 通过 {sc['passed']} | 候选 {sc['candidates']}")
    print("--- AI 语义(未调用) ---")
    print(f"  命中已有语义 {st['semantic']['matched']} | missing_semantic "
          f"{st['semantic']['missing_semantic']} | DeepSeek calls {st['semantic']['deepseek_calls']}")
    print("--- 商品机会池 ---")
    p = st["pool"]
    print(f"  三级类目数 {p['level3_categories']} | canonical key 数 {p['canonical_keys']} | "
          f"机会数 {p['opportunities']} | 分类 {p['category_status']}")
    print("--- CORE/ADJACENT/EXCLUDE/REVIEW ---")
    cs = p["category_status"]
    print(f"  CORE={cs.get('CORE', 0)} ADJACENT={cs.get('ADJACENT', 0)} "
          f"EXCLUDE={cs.get('EXCLUDE', 0)} REVIEW={cs.get('REVIEW', 0)}")
    print(f"--- Top 20（score 降序；不截断成 9）---")
    for r in sorted(result["records"], key=lambda x: x.score, reverse=True)[:20]:
        w_ev = json.loads(r.weekly_evidence)
        m_ev = json.loads(r.monthly_evidence)
        w_sum = f"{len(w_ev)}期/{w_ev[0]['best_keyword'] if w_ev else '-'}"
        m_sum = f"{len(m_ev)}期/{m_ev[0]['best_keyword'] if m_ev else '-'}"
        print(f"  {r.opportunity_id} {r.canonical_product_name:<10}({r.canonical_product_key}) "
              f"[{r.level_2_category or '一级'}] 三级={r.level_3_category} "
              f"score={r.score:.3f} d={r.demand_score:.2f} i={r.purchase_intent_score:.2f} "
              f"gap={r.opportunity_gap:+.2f} kws={r.evidence_count} "
              f"周:{w_sum} 月:{m_sum} {r.category_status}")
    print("--- 与旧 product_opportunities(47) 差异 ---")
    diff = _diff_vs_v1(conn, result["records"])
    print(f"  旧 {diff['old_keys']} vs 新(一级·月) {diff['new_level1_month_keys']} | "
          f"新增 {diff['added']} | 消失 {diff['gone']} | 共同 {diff['common']}")
    if diff["added_examples"]:
        print(f"  新增示例: {diff['added_examples']}")
    if diff["gone_examples"]:
        print(f"  消失示例: {diff['gone_examples']}")
    if diff["biggest_rank_moves"]:
        print(f"  位次变化最大(key, v1位, v2位): {diff['biggest_rank_moves']}")
    if args.no_store:
        print("\n(no-store 预览：未写 product_opportunities_v2)")
    else:
        print(f"\n已写入 product_opportunities_v2 {p['stored']} 行（幂等重建；正式 47 未动）。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
