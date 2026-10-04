# -*- coding: utf-8 -*-
"""采集完成后生成汇报总结。读 progress.json，输出成功/失败/数据库统计。"""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(r"D:\tiktok-ai-sourcing")
DB = ROOT / "data" / "tiktok_market.db"
PROGRESS = ROOT / "data" / "_tasks" / "run_all_progress.json"


def main():
    print("=" * 70)
    print("采集汇报 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)
    if not PROGRESS.exists():
        print("[ERROR] 找不到 " + str(PROGRESS))
        return 1
    p = json.loads(PROGRESS.read_text(encoding="utf-8"))
    batch_id = p.get("batch_id", "?")
    summary = p.get("summary", {})
    tasks = p.get("tasks", {})
    done = sum(1 for x in tasks.values() if x.get("status") == "done")
    failed_items = []
    for k, v in tasks.items():
        if v.get("status") == "failed":
            failed_items.append((k, v.get("error", "")[:200]))
    print()
    print("批次: " + str(batch_id))
    print("开始: " + str(p.get("started_at", "?")))
    print("总任务: " + str(summary.get("total", "?")))
    print("成功: " + str(done))
    print("失败: " + str(len(failed_items)))
    print()
    print("--- 数据库 ---")
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    for t in ["category", "keyword_metric", "collection_batch"]:
        n = cur.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
        print("  " + t + ": " + str(n))
    for r in cur.execute("SELECT period, COUNT(*) FROM keyword_metric GROUP BY period"):
        print("  keyword_metric period=" + str(r[0]) + ": " + str(r[1]))
    con.close()
    if failed_items:
        print()
        print("--- 失败清单 " + str(len(failed_items)) + " 个 ---")
        for k, err in failed_items[:50]:
            print("  " + k)
            print("    " + err)
        if len(failed_items) > 50:
            print("  ... 还有 " + str(len(failed_items) - 50) + " 个")
    else:
        print()
        print("无失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())