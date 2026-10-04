# -*- coding: utf-8 -*-
"""TikTok 泰国市场数据查询接口（只读，连接 data/tiktok_market.db）。

用法:
  # 1. 指定类目 + 月份 + 指标 TOP20
  python scripts/query/query_market_data.py top20 --level1 家居用品 --level2 卫浴用品 --period 2026-08 --metric 搜索量

  # 2. 查询关键词关联六指标数据
  python scripts/query/query_market_data.py keyword --keyword ผ้าขนหนูเช็ดตัว

  # 3. 查询某类目历史月份变化（各 period 各指标汇总）
  python scripts/query/query_market_data.py history --level1 家居用品 --level2 卫浴用品

  # 通用：--market 泰国|TH；--json 输出 JSON
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "tiktok_market.db"

MARKET_ALIAS = {"泰国": "TH", "thai": "TH", "TH": "TH", "th": "TH"}


def _norm_market(market: str) -> str:
    return MARKET_ALIAS.get(str(market).strip(), str(market).strip())


def _connect(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ===========================================================================
# 查询函数（返回 list[dict]）
# ===========================================================================
def query_top20(db_path: Path, level1: str, level2: str, period: str,
                metric: str, market: str = "TH") -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT km.rank, km.keyword, km.original_value, km.numeric_value,
                      km.source_file, km.collect_time
               FROM keyword_metric km JOIN category c ON km.category_id = c.id
               WHERE c.market = ? AND c.level1 = ? AND c.level2 = ?
                 AND km.period = ? AND km.metric = ?
               ORDER BY km.rank ASC""",
            (_norm_market(market), level1, level2, period, metric),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_keyword(db_path: Path, keyword: str, level1: str | None = None,
                  period: str | None = None, market: str = "TH") -> list[dict]:
    conn = _connect(db_path)
    try:
        sql = """SELECT c.level1, c.level2, km.period, km.metric, km.rank,
                        km.keyword, km.original_value, km.numeric_value
                 FROM keyword_metric km JOIN category c ON km.category_id = c.id
                 WHERE km.keyword = ? AND c.market = ?"""
        params: list = [keyword, _norm_market(market)]
        if level1:
            sql += " AND c.level1 = ?"
            params.append(level1)
        if period:
            sql += " AND km.period = ?"
            params.append(period)
        sql += " ORDER BY km.metric ASC, km.rank ASC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_history(db_path: Path, level1: str, level2: str,
                  metric: str | None = None, market: str = "TH") -> list[dict]:
    conn = _connect(db_path)
    try:
        sql = """SELECT km.period, km.metric,
                        COUNT(*) AS cnt,
                        AVG(km.numeric_value) AS avg_numeric,
                        MAX(km.numeric_value) AS max_numeric,
                        MAX(CASE WHEN km.rank = 1 THEN km.keyword END) AS top1_keyword
                 FROM keyword_metric km JOIN category c ON km.category_id = c.id
                 WHERE c.market = ? AND c.level1 = ? AND c.level2 = ?"""
        params: list = [_norm_market(market), level1, level2]
        if metric:
            sql += " AND km.metric = ?"
            params.append(metric)
        sql += " GROUP BY km.period, km.metric ORDER BY km.period ASC, km.metric ASC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ===========================================================================
# CLI
# ===========================================================================
def _print_table(headers: list[str], rows: list[dict], key_map: dict) -> None:
    cols = [key_map[h] for h in headers]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, k in enumerate(cols):
            v = r.get(k)
            s = "" if v is None else str(v)
            widths[i] = max(widths[i], len(s))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        vals = []
        for k in cols:
            v = r.get(k)
            vals.append("" if v is None else str(v))
        print(fmt.format(*vals))


def _add_common(p):
    p.add_argument("--db", default=str(DEFAULT_DB), help="数据库路径")
    p.add_argument("--market", default="TH", help="市场: TH / 泰国")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TikTok 泰国市场数据查询")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("top20", help="指定类目+月份+指标 TOP20")
    _add_common(p1)
    p1.add_argument("--level1", required=True)
    p1.add_argument("--level2", required=True)
    p1.add_argument("--period", required=True, help="月份，如 2026-08")
    p1.add_argument("--metric", required=True, help="指标: 搜索量/商品点击数/SKU销售指数/在售商品/CTR指数/CTOR评分")

    p2 = sub.add_parser("keyword", help="查询关键词关联六指标数据")
    _add_common(p2)
    p2.add_argument("--keyword", required=True, help="泰文关键词")
    p2.add_argument("--level1", default=None)
    p2.add_argument("--period", default=None)

    p3 = sub.add_parser("history", help="查询某类目历史月份变化")
    _add_common(p3)
    p3.add_argument("--level1", required=True)
    p3.add_argument("--level2", required=True)
    p3.add_argument("--metric", default=None)

    args = ap.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        print(f"[ERROR] 数据库不存在: {db}（先运行 normalize_top20_data.py 入库）")
        return 2

    if args.cmd == "top20":
        data = query_top20(db, args.level1, args.level2, args.period, args.metric, args.market)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"== {args.market} {args.level1}/{args.level2} {args.period} {args.metric} TOP20 ==")
            _print_table(["排名", "关键词", "原始值", "数值"], data,
                         {"排名": "rank", "关键词": "keyword", "原始值": "original_value", "数值": "numeric_value"})
    elif args.cmd == "keyword":
        data = query_keyword(db, args.keyword, args.level1, args.period, args.market)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"== 关键词: {args.keyword}（{len(data)} 条）==")
            _print_table(["类目", "子类目", "月份", "指标", "排名", "原始值", "数值"], data,
                         {"类目": "level1", "子类目": "level2", "月份": "period", "指标": "metric", "排名": "rank",
                          "原始值": "original_value", "数值": "numeric_value"})
    elif args.cmd == "history":
        data = query_history(db, args.level1, args.level2, args.metric, args.market)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"== {args.market} {args.level1}/{args.level2} 历史月份变化 ==")
            _print_table(["月份", "指标", "记录数", "均值", "最大值", "TOP1关键词"], data,
                         {"月份": "period", "指标": "metric", "记录数": "cnt", "均值": "avg_numeric",
                          "最大值": "max_numeric", "TOP1关键词": "top1_keyword"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
