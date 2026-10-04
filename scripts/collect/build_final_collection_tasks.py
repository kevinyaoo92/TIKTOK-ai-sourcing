# -*- coding: utf-8 -*-
"""从官方类目 TXT + 探针映射生成最终采集任务清单。

输入：
  1. 官方类目 TXT（唯一分类来源，app.category_loader.load_catalog()）
  2. data/category_mapping/tiktok_th_category_page_mapping.json（任务6探针映射）

输出：
  data/_tasks/final_collection_tasks.json

规则：
  - 官方 L2 作为唯一分类来源；page_level2 仅用于实际页面操作。
  - MATCH / ALIAS → execute=true, status=PENDING
  - FAILED（4 个页面缺失类目）→ execute=false, status=SKIPPED
  - 「美妆个护」的「美容」「个护电器」为两个独立任务，页面名相同不去重。
  - 覆盖 30 L1 / 219 L2，无重复任务，无空字段。
  - 不启动任何正式采集；不修改采集核心逻辑 / V1 / AI / 1688 / 前端。

用法：
  python scripts/collect/build_final_collection_tasks.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.category_loader import load_catalog  # noqa: E402

MARKET = "TH"
PERIOD = "month"          # 沿用已验证配置：月度
TAB = "热门搜索关键词"     # 沿用已验证配置：热门搜索关键词

MAPPING_FILE = PROJECT_ROOT / "data" / "category_mapping" / "tiktok_th_category_page_mapping.json"
OUT_FILE = PROJECT_ROOT / "data" / "_tasks" / "final_collection_tasks.json"

# 已知页面缺失、探针确认 FAILED 的官方 L2（硬编码仅为跳过清单，非类目来源；
# 类目来源仍是官方 TXT + mapping JSON）
SKIP_LEVEL2 = {
    ("收藏品", "收藏钱币"),
    ("运动与户外", "露营与徒步设备"),
    ("食品饮料", "酒"),
    ("二手", "二手收藏卡片"),
}


def load_mapping_index() -> dict[tuple[str, str], dict]:
    """mapping JSON → {(official_level1, official_level2): record}。"""
    data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    results = data["results"]
    index: dict[tuple[str, str], dict] = {}
    for r in results:
        key = (r["official_level1"], r["official_level2"])
        if key in index:
            raise ValueError(f"映射文件存在重复记录: {key}")
        index[key] = r
    return index


def build_tasks(catalog, mapping_index: dict[tuple[str, str], dict]) -> list[dict]:
    tasks: list[dict] = []
    for entry in catalog.entries:
        l1 = entry.level1
        for l2 in entry.level2:
            rec = mapping_index.get((l1, l2))
            if rec is None:
                raise ValueError(f"官方 L2 缺少探针映射记录: {l1}/{l2}")
            match_status = rec.get("match_status", "")
            if (l1, l2) in SKIP_LEVEL2:
                if match_status != "FAILED":
                    raise ValueError(f"跳过清单与映射状态不一致（应为 FAILED）: {l1}/{l2}")
                execute, status = False, "SKIPPED"
            elif match_status in ("MATCH", "ALIAS"):
                execute, status = True, "PENDING"
            else:
                raise ValueError(f"未知 match_status「{match_status}」: {l1}/{l2}")

            task = {
                "market": MARKET,
                "official_level1": l1,
                "official_level2": l2,
                "page_level1": rec.get("page_level1", ""),
                "page_level2": rec.get("page_level2", ""),
                "match_status": match_status,
                "execute": execute,
                "status": status,
                "period": PERIOD,
                "tab": TAB,
                "reason": rec.get("reason", "") if match_status == "FAILED" else "",
                "verified_at": rec.get("verified_at", ""),
            }
            tasks.append(task)
    return tasks


def validate(tasks: list[dict], catalog) -> list[str]:
    """独立校验：覆盖、重复、空字段。返回错误列表。"""
    errors: list[str] = []

    # 1) 覆盖 30 L1 / 219 L2
    official_l1 = [e.level1 for e in catalog.entries]
    official_l2 = [(e.level1, l2) for e in catalog.entries for l2 in e.level2]
    task_l1 = {t["official_level1"] for t in tasks}
    task_l2 = {(t["official_level1"], t["official_level2"]) for t in tasks}
    missing_l1 = [x for x in official_l1 if x not in task_l1]
    missing_l2 = [x for x in official_l2 if x not in task_l2]
    if missing_l1:
        errors.append(f"缺少 L1: {missing_l1}")
    if missing_l2:
        errors.append(f"缺少 L2: {missing_l2}")

    # 2) 无重复任务
    seen = set()
    dup = []
    for t in tasks:
        key = (t["official_level1"], t["official_level2"])
        if key in seen:
            dup.append(key)
        seen.add(key)
    if dup:
        errors.append(f"重复任务: {dup}")

    # 3) 无空字段（关键字段全部非空；execute=true 必须 page_level2 非空；
    #    SKIPPED 允许 page_level2 为空但必须有 reason）
    for t in tasks:
        if not t["official_level1"] or not t["official_level2"]:
            errors.append(f"空分类字段: {t}")
            continue
        if t["execute"] and not t.get("page_level2", "").strip():
            errors.append(f"execute=true 但 page_level2 为空: {t['official_level1']}/{t['official_level2']}")
        if not t["execute"] and not t.get("reason", "").strip():
            errors.append(f"SKIPPED 但 reason 为空: {t['official_level1']}/{t['official_level2']}")

    # 4) SKIPPED 集合与探针 FAILED 一致
    skipped = {(t["official_level1"], t["official_level2"]) for t in tasks if not t["execute"]}
    mapping_index = load_mapping_index()
    failed_from_mapping = {k for k, v in mapping_index.items() if v.get("match_status") == "FAILED"}
    if skipped != failed_from_mapping:
        errors.append(f"SKIPPED 集合与映射 FAILED 不一致: skipped={skipped} failed={failed_from_mapping}")

    # 5) 不启动采集：本脚本只写任务清单
    return errors


def main(argv: list[str] | None = None) -> int:
    catalog = load_catalog()
    mapping_index = load_mapping_index()
    tasks = build_tasks(catalog, mapping_index)
    errors = validate(tasks, catalog)

    execute_count = sum(1 for t in tasks if t["execute"])
    skipped_count = sum(1 for t in tasks if not t["execute"])
    match_count = sum(1 for t in tasks if t["match_status"] == "MATCH")
    alias_count = sum(1 for t in tasks if t["match_status"] == "ALIAS")
    failed_count = sum(1 for t in tasks if t["match_status"] == "FAILED")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "market": MARKET,
            "official_l1_count": catalog.l1_count,
            "official_l2_count": catalog.l2_total,
            "task_total": len(tasks),
            "execute_count": execute_count,
            "skipped_count": skipped_count,
            "match_count": match_count,
            "alias_count": alias_count,
            "failed_count": failed_count,
            "period": PERIOD,
            "tab": TAB,
            "validation_ok": not errors,
            "validation_errors": errors,
        },
        "tasks": tasks,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 统计报告 ----
    print("=" * 60)
    print("最终采集任务清单生成报告")
    print("=" * 60)
    print(f"类目来源: {catalog.source}")
    print(f"映射来源: {MAPPING_FILE}")
    print(f"官方 L1 总数: {catalog.l1_count}")
    print(f"官方 L2 总数: {catalog.l2_total}")
    print(f"任务总数: {len(tasks)}")
    print(f"  执行数量 (execute=true): {execute_count}")
    print(f"    其中 MATCH: {match_count}")
    print(f"    其中 ALIAS: {alias_count}")
    print(f"  跳过数量 (execute=false/SKIPPED): {skipped_count}")
    print(f"    其中 FAILED: {failed_count}")
    print("-" * 60)
    print("独立校验:", "通过" if not errors else "未通过")
    for e in errors:
        print("  -", e)
    print("-" * 60)
    print(f"输出文件: {OUT_FILE}")
    print("本次仅生成任务清单，未启动任何 TikTok 页面采集。")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
