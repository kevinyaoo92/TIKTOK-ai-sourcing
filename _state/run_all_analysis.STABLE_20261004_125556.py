# -*- coding: utf-8 -*-
"""批量分析调度器：遍历 final_collection_tasks.json 中全部唯一 L2，逐个跑 analyze_service.analyze。

用法:
  python scripts/analyze/run_all_analysis.py --period 2026-09
  python scripts/analyze/run_all_analysis.py --period 2026-09 --limit 3
  python scripts/analyze/run_all_analysis.py --period 2026-09 --no-ai
  python scripts/analyze/run_all_analysis.py --period 2026-09 --reset

设计:
  - force_refresh=True 强制重算，不读旧缓存
  - 串行执行，逐个 L2
  - 每个 L2 完成立即写进度（原子写），支持断点续跑
  - 每个 L2 失败重试最多 2 次（共 3 次机会）
  - Ctrl+C 中断会保存进度后退出
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

# 项目根加入 sys.path（脚本位于 scripts/analyze/run_all_analysis.py）
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.analysis import analyze_service  # noqa: E402

TASKS_FILE = ROOT / "data" / "_tasks" / "final_collection_tasks.json"
PROGRESS_FILE = ROOT / "data" / "_tasks" / "run_all_analysis_progress.json"
DB_PATH = ROOT / "data" / "tiktok_market.db"


def _validate_period(p: str) -> None:
    if not re.match(r"^\d{4}-\d{2}$", p or ""):
        raise ValueError(f"period 格式错误: {p!r}，应为 YYYY-MM，如 2026-09")


def _atomic_write(path: Path, content: str) -> None:
    """原子写入：先写临时文件再 rename，防止中断损坏。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def load_unique_l2() -> list[dict]:
    """读任务清单，去重 (level1, level2)。"""
    data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    tasks = data.get("tasks") or []
    seen = set()
    unique = []
    for t in tasks:
        if not t.get("execute", True):
            continue
        l1 = (t.get("official_level1") or t.get("page_level1") or "")
        l2 = (t.get("official_level2") or t.get("page_level2") or "")
        if not isinstance(l1, str) or not isinstance(l2, str):
            continue
        l1 = l1.strip()
        l2 = l2.strip()
        if not l1 or not l2:
            continue
        key = (l1, l2)
        if key in seen:
            continue
        seen.add(key)
        unique.append({"level1": l1, "level2": l2})
    return unique


def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {"done": [], "results": {}}
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        ts = time.strftime("%Y%m%d_%H%M%S")
        try:
            PROGRESS_FILE.rename(PROGRESS_FILE.with_suffix(f".corrupt_{ts}.json"))
            print(f"[WARN] 进度文件损坏，已重命名备份，从头开始")
        except Exception:
            pass
        return {"done": [], "results": {}}


def save_progress(progress: dict) -> None:
    content = json.dumps(progress, ensure_ascii=False, indent=2)
    _atomic_write(PROGRESS_FILE, content)


def run_one(l1: str, l2: str, period: str, with_ai: bool) -> dict:
    """跑单个 L2。失败（含 error 状态 + 异常）重试最多 2 次，共 3 次机会。

    empty 是业务判定结果（cohort < 3），不算失败，不重试。
    """
    last_err = ""
    for attempt in range(3):
        try:
            res = analyze_service.analyze(
                country="泰国", level1=l1, level2=l2, period=period,
                top_n=20, db_path=DB_PATH, with_ai=with_ai, force_refresh=True)
            status = res.get("status", "unknown")
            if status == "success":
                return {
                    "status": "success",
                    "count": len(res.get("opportunities") or []),
                    "ai_stats": res.get("ai_stats", {}),
                }
            if status == "empty":
                return {
                    "status": "empty",
                    "message": res.get("message", ""),
                    "count": 0,
                }
            # 其他状态视为 error，重试
            last_err = str(res.get("message") or res.get("error") or "unknown error")[:500]
        except Exception as e:
            last_err = str(e)[:500]

        if attempt < 2:
            time.sleep(5)

    return {"status": "error", "message": last_err, "count": 0}


def _count_ai_failures(period: str) -> int:
    try:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        n = cur.execute(
            "SELECT COUNT(*) FROM opportunity_analysis "
            "WHERE period=? AND ai_summary LIKE ?",
            (period, "%AI 语义分析暂不可用%")
        ).fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return -1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="周期，如 2026-09")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 L2（0=全部）")
    ap.add_argument("--no-ai", action="store_true", help="跳过 AI 语义分析，只算确定性评分")
    ap.add_argument("--reset", action="store_true", help="清空断点进度，从头跑")
    args = ap.parse_args(argv)

    try:
        _validate_period(args.period)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 2

    if not TASKS_FILE.exists():
        print(f"[ERROR] 找不到任务清单: {TASKS_FILE}")
        return 1
    if not DB_PATH.exists():
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        return 1

    unique_l2 = load_unique_l2()
    print(f"[TASKS] 去重后唯一 L2: {len(unique_l2)} 个")
    if not unique_l2:
        print("[ERROR] 任务清单里没有可执行 L2")
        return 1

    if args.limit > 0:
        unique_l2 = unique_l2[:args.limit]
        print(f"[LIMIT] --limit={args.limit}, 只处理前 {len(unique_l2)} 个")

    if args.reset and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        print("[RESET] 已清空进度文件")

    progress = load_progress()
    done_set = set(progress.get("done") or [])
    results = progress.get("results") or {}

    remaining = [x for x in unique_l2 if f"{x['level1']}||{x['level2']}" not in done_set]
    print(f"[PROGRESS] 已完成 {len(done_set)}, 剩余 {len(remaining)}")

    if not remaining:
        print("[DONE] 无需处理，全部已完成。如需重跑请加 --reset")
        return 0

    t0 = time.time()
    success = empty = failed = 0

    try:
        for i, item in enumerate(remaining, start=1):
            l1, l2 = item["level1"], item["level2"]
            key = f"{l1}||{l2}"
            print(f"\n[{i}/{len(remaining)}] {l1} > {l2}")
            r = run_one(l1, l2, args.period, with_ai=not args.no_ai)
            results[key] = {
                "level1": l1, "level2": l2, "period": args.period,
                "status": r["status"], "count": r.get("count", 0),
                "message": r.get("message", ""), "ai_stats": r.get("ai_stats", {}),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            done_set.add(key)
            save_progress({"done": sorted(done_set), "results": results})

            if r["status"] == "success":
                success += 1
                print(f"    OK: {r.get('count')} 条机会")
            elif r["status"] == "empty":
                empty += 1
                print(f"    EMPTY: {str(r.get('message', ''))[:60]}")
            else:
                failed += 1
                print(f"    FAIL: {str(r.get('message', ''))[:100]}")
    except KeyboardInterrupt:
        print("\n[INTERRUPT] 用户中断，进度已保存")
        return 130

    elapsed = time.time() - t0
    print()
    print("=" * 60)
    print(f"[DONE] 耗时 {elapsed/60:.1f} 分钟")
    print(f"  成功 {success} / 空 {empty} / 失败 {failed} / 总计 {len(remaining)}")
    ai_fail = _count_ai_failures(args.period)
    if ai_fail >= 0:
        print(f"  AI 语义失败条目: {ai_fail}（可在 ai_analysis_log 表查看详情）")
    print(f"  断点文件: {PROGRESS_FILE}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())