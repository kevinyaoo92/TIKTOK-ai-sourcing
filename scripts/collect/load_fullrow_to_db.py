# -*- coding: utf-8 -*-
"""整行采集数据入库：data/TH/<L1>/<L2>/monthly/*_FULLROW_*.txt → tiktok_market.db。

与 normalize_top20_data.py（逐字段 TOP20）不同：
- 本脚本解析 FULLROW 格式：排名|关键词|搜索量|商品点击数|SKU销售指数|在售商品|平均价格|CTR指数|CTOR评分
- 每个关键词一行，含全部指标 → 每词写多条 keyword_metric（指标级）。
- 每个指标在当批词内按 numeric_value 降序重新计算 rank（供 evidence 显示第N名）。

用法:
  python scripts/collect/load_fullrow_to_db.py --txt-root data\TH --db data\tiktok_market.db
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

# 指标列 → (metric 中文名, 是否为评分必需)
COLUMNS = [
    ("排名", "排名", False),
    ("关键词", "关键词", False),
    ("搜索量", "搜索量", True),
    ("商品点击数", "商品点击数", False),
    ("SKU销售指数", "SKU销售指数", True),
    ("在售商品", "在售商品", True),
    ("平均价格", "平均价格", False),
    ("CTR指数", "CTR指数", False),
    ("CTOR评分", "CTOR评分", True),
]

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
    """解析 FULLROW TXT → [{keyword, 指标: {orig, num}}]。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    header_idx = None
    rows = []
    for i, ln in enumerate(lines):
        if ln.startswith("排名|"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"未找到表头: {path}")
    headers = [c.strip() for c in lines[header_idx].split("|")]
    # 找到关键词列与指标列索引
    kw_idx = headers.index("关键词")
    metric_idx = {h: i for i, h in enumerate(headers) if h in ("搜索量", "商品点击数", "SKU销售指数", "在售商品", "平均价格", "CTR指数", "CTOR评分")}
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


def upsert_category(conn: sqlite3.Connection, market: str, level1: str, level2: str) -> int:
    cur = conn.execute(
        """SELECT id FROM category WHERE market=? AND level1=? AND level2=?""",
        (market, level1, level2))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        """INSERT INTO category (market, level1, level2, page_level1, page_level2, mapping_status)
           VALUES (?, ?, ?, ?, ?, 'auto')""",
        (market, level1, level2, level1, level2))
    conn.commit()
    return cur.lastrowid


def load_fullrow(txt_path: Path, db_path: Path, period_label: str | None = None) -> dict:
    rows = parse_fullrow_txt(txt_path)
    if not rows:
        raise ValueError(f"无有效行: {txt_path}")

    # 从路径提取 market/L1/L2: data/TH/美妆个护/美妆/monthly/xxx.txt
    parts = txt_path.parts
    try:
        month_idx = parts.index("monthly")
        market, level1, level2 = parts[month_idx - 3], parts[month_idx - 2], parts[month_idx - 1]
    except ValueError:
        market, level1, level2 = "TH", "未知", "未知"

    # period 从文件名提取: ..._2026-08-01_2026-08-31.txt
    period = period_label
    if not period:
        m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", txt_path.name)
        if m:
            period = f"{m.group(1)[:7]}~{m.group(2)[:7]}"
    if not period:
        period = datetime.now().strftime("%Y-%m")

    conn = sqlite3.connect(str(db_path))
    try:
        # 清理该 L2 旧数据（含旧逐字段 TOP20 与旧 FULLROW），保证整行一致
        cat_id = upsert_category(conn, market, level1, level2)
        conn.execute("DELETE FROM keyword_metric WHERE category_id=?", (cat_id,))
        conn.commit()

        # 指标级排序 rank：同一指标按 numeric_value 降序
        metric_names = ["搜索量", "商品点击数", "SKU销售指数", "在售商品", "平均价格", "CTR指数", "CTOR评分"]
        ranks = {m: {} for m in metric_names}
        for m in metric_names:
            ordered = sorted(rows, key=lambda x: (x[m]["num"] if x[m]["num"] is not None else -1), reverse=True)
            for rk, it in enumerate(ordered, start=1):
                ranks[m][it["keyword"]] = rk

        now = datetime.now().isoformat(timespec="seconds")
        inserted = 0
        for it in rows:
            kw = it["keyword"]
            for m in metric_names:
                val = it.get(m, {})
                conn.execute(
                    """INSERT INTO keyword_metric
                       (category_id, period, metric, rank, keyword, original_value, numeric_value, source_file, collect_time)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cat_id, period, m, ranks[m][kw], kw, val.get("orig", ""), val.get("num"), txt_path.name, now))
                inserted += 1
        conn.commit()
        return {"rows": len(rows), "metrics": len(metric_names), "inserted": inserted,
                "category_id": cat_id, "market": market, "level1": level1, "level2": level2, "period": period}
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", required=True, help="FULLROW txt 文件路径")
    ap.add_argument("--db", default=r"data\tiktok_market.db")
    ap.add_argument("--period", default=None)
    args = ap.parse_args()
    res = load_fullrow(Path(args.txt), Path(args.db), args.period)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
