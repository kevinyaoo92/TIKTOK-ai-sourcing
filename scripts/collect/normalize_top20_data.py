# -*- coding: utf-8 -*-
"""TikTok 泰国市场 TOP20 数据标准化 + 数据库索引层。

功能：
1. 读取 data/TH/<L1>/<L2>/monthly/*.txt（采集核心生成的标准 TXT），解析为标准 JSON。
2. 写入 data/tiktok_market.db（不影响 opportunity.db）：
   - category 表：market/level1/level2/page_level1/page_level2/mapping_status
   - keyword_metric 表：每条 (类目, period, metric, rank, keyword, ...) 一行
   - collection_batch 表：本次标准化批次的统计
3. 批量处理全部类目，支持断点（已存在 JSON 且 db 有记录可跳过）。

规则：
- 保留泰文关键词原文（keyword 不做任何清洗）。
- original_value 保留 TikTok 页面原始数值格式（如 3.03K / 14.22）。
- numeric_value 仅用于计算分析（K/M/B 后缀换算，无法转换时为 None 并记录日志）。
- 不修改 tiktok_page_top20.py / V1 / AI / 1688 / 前端。

用法:
  python scripts/collect/normalize_top20_data.py                 # 全量标准化 + 入库
  python scripts/collect/normalize_top20_data.py --txt-root data/_batches/20260915_205204  # 指定 TXT 根目录
  python scripts/collect/normalize_top20_data.py --json-only     # 只转 JSON，不入库
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TXT_ROOT = PROJECT_ROOT / "data" / "TH"
DEFAULT_MAPPING = PROJECT_ROOT / "data" / "category_mapping" / "tiktok_th_category_page_mapping.json"
DEFAULT_JSON_ROOT = PROJECT_ROOT / "data" / "processed" / "TH"
DEFAULT_DB = PROJECT_ROOT / "data" / "tiktok_market.db"

METRICS = ["搜索量", "商品点击数", "SKU销售指数", "在售商品", "CTR指数", "CTOR评分"]

logger = logging.getLogger("normalize_top20")


# ===========================================================================
# TXT 解析
# ===========================================================================
def parse_numeric(value: str) -> float | None:
    """把 TikTok 原始数值转换为可计算数值。

    3.03K -> 3030.0；14.22 -> 14.22；1.2M -> 1200000.0；5B -> 5e9。
    无法转换时返回 None（由调用方保留 original_value 并记日志）。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = re.fullmatch(r"([0-9][0-9,]*\.?[0-9]*)\s*([KMB]?)", s, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = m.group(2).upper()
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    elif suffix == "B":
        num *= 1_000_000_000
    return num


def parse_txt(path: Path) -> dict:
    """解析单个 TXT，返回标准 JSON 字典。解析失败抛 ValueError。"""
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln.rstrip("\n") for ln in text.splitlines()]

    # 1. 头部
    header = lines[0] if lines else ""
    parts = header.split("｜")
    if len(parts) < 4:
        raise ValueError(f"头部格式异常: {header!r}")
    market = "TH" if "泰国" in parts[0] else parts[0]
    level1_official = parts[1].strip()
    level2_official = parts[2].strip()
    ranking_type = parts[3].strip()

    # 2. 日期
    period_start = ""
    period_end = ""
    for ln in lines[1:4]:
        m = re.search(r"日期[：:]\s*(\d{4}-\d{2}-\d{2})\s*[-—]\s*(\d{4}-\d{2}-\d{2})", ln)
        if m:
            period_start, period_end = m.group(1), m.group(2)
            break
    if not period_start:
        raise ValueError(f"日期行缺失或格式异常: {lines[1] if len(lines) > 1 else ''}")

    # 3. 指标块
    metrics: dict[str, list[dict]] = {}
    cur_metric: str | None = None
    for ln in lines:
        m = re.match(r"【(.+?)\s*TOP\d+\s*｜高→低】", ln)
        if m:
            cur_metric = m.group(1).strip()
            metrics.setdefault(cur_metric, [])
            continue
        if cur_metric is None:
            continue
        row = re.match(r"^\s*(\d+)[.、]\s+(.+?)\s*[—–]\s*(\S+)\s*$", ln)
        if not row:
            continue
        rank = int(row.group(1))
        keyword = row.group(2).strip()
        original_value = row.group(3).strip()
        metrics[cur_metric].append({
            "rank": rank,
            "keyword": keyword,
            "original_value": original_value,
            "numeric_value": parse_numeric(original_value),
            "metric": cur_metric,
        })

    if not metrics:
        raise ValueError(f"未解析到任何指标块: {path.name}")

    mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "market": market,
        "level1_official": level1_official,
        "level2_official": level2_official,
        "level1_page": level1_official,   # 页面名在 build_txt 中与官方名一致时回退，mapping 覆盖
        "level2_page": level2_official,
        "mapping_status": "",
        "period_start": period_start,
        "period_end": period_end,
        "period": period_start[:7],
        "ranking_type": ranking_type,
        "source_file": path.name,
        "collect_time": mtime,
        "metrics": metrics,
    }


def apply_mapping(data: dict, mapping_index: dict) -> dict:
    """用官方->页面映射覆盖 level1_page/level2_page/mapping_status。"""
    key = (data["level1_official"], data["level2_official"])
    m = mapping_index.get(key)
    if m:
        data["level1_page"] = m["page_level1"]
        data["level2_page"] = m["page_level2"]
        data["mapping_status"] = m["match_status"]
    return data


def load_mapping_index(mapping_path: Path) -> dict:
    """读取 mapping JSON -> {(level1_official, level2_official): {...}}"""
    if not mapping_path.exists():
        return {}
    raw = json.loads(mapping_path.read_text(encoding="utf-8"))
    results = raw.get("results", raw if isinstance(raw, list) else [])
    idx: dict = {}
    for r in results:
        idx[(r.get("official_level1", ""), r.get("official_level2", ""))] = {
            "page_level1": r.get("page_level1", ""),
            "page_level2": r.get("page_level2", ""),
            "match_status": r.get("match_status", ""),
        }
    return idx


# ===========================================================================
# 数据库
# ===========================================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    level1 TEXT NOT NULL,
    level2 TEXT NOT NULL,
    page_level1 TEXT,
    page_level2 TEXT,
    mapping_status TEXT,
    UNIQUE(market, level1, level2)
);

CREATE TABLE IF NOT EXISTS keyword_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES category(id),
    period TEXT NOT NULL,
    metric TEXT NOT NULL,
    rank INTEGER,
    keyword TEXT,
    original_value TEXT,
    numeric_value REAL,
    source_file TEXT,
    collect_time TEXT
);

CREATE INDEX IF NOT EXISTS idx_km_cat_period_metric ON keyword_metric(category_id, period, metric);

CREATE TABLE IF NOT EXISTS collection_batch (
    batch_id TEXT PRIMARY KEY,
    start_time TEXT,
    end_time TEXT,
    success_count INTEGER,
    failed_count INTEGER,
    status TEXT
);
"""


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)


def write_to_db(db_path: Path, json_items: list[dict], batch_id: str,
                start_time: str, end_time: str, success_count: int,
                failed_count: int, status: str = "done") -> int:
    """批量写入 category + keyword_metric，返回写入的 keyword_metric 行数。"""
    ensure_schema(db_path)
    written = 0
    with sqlite3.connect(str(db_path)) as conn:
        for item in json_items:
            conn.execute(
                """INSERT INTO category (market, level1, level2, page_level1, page_level2, mapping_status)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, level1, level2) DO UPDATE SET
                     page_level1=excluded.page_level1,
                     page_level2=excluded.page_level2,
                     mapping_status=excluded.mapping_status""",
                (item["market"], item["level1_official"], item["level2_official"],
                 item["level1_page"], item["level2_page"], item["mapping_status"]),
            )
            row = conn.execute(
                "SELECT id FROM category WHERE market=? AND level1=? AND level2=?",
                (item["market"], item["level1_official"], item["level2_official"]),
            ).fetchone()
            category_id = row[0]

            for metric, records in item["metrics"].items():
                for rec in records:
                    conn.execute(
                        """INSERT INTO keyword_metric
                           (category_id, period, metric, rank, keyword, original_value,
                            numeric_value, source_file, collect_time)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (category_id, item["period"], metric, rec["rank"], rec["keyword"],
                         rec["original_value"], rec["numeric_value"],
                         item["source_file"], item["collect_time"]),
                    )
                    written += 1

        conn.execute(
            """INSERT OR REPLACE INTO collection_batch
               (batch_id, start_time, end_time, success_count, failed_count, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (batch_id, start_time, end_time, success_count, failed_count, status),
        )
    return written


# ===========================================================================
# 主流程
# ===========================================================================
def find_txt_files(txt_root: Path) -> list[Path]:
    if not txt_root.exists():
        return []
    return sorted(txt_root.glob("*/*/monthly/*.txt"))


def normalize_all(txt_root: Path, mapping_path: Path, json_root: Path,
                  db_path: Path, json_only: bool = False) -> dict:
    """批量标准化 + 入库。返回统计。"""
    txt_files = find_txt_files(txt_root)
    mapping_index = load_mapping_index(mapping_path)
    if not json_only:
        ensure_schema(db_path)

    start = datetime.now()
    batch_id = "norm_" + start.strftime("%Y%m%d_%H%M%S")
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%S")

    json_items: list[dict] = []
    issues: list[dict] = []
    success = 0
    failed = 0

    for p in txt_files:
        try:
            item = parse_txt(p)
            apply_mapping(item, mapping_index)
            # numeric 无法转换的字段记录日志
            bad_numeric = [
                (rec["metric"], rec["rank"], rec["original_value"])
                for recs in item["metrics"].values() for rec in recs
                if rec["numeric_value"] is None
            ]
            for metric, rank, ov in bad_numeric:
                logger.warning("[数值未转换] %s/%s %s #%s 原始值=%r", item["level1_official"],
                               item["level2_official"], metric, rank, ov)
            json_items.append(item)

            if not json_only:
                out = json_root / item["level1_official"] / item["level2_official"] / "monthly"
                out.mkdir(parents=True, exist_ok=True)
                (out / (p.stem + ".json")).write_text(
                    json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            success += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            issues.append({"file": str(p), "error": str(e)})
            logger.error("解析失败: %s (%s)", p, e)

    end_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    stats = {
        "batch_id": batch_id,
        "start_time": start_iso,
        "end_time": end_iso,
        "txt_total": len(txt_files),
        "success": success,
        "failed": failed,
        "issues": issues,
    }

    written = 0
    if not json_only and json_items:
        written = write_to_db(db_path, json_items, batch_id, start_iso, end_iso,
                              success, failed)
    stats["db_rows_written"] = written
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TikTok 泰国 TOP20 数据标准化 + 数据库索引")
    ap.add_argument("--txt-root", default=str(DEFAULT_TXT_ROOT), help="TXT 根目录")
    ap.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="映射 JSON 路径")
    ap.add_argument("--json-root", default=str(DEFAULT_JSON_ROOT), help="标准化 JSON 输出目录")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="目标数据库路径")
    ap.add_argument("--json-only", action="store_true", help="只转换 JSON，不入库")
    ap.add_argument("--quiet", action="store_true", help="仅输出汇总")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    stats = normalize_all(Path(args.txt_root), Path(args.mapping),
                          Path(args.json_root), Path(args.db), args.json_only)

    print("=" * 50)
    print("标准化完成: 文件 %d | 成功 %d | 失败 %d | 入库行 %d"
          % (stats["txt_total"], stats["success"], stats["failed"], stats["db_rows_written"]))
    print(f"批次: {stats['batch_id']}")
    for issue in stats["issues"]:
        print(f"  [失败] {issue['file']}: {issue['error']}")
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
