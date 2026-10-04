# -*- coding: utf-8 -*-
"""TikTok 关键词榜单导出文件解析器（确定性，纯 Python/pandas，不经 AI）。

已按真实导出结构验证（data/raw/tiktok/thailand/Top_Search_Term_Rank_List_*.xlsx）：
- 前若干行为元信息行，形如：`[日期范围]: 2026-07-01 ~ 2026-07-31`、`[类目]: 时尚配件`
- 表头行（含"关键词/搜索量/…"）可能在元信息之后，逐行探测定位
- 表头：关键词 | 搜索量 | 商品点击数 | SKU 销售指数 | 在售商品 | 平均价格 (฿) | CTR 指数 | CTOR 评分
- 数据行为泰语关键词（原样保存，不翻译、不推断、不改写）

如果未来导出结构变化：优先扩展别名表 _HEADER_ALIASES 与元信息解析，不要改业务框架。
"""
from __future__ import annotations

import csv as _csv
import io
import re
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from app.models import KeywordRecord

# ---------------------------------------------------------------------------
# 表头别名（normalize：去所有空白 + 小写；便于兼容细微差异）
# ---------------------------------------------------------------------------
_HEADER_ALIASES: dict[str, str] = {
    "关键词": "keyword", "搜索词": "keyword", "关键词/搜索词": "keyword", "关键字": "keyword",
    "搜索量": "search_volume", "搜索次数": "search_volume",
    "商品点击数": "product_clicks", "点击商品数": "product_clicks",
    "sku销售指数": "sku_sales_index", "销售指数": "sku_sales_index",
    "在售商品": "on_sale_products", "在售商品数": "on_sale_products", "在售商品数量": "on_sale_products",
    "平均价格": "avg_price",  # 真实表头为 "平均价格 (฿)"，用 startswith 处理
    "ctr指数": "ctr_index",
    "ctor评分": "ctor_score", "ctor指数": "ctor_score",
}

# 导出文件名前缀 → 榜单类型（仅用于识别；不在映射内的回退默认"热门搜索关键词"）
_FILENAME_KEYWORD_TYPE_HINTS = (
    ("TOP_SEARCH_TERM", "热门搜索关键词"),   # Top_Search_Term_Rank_List_xxx
)

# 空白值（TikTok 导出空单元格/'-'/'N/A'）
_EMPTY = {"", "-", "--", "n/a", "na", "nan", "none", "null"}

_META_RE = re.compile(r"^\s*\[([^\]]+)\]\s*[:：]\s*(.*?)\s*$")
_DATE_RANGE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})\s*[~～\-—至到]\s*(\d{4})-(\d{2})-(\d{2})"
)


def _norm_header(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _cell_str(v) -> str:
    """单元格 → 字符串（数字 6767.0 → '6767'，保留原文语义）。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s


def _num(v) -> Optional[float]:
    """数值单元格 → float|None（'-'/'N/A'/空 → None；不推断、不补缺）。"""
    s = _cell_str(v)
    if s.lower() in _EMPTY:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _int_num(v) -> Optional[int]:
    f = _num(v)
    if f is None:
        return None
    return int(f) if float(f).is_integer() else None


def _detect_period(meta_dates: dict) -> tuple[Optional[str], Optional[str], str]:
    """由 [日期范围] 判定周期类型。规则：整月(起日=1且天数≥28)→month；≤7天→week。"""
    ps, pe = meta_dates.get("period_start"), meta_dates.get("period_end")
    if not ps or not pe:
        return None, None, "unknown"
    days = (pe - ps).days + 1
    if ps.day == 1 and days >= 28:
        return ps.isoformat(), pe.isoformat(), "month"
    if days <= 7:
        return ps.isoformat(), pe.isoformat(), "week"
    return ps.isoformat(), pe.isoformat(), "other"


def _detect_keyword_type(filename: str, default: str) -> str:
    upper = filename.upper()
    for prefix, kt in _FILENAME_KEYWORD_TYPE_HINTS:
        if upper.startswith(prefix):
            return kt
    return default


def _iter_rows(path: Path):
    """统一按 无表头(object) 二维行迭代，兼容 xlsx 与 csv。"""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        df = pd.read_excel(path, sheet_name=0, header=None, dtype=object)
        for row in df.itertuples(index=False, name=None):
            yield [None if pd.isna(c) else c for c in row]
    elif path.suffix.lower() == ".csv":
        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError(f"无法识别文件编码: {path.name}")
        # 用标准 csv 模块：兼容"元信息行 1 列、表头/数据行多列"的不齐整导出
        for row in _csv.reader(io.StringIO(text)):
            yield [c if c != "" else None for c in row]
    else:
        raise ValueError(f"不支持的导出格式: {path.suffix}（支持 .xlsx/.xlsm/.csv）")


def parse_export_file(
    file_path: str | Path,
    *,
    market: str = "TH",
    category: Optional[str] = None,
    keyword_type: Optional[str] = None,
    level_1_category: Optional[str] = None,
    level_2_category: Optional[str] = None,
) -> dict:
    """解析一份关键词榜单导出文件。

    参数:
      market: 市场(TH)
      category: 榜单类目原文覆盖（默认取文件元信息 [类目]；一级=一级类目名，二级=二级类目名）
      keyword_type: 榜单类型覆盖（默认由文件名推断）
      level_1_category: 规范化一级类目（如 时尚配件）。缺省 = category。
      level_2_category: 规范化二级类目（如 平价饰品）。一级数据传 None。

    返回 dict:
      source_file / file_path / market / category / keyword_type
      level_1_category / level_2_category / file_hash
      period_start / period_end / period_type
      records: list[KeywordRecord]
    原始文件只读不改。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"导出文件不存在: {path}")

    rows = list(_iter_rows(path))
    if not rows:
        raise ValueError(f"导出文件为空: {path.name}")

    meta: dict[str, str] = {}       # [X]: Y → {x: y}
    header_idx: Optional[int] = None
    col: dict[str, int] = {}        # 字段名 → 列下标

    for i, row in enumerate(rows):
        cells = [_cell_str(c) for c in row]
        if not any(cells):
            continue
        # 1) 元信息行：[日期范围]: ...（单元格内可能多行，逐行拆）
        for line in "\n".join(cells).splitlines():
            m = _META_RE.match(line)
            if m:
                key, val = m.group(1).strip(), m.group(2).strip()
                meta.setdefault(key, val)  # 同键取首个
        # 2) 表头行：定位含"关键词"与任一指标列的行
        if header_idx is None:
            norm = [_norm_header(c) for c in cells]
            hit = {}
            for k, field in _HEADER_ALIASES.items():
                for ci, n in enumerate(norm):
                    if field == "avg_price":
                        if n.startswith(_norm_header("平均价格")):
                            hit["avg_price"] = ci
                    elif n == _norm_header(k):
                        hit[field] = ci
            if "keyword" in hit and "search_volume" in hit:
                header_idx = i
                col = hit
                break  # 表头一定在数据之前，找到即止

    if header_idx is None or "keyword" not in col:
        raise ValueError(
            f"未在文件中定位到表头行(需含'关键词'与'搜索量'列): {path.name}"
        )

    # ---- 周期 ----
    ps_date, pe_date = None, None
    rng = meta.get("日期范围", "")
    m = _DATE_RANGE_RE.search(rng)
    if m:
        ps_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        pe_date = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
    period_start, period_end, period_type = _detect_period(
        {"period_start": ps_date, "period_end": pe_date}
    )

    # ---- 类目 / 榜单类型 / 规范化类目层级 ----
    cat = category or meta.get("类目") or "未知类目"
    lvl1 = level_1_category or cat      # 一级数据默认 level_1=榜单类目
    lvl2 = level_2_category             # 二级数据显式提供；一级为 None
    kt = keyword_type or _detect_keyword_type(path.name, "热门搜索关键词")

    # ---- 源文件指纹（同文件重复导入识别）----
    import hashlib
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    # ---- 数据行 ----
    records: list[KeywordRecord] = []
    rank = 0
    for row in rows[header_idx + 1:]:
        cells = [_cell_str(c) for c in row]
        kw = cells[col["keyword"]] if col["keyword"] < len(cells) else ""
        if not kw:  # 空关键词行（含可能的总计/页脚）跳过
            continue
        def cell(field: str):
            ci = col.get(field)
            if ci is None or ci >= len(cells):
                return None
            return cells[ci]
        rank += 1
        records.append(KeywordRecord(
            period_start=period_start or "",
            period_end=period_end or "",
            period_type=period_type,
            market=market,
            category=cat,
            keyword_type=kt,
            keyword=kw,
            rank=rank,
            search_volume=_num(cell("search_volume")),
            product_clicks=_num(cell("product_clicks")),
            sku_sales_index=_num(cell("sku_sales_index")),
            on_sale_products=_int_num(cell("on_sale_products")),
            avg_price=_num(cell("avg_price")),
            price_unit=None,  # 导出表头"(฿)"=THB；不写死，避免推断
            ctr_index=_num(cell("ctr_index")),
            ctor_score=_num(cell("ctor_score")),
            source_file=path.name,
            file_hash=file_hash,
            level_1_category=lvl1,
            level_2_category=lvl2,
        ))

    if not records:
        raise ValueError(f"表头后无数据行: {path.name}")

    return {
        "source_file": path.name,
        "file_path": str(path),
        "market": market,
        "category": cat,
        "keyword_type": kt,
        "level_1_category": lvl1,
        "level_2_category": lvl2,
        "file_hash": file_hash,
        "period_start": period_start,
        "period_end": period_end,
        "period_type": period_type,
        "n_records": len(records),
        "records": records,
    }
