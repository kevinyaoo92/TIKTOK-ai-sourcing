# -*- coding: utf-8 -*-
"""整行采集数据入库：data/TH/<L1>/<L2>/monthly/TH_*_月度_热门+飙升__fullrow.txt → tiktok_market.db。

与 load_fullrow_to_db.py 的区别：
- 解析「热门+飙升」合并去重后的整行 TXT（不含 Price）。
- collection_batch 每 L2 一条，携带 run_ts/country/level1/level2/period/mode/range/source/keyword_count。
- keyword_metric 每条带 batch_id（关联本次批次）。

**表结构说明（重要）**：
现有 collection_batch 表只有 batch_id/start_time/end_time/success_count/failed_count/status；
现有 keyword_metric 表没有 batch_id 列。要满足上述新字段需求，必须先执行 ALTER TABLE 扩展
（见 --check-schema 输出）。本脚本**不会自动改表**：检测到缺列时打印需要的 ALTER SQL 并退出，
等待用户确认后由 run_all_collection.py --ensure-schema 或手动执行。

用法:
  python scripts/collect/normalize_fullrow.py --txt data\TH\美妆个护\美妆\monthly\TH_美妆个护_美妆_月度_热门+飙升__fullrow.txt
  python scripts/collect/normalize_fullrow.py --check-schema
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "tiktok_market.db"

# TXT 表头 → (metric 中文名, 是否评分必需)
COLUMNS = [
    ("排名", "排名", False),
    ("关键词", "关键词", False),
    ("搜索量", "搜索量", True),
    ("商品点击数", "商品点击数", False),
    ("SKU销售指数", "SKU销售指数", True),
    ("在售商品", "在售商品", True),
    ("CTR指数", "CTR指数", False),
    ("CTOR评分", "CTOR评分", True),
]
METRIC_NAMES = ["搜索量", "商品点击数", "SKU销售指数", "在售商品", "CTR指数", "CTOR评分"]

# 期望的扩展列（collection_batch 新增 / keyword_metric 新增）
BATCH_NEW_COLUMNS = {
    "run_ts": "TEXT",
    "country": "TEXT",
    "level1": "TEXT",
    "level2": "TEXT",
    "period": "TEXT",
    "mode": "TEXT",
    "range": "INTEGER",
    "source": "TEXT",
    "keyword_count": "INTEGER",
}
KM_NEW_COLUMNS = {"batch_id": "TEXT"}

_NUM_RE = re.compile(r"^([0-9.,]+)\s*([KMB]?)$", re.IGNORECASE)


def parse_numeric(raw: str) -> float | None:
    """'12.27K' → 12270.0；'฿3.11' → 3.11；'—'/空 → None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("—", "-", "/"):
        return None
    s = s.replace("฿", "").replace(",", "").strip()
    m = _NUM_RE.match(s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    num, suf = m.group(1), (m.group(2) or "").upper()
    v = float(num)
    if suf == "K":
        v *= 1_000
    elif suf == "M":
        v *= 1_000_000
    elif suf == "B":
        v *= 1_000_000_000
    return v


def parse_fullrow_txt(path: Path) -> list[dict]:
    """解析合并后的 FULLROW TXT → [{keyword, rank, 指标: {orig, num}}]。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("排名|"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"未找到表头: {path}")
    headers = [c.strip() for c in lines[header_idx].split("|")]
    kw_idx = headers.index("关键词")
    metric_idx = {h: i for i, h in enumerate(headers) if h in METRIC_NAMES}
    if not metric_idx:
        raise ValueError(f"表头中没有指标列: {path}")
    rows = []
    for ln in lines[header_idx + 1:]:
        ln = ln.strip()
        if not ln:
            continue
        parts = [c.strip() for c in ln.split("|")]
        if len(parts) < len(headers):
            continue
        kw = parts[kw_idx]
        if not kw:
            continue
        item = {"keyword": kw, "rank": parts[0] if headers and headers[0] == "排名" else None}
        for mname, ci in metric_idx.items():
            orig = parts[ci] if ci < len(parts) else ""
            item[mname] = {"orig": orig, "num": parse_numeric(orig)}
        rows.append(item)
    return rows


def check_schema(db_path: Path) -> tuple[list[str], list[str]]:
    """检测扩展列是否就绪。返回 (batch_missing, km_missing)。"""
    missing_batch, missing_km = [], []
    with sqlite3.connect(str(db_path)) as conn:
        bcols = {r[1] for r in conn.execute("PRAGMA table_info(collection_batch)")}
        kcols = {r[1] for r in conn.execute("PRAGMA table_info(keyword_metric)")}
    missing_batch = [c for c in BATCH_NEW_COLUMNS if c not in bcols]
    missing_km = [c for c in KM_NEW_COLUMNS if c not in kcols]
    return missing_batch, missing_km


def alter_sql() -> str:
    # SQLite 的 ALTER TABLE ADD COLUMN 一次只能加一列，逐条生成
    lines = [f"ALTER TABLE collection_batch ADD COLUMN {c} {t};" for c, t in BATCH_NEW_COLUMNS.items()]
    lines += [f"ALTER TABLE keyword_metric ADD COLUMN {c} {t};" for c, t in KM_NEW_COLUMNS.items()]
    return "\n".join(lines) + "\n"


def ensure_schema(db_path: Path) -> None:
    """执行扩展列（由用户明确确认后调用）。"""
    missing_batch, missing_km = check_schema(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for c, t in BATCH_NEW_COLUMNS.items():
            if c in missing_batch:
                conn.execute(f"ALTER TABLE collection_batch ADD COLUMN {c} {t}")
        for c, t in KM_NEW_COLUMNS.items():
            if c in missing_km:
                conn.execute(f"ALTER TABLE keyword_metric ADD COLUMN {c} {t}")
        conn.commit()


def upsert_category(conn: sqlite3.Connection, market: str, level1: str, level2: str) -> int:
    row = conn.execute(
        "SELECT id FROM category WHERE market=? AND level1=? AND level2=?",
        (market, level1, level2)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        """INSERT INTO category (market, level1, level2, page_level1, page_level2, mapping_status)
           VALUES (?, ?, ?, ?, ?, 'auto')""",
        (market, level1, level2, level1, level2))
    conn.commit()
    return cur.lastrowid


def normalize_fullrow(txt_path: Path, db_path: Path, period_label: str | None = None,
                      batch_id: str | None = None) -> dict:
    """解析 TXT → 写入 collection_batch(1条) + keyword_metric(每指标1条, 带 batch_id)。"""
    rows = parse_fullrow_txt(txt_path)
    if not rows:
        return {"txt": str(txt_path), "rows": 0, "empty": True,
                "note": "无有效行（空榜单），跳过入库"}

    parts = txt_path.parts
    try:
        month_idx = parts.index("monthly")
        market, level1, level2 = parts[month_idx - 3], parts[month_idx - 2], parts[month_idx - 1]
    except ValueError:
        market, level1, level2 = "TH", "未知", "未知"
    country = "泰国" if market.upper() == "TH" else market

    period = period_label
    if not period:
        m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", txt_path.name)
        if m:
            period = m.group(1)[:7]
    if not period:
        period = datetime.now().strftime("%Y-%m")

    if batch_id is None:
        batch_id = f"fullrow_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{market}_{level1}_{level2}"

    missing_batch, missing_km = check_schema(db_path)
    if missing_batch or missing_km:
        raise RuntimeError(
            "表结构不满足需求（不自动改表）。请先执行以下 ALTER SQL：\n" + alter_sql())

    conn = sqlite3.connect(str(db_path))
    try:
        cat_id = upsert_category(conn, market, level1, level2)
        # 该 L2 旧数据整行一致：先清该类目（保留其他类目）
        conn.execute("DELETE FROM keyword_metric WHERE category_id=?", (cat_id,))
        conn.commit()

        # 指标级 rank：同一指标按 numeric_value 降序
        ranks = {m: {} for m in METRIC_NAMES}
        for m in METRIC_NAMES:
            ordered = sorted(rows, key=lambda x: (x[m]["num"] if x[m]["num"] is not None else -1),
                             reverse=True)
            for rk, it in enumerate(ordered, start=1):
                ranks[m][it["keyword"]] = rk

        now = datetime.now().isoformat(timespec="seconds")
        inserted = 0
        for it in rows:
            kw = it["keyword"]
            for m in METRIC_NAMES:
                val = it.get(m, {})
                conn.execute(
                    """INSERT INTO keyword_metric
                       (category_id, period, metric, rank, keyword, original_value, numeric_value,
                        source_file, collect_time, batch_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cat_id, period, m, ranks[m][kw], kw, val.get("orig", ""), val.get("num"),
                     txt_path.name, now, batch_id))
                inserted += 1

        # collection_batch：每 L2 一条（batch_id 加 level1+level2 后缀，避免同批次多 L2 互相覆盖）
        sub_batch_id = f"{batch_id}__{level1}__{level2}"
        conn.execute(
            """INSERT OR REPLACE INTO collection_batch
               (batch_id, start_time, end_time, success_count, failed_count, status,
                run_ts, country, level1, level2, period, mode, range, source, keyword_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sub_batch_id, now, now, len(rows), 0, "done",
             now, country, level1, level2, period, "fullrow", 30, "hot+rising", len(rows)))
        conn.commit()
        return {"rows": len(rows), "metrics": len(METRIC_NAMES), "inserted": inserted,
                "category_id": cat_id, "market": market, "country": country,
                "level1": level1, "level2": level2, "period": period, "batch_id": batch_id}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", help="FULLROW txt 文件路径")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--period", default=None)
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--check-schema", action="store_true",
                    help="仅检测表结构是否满足新字段需求并退出")
    args = ap.parse_args()

    db_path = Path(args.db)
    if args.check_schema:
        mb, mk = check_schema(db_path)
        print("collection_batch 缺列:", mb or "(无)")
        print("keyword_metric 缺列:", mk or "(无)")
        if mb or mk:
            print("\n需要执行的 ALTER SQL（不自动改表）：")
            print(alter_sql())
            return 1
        print("表结构已满足 fullrow 需求。")
        return 0

    if not args.txt:
        print("--txt 必填")
        return 2
    try:
        res = normalize_fullrow(Path(args.txt), db_path, args.period, args.batch_id)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        return 3
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
