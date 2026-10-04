# -*- coding: utf-8 -*-
"""V2.1 机会分析层 CLI（rule-based 默认；DeepSeek calls=0；不修改任何 pool 表）。

用法:
  python scripts/analyze_opportunities_v21.py                # 分析最新 V2 池并写入分析三表
  python scripts/analyze_opportunities_v21.py --no-store     # 预览(不写库)
  python scripts/analyze_opportunities_v21.py --print-n 30   # 打印前 N 条注解
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import config
from app.analysis.analysis_layer import build_analysis
from app.database import AnalysisRepo, open_db


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()
    ap = argparse.ArgumentParser(description="V2.1 机会分析层")
    ap.add_argument("--market", default="TH")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--print-n", type=int, default=25)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn, _kw, _opp = open_db(args.db)
    pool_n = conn.execute(
        "SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0]
    pool_run = conn.execute(
        "SELECT MAX(run_id) FROM product_opportunities_v2").fetchone()[0]
    v1_n = conn.execute("SELECT COUNT(*) FROM product_opportunities").fetchone()[0]

    r = build_analysis(conn, market=args.market, store=not args.no_store)
    st = r["stats"]
    print(f"=== V2.1 机会分析层（source run={r['run_id'] or '无'}；model={r['model']}；"
          f"DeepSeek calls={st['deepseek_calls']}）===")
    print(f"输入机会(池行) {st['input']} | 分析 {st['analyzed']} | EXCLUDE 不入分析 "
          f"{len(st['excluded_keys'])}")
    print(f"方向 {st['directions']} 个 | 簇 {st['clusters']} 个 | 注解 {len(r['analysis'])} 条")
    print(f"证据状态分布: {st['evidence_states']}")
    print("\n--- Direction（按 priority=成员 score 均值降序；透明公式）---")
    for d in sorted(r["directions"], key=lambda x: x.priority, reverse=True):
        print(f"  {d.direction_name} ({d.direction_id}) n={d.opportunity_count} "
              f"core={d.core_count} adj={d.adjacent_count} rev={d.review_count} "
              f"prio={d.priority} keys={d.member_keys}")
    print("\n--- Cluster ---")
    for c in sorted(r["clusters"], key=lambda x: x.priority, reverse=True):
        print(f"  {c.cluster_name} [{c.cluster_type}] n={c.opportunity_count} "
              f"prio={c.priority} members={c.member_keys}")
    print("\n--- 注解样本 ---")
    for a in r["analysis"][: args.print_n]:
        print(f"  {a.canonical_product_key:<24} type={a.product_type or '-'} "
              f"attrs={a.attributes:<22} style={a.style:<14} group={a.target_group:<8} "
              f"state={a.evidence_state:<14} reason={a.reason_tags}")
    if args.no_store:
        print("\n(no-store 预览：未写分析表)")
    else:
        print(f"\n已写入分析三表 {st['stored']} 行（run={r['run_id']}，幂等；"
              f"V2 池 {pool_n} 行 & product_opportunities {v1_n} 行均未修改）。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
