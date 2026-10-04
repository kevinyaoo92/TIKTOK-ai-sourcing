# -*- coding: utf-8 -*-
"""AI 语义分析脚本（第二阶段衔接：screening_v1 TopN 候选 → DeepSeek → ai_semantic_results）。

数据流:
    screening_v1（通过筛选且 top_rank <= AI_CANDIDATE_LIMIT，按 top_rank 升序）
      → 断点过滤（已 success 不重复调用；failed 达上限跳过）
      → 每批 ≤30 词调 DeepSeek（默认 30）
      → 严格校验后写 ai_semantic_results（PRODUCT/NON_PRODUCT/AMBIGUOUS）

用法:
  python scripts/analyze_semantics.py                    # 处理候选(默认上限 80, V1 假设)
  python scripts/analyze_semantics.py --ai-candidate-limit 80
  python scripts/analyze_semantics.py --dry-run          # 只统计候选/待处理，不调用 AI
  python scripts/analyze_semantics.py --status           # 已处理统计
  python scripts/analyze_semantics.py --limit 30         # 本次最多处理 30 词(断点)
  python scripts/analyze_semantics.py --force-retry      # 无视失败上限重试

前置: .env 中 AI_ENABLED=true 且配置 DEEPSEEK_API_KEY（AI_ENABLED=false 时一切调用被拒绝）。
说明: AI 只做语义；百分位/排名/机会分等数字全部仍由 Python（screening_v1）负责。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行（项目根加入模块路径）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import config
from app.ai.deepseek import DeepSeekDisabledError
from app.ai.semantics import SemanticsPipeline
from app.database import SemanticRepo, open_db


def _try_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _require_ai() -> int:
    """AI Guard：未启用/缺 key 时拒绝（延续既有守卫）。"""
    if not config.AI_ENABLED:
        print("[拒绝] AI_ENABLED=false：DeepSeek 调用被 AI Guard 拦截。")
        print("       如需语义分析，请在 .env 设置 AI_ENABLED=true 并配置 DEEPSEEK_API_KEY。")
        return 2
    if not config.DEEPSEEK_API_KEY:
        print("[拒绝] 缺少 DEEPSEEK_API_KEY（请在 .env 配置，勿写死在代码）。")
        return 2
    return 0


def main() -> int:
    _try_utf8_console()
    config.ensure_dirs()

    ap = argparse.ArgumentParser(description="V1 筛选候选 → DeepSeek 语义 → ai_semantic_results")
    ap.add_argument("--market", default="TH")
    ap.add_argument("--ai-candidate-limit", type=int, default=config.AI_CANDIDATE_LIMIT,
                    help=f"只处理 screening_v1 中 top_rank<=本值的候选"
                         f"（默认 {config.AI_CANDIDATE_LIMIT}；V1 假设值，未经回测）")
    ap.add_argument("--batch-size", type=int, default=config.AI_BATCH_SIZE,
                    help=f"每批词数，硬上限 {config.AI_BATCH_MAX}（默认 {config.AI_BATCH_SIZE}）")
    ap.add_argument("--max-attempts", type=int, default=config.AI_MAX_ATTEMPTS)
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理 N 个候选(0=不限)")
    ap.add_argument("--force-retry", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只统计候选与待处理数，不调用 AI")
    ap.add_argument("--status", action="store_true", help="查看 ai_semantic_results 统计后退出")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn, _kw, _opp = open_db(args.db)

    if args.status:
        counts = SemanticRepo(conn).count_by_status(args.market)
        print(f"ai_semantic_results 统计（market={args.market}）: {counts or '暂无记录'}")
        conn.close()
        return 0

    # batch-size 校验：≤硬上限 30（>30 由 pipeline 拒绝，这里先给友好提示）
    if args.batch_size > config.AI_BATCH_MAX:
        print(f"[拒绝] batch-size={args.batch_size} 超过硬上限 {config.AI_BATCH_MAX}"
              "（产品规则：每批最多 30 个关键词）。")
        conn.close()
        return 2

    pipeline = SemanticsPipeline(
        conn,
        batch_size=args.batch_size,
        max_attempts=args.max_attempts,
        force_retry=args.force_retry,
        market=args.market,
    )

    # 候选统计（不调用 AI）
    candidates = pipeline.screening_candidates(args.ai_candidate_limit)
    eligible = pipeline.filter_eligible(candidates)
    print(f"screening_v1 候选(top_rank≤{args.ai_candidate_limit}): {len(candidates)} 词")
    print(f"待处理(断点过滤后): {len(eligible)} 词 | 每批 {args.batch_size} 词 | "
          f"模型 {pipeline.model} | 上限尝试 {pipeline.max_attempts}")
    print(f"候选示例: {[c['keyword'] for c in candidates[:5]]}")

    if args.limit:
        eligible = eligible[: args.limit]
        print(f"(--limit {args.limit} 截断)")

    # V1.1 额度硬预算：预计调用批数 = ceil(待处理/每批)；> AI_MAX_BATCHES(3) 立即停止
    from app.ai.semantics import planned_batches
    planned = planned_batches(len(eligible), args.batch_size)
    budget_ok = planned <= config.AI_MAX_BATCHES
    print(f"预计真实调用: {planned} 批（硬预算 {config.AI_MAX_BATCHES} 批，"
          f"每批≤{config.AI_BATCH_MAX} 词）-> {'通过' if budget_ok else '超预算，禁止执行'}")

    if args.dry_run:
        print(f"[dry-run] 候选 {len(candidates)} / 待处理 {len(eligible)} / "
              f"约 {planned} 批（未真正调用 AI）。")
        conn.close()
        return 0

    if not budget_ok:
        print(f"[停止] 预计调用 {planned} 批 > 硬预算 {config.AI_MAX_BATCHES} 批。"
              "不执行真实调用（checkpoint 机制：先处理部分，或调大 AI_CANDIDATE_LIMIT 前先缩小范围）。")
        conn.close()
        return 3

    # AI Guard：真正调用前最后一道闸
    rc = _require_ai()
    if rc:
        conn.close()
        return rc

    if not eligible:
        print("没有待处理的关键词（全部已有 success 语义缓存，或失败达上限——可用 --force-retry）。")
        conn.close()
        return 0

    def on_batch(st):
        print(f"  批次 {st['sent_batches']}: 累计成功 {st['success']} / 失败 {st['failed']} / 共 {st['requested']}")

    print("\n开始批量语义分析（每批一次 DeepSeek 调用，每批≤30 词）...")
    try:
        stats = pipeline.process(eligible, on_batch=on_batch)
    except DeepSeekDisabledError as e:
        print(f"[中止] {e}")
        conn.close()
        return 2
    except KeyboardInterrupt:
        print("\n[中止] 用户中断。已成功词已缓存，重跑本命令即从断点继续（失败词自动重试）。")
        conn.close()
        return 130

    conn.commit()
    print(f"\n完成: 成功 {stats['success']}，失败 {stats['failed']}，"
          f"本次处理 {stats['requested']} 词（AI 调用 {stats['sent_batches']} 次）。")
    print("查看语义结果: sqlite3 database/opportunity.db "
          "\"SELECT original_keyword, intent_status, is_product, meaning_zh, "
          "product_category, search_terms_1688, confidence, status, attempts, error "
          "FROM ai_semantic_results LIMIT 20;\"")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
