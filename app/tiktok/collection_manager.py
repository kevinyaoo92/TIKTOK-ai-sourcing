# -*- coding: utf-8 -*-
"""独立 TikTok 数据采集管理模块。

职责（全部后台，与用户请求链路隔离）：
1. 发现最新有效周期（通过 Playwright 真实导出验证，禁止依据当前日期推算）
2. 执行 TikTok 导出（调用已验证的 Playwright 脚本）
3. 读取实际 Excel（解析真实 [日期范围] 元信息）
4. 验证实际日期范围（文件存在/可读/有数据行/字段完整/类目一致/ranking_type 一致）
5. 保存原始数据（Excel 落盘到 data/raw/tiktok/thailand/）
6. 记录数据血缘（collection_log 表：每次采集尝试一条记录）
7. 判断是否已存在（keyword_data 自然键去重）
8. 形成最新有效数据（重复不导入，历史不覆盖）

ranking_type 周期规则（冻结）：
- 热门搜索关键词 -> 最新有效月度数据（month）
- 飙升关键词     -> 最新有效月度数据（month）
- 高潜力关键词   -> 最新有效周度数据（week）

状态枚举：
- SUCCESS         : 采集成功，数据已入库
- DUPLICATE       : 数据已存在，未重复导入
- COLLECTION_FAILED: 整体采集流程失败
- EXPORT_FAILED    : Playwright 导出失败
- DOWNLOAD_FAILED  : 文件下载失败
- PARSE_FAILED     : Excel 解析失败
- VALIDATION_FAILED: 校验失败（类目/ranking_type/日期/数据行等）

本模块不修改 V1、不修改前端 API、不调用 DeepSeek、不做 1688。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from app import config
from app.database import CollectionLogRepo, connect, init_db, now_str
from app.models import CollectionLogRecord
from app.tiktok.collector import LoginRequiredError, import_export_file, run_browser_export
from app.tiktok.parser import parse_export_file


# ranking_type -> 预期粒度（冻结规则）
RANKING_TYPE_GRANULARITY = {
    "热门搜索关键词": "month",
    "飙升关键词": "month",
    "高潜力关键词": "week",
}


def collect(
    *,
    market: str = "TH",
    level_1: str,
    level_2: Optional[str] = None,
    ranking_type: str = "热门搜索关键词",
    probe_max_months: int = 3,
    db_path: Optional[str | Path] = None,
) -> dict:
    """执行一次完整的采集闭环。

    流程:
    1. 调用 run_browser_export() 发现最新有效周期并导出 Excel
    2. 读取 Excel 实际日期范围
    3. 验证（文件/日期/类目/ranking_type/数据行）
    4. 判断是否已存在（keyword_data 自然键）
    5. 如不重复则导入
    6. 记录 collection_log

    返回 dict 包含:
      status: SUCCESS / DUPLICATE / COLLECTION_FAILED / EXPORT_FAILED / ...
      error: 失败原因（非 SUCCESS/DUPLICATE 时）
      period_start / period_end: Excel 实际日期
      source_file / file_hash / n_records / file_size_bytes
      inserted_rows / skipped_duplicates
      ui_period: UI 选择的候选周期
      mapping_table: 候选周期探测映射表
      collected_at / imported_at
    """
    collected_at = now_str()
    granularity = RANKING_TYPE_GRANULARITY.get(ranking_type, "month")
    mapping_table_json = "{}"

    # ---- Step 1: 浏览器导出 ----
    try:
        result = run_browser_export(
            level_1=level_1,
            level_2=level_2,
            keyword_type=ranking_type,
            granularity=granularity,
            probe_max_months=probe_max_months,
        )
    except LoginRequiredError as e:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "COLLECTION_FAILED", str(e),
            mapping_table_json=mapping_table_json,
        )
    except Exception as e:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "EXPORT_FAILED", f"{type(e).__name__}: {e}",
            mapping_table_json=mapping_table_json,
        )

    # ---- Step 2: 读取探测映射表 ----
    mapping_table = result.get("mapping_table", [])
    mapping_table_json = json.dumps(mapping_table, ensure_ascii=False)

    latest = result.get("latest_period")
    if not latest:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "EXPORT_FAILED", "没有有效的导出周期",
            mapping_table_json=mapping_table_json,
        )

    final_file = result.get("final_file")
    if not final_file or not Path(final_file).exists():
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "DOWNLOAD_FAILED", "导出成功但文件未落盘",
            mapping_table_json=mapping_table_json,
            ui_period=latest.get("ui_month_cn"),
        )

    file_path = Path(final_file)
    file_size = file_path.stat().st_size
    file_hash = latest.get("file_hash", "")
    n_records = latest.get("n_records", 0)
    ui_period = latest.get("ui_month_cn")
    period_start = latest.get("period_start")
    period_end = latest.get("period_end")

    # ---- Step 3: 独立验证（不信任 Playwright 返回的摘要，用 parser 重新解析）----
    try:
        parsed = parse_export_file(
            file_path,
            market=market,
            keyword_type=ranking_type,
            level_1_category=level_1,
            level_2_category=level_2,
        )
    except Exception as e:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "PARSE_FAILED", f"{type(e).__name__}: {e}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=n_records, period_start=period_start, period_end=period_end,
        )

    # 3a. 日期范围验证
    if not parsed.get("period_start") or not parsed.get("period_end"):
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED", "Excel 未读取到真实日期范围",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=n_records,
        )

    ps = parsed["period_start"]
    pe = parsed["period_end"]
    pt = parsed["period_type"]

    # 3b. 粒度验证
    if pt != granularity:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED",
            f"粒度不符: 期望 {granularity}，实际 {pt}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=n_records, period_start=ps, period_end=pe,
        )

    # 3c. 类目验证
    if level_1 and parsed.get("level_1_category") and parsed["level_1_category"] != level_1:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED",
            f"一级类目不符: 期望 {level_1}，实际 {parsed['level_1_category']}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=n_records, period_start=ps, period_end=pe,
        )

    if level_2 and parsed.get("level_2_category") and parsed["level_2_category"] != level_2:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED",
            f"二级类目不符: 期望 {level_2}，实际 {parsed['level_2_category']}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=n_records, period_start=ps, period_end=pe,
        )

    # 3d. ranking_type 验证
    if parsed.get("keyword_type") and parsed["keyword_type"] != ranking_type:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED",
            f"ranking_type 不符: 期望 {ranking_type}，实际 {parsed['keyword_type']}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=n_records, period_start=ps, period_end=pe,
        )

    # 3e. 数据行数验证
    if not parsed.get("n_records") or parsed["n_records"] <= 0:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED", "Excel 无数据行",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=file_hash, file_size_bytes=file_size,
            n_records=0, period_start=ps, period_end=pe,
        )

    # 3f. file_hash 验证
    if not parsed.get("file_hash"):
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "VALIDATION_FAILED", "无法计算 file_hash",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_size_bytes=file_size,
            n_records=parsed["n_records"], period_start=ps, period_end=pe,
        )

    # ---- Step 4: 去重检查 ----
    conn = connect(db_path)
    try:
        init_db(conn)
        repo = CollectionLogRepo(conn)
        already_exists = repo.exists_period(
            market, level_1, level_2, ranking_type, ps, pe,
        )
    finally:
        conn.close()

    if already_exists:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "DUPLICATE",
            f"数据已存在: {market}/{level_1}/{level_2}/{ranking_type}/{ps}~{pe}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=parsed["file_hash"], file_size_bytes=file_size,
            n_records=parsed["n_records"], period_start=ps, period_end=pe,
        )

    # ---- Step 5: 导入 ----
    try:
        import_result = import_export_file(
            file_path,
            db_path,
            market=market,
            keyword_type=ranking_type,
            level_1_category=level_1,
            level_2_category=level_2,
        )
    except Exception as e:
        return _log_and_return(
            db_path, market, level_1, level_2, ranking_type, granularity,
            collected_at, "PARSE_FAILED", f"导入失败: {type(e).__name__}: {e}",
            mapping_table_json=mapping_table_json,
            ui_period=ui_period, source_file=file_path.name,
            file_hash=parsed["file_hash"], file_size_bytes=file_size,
            n_records=parsed["n_records"], period_start=ps, period_end=pe,
        )

    imported_at = now_str()
    inserted = import_result.get("inserted", 0)
    skipped = import_result.get("skipped_duplicates", 0)

    # ---- Step 6: 记录 collection_log ----
    return _log_and_return(
        db_path, market, level_1, level_2, ranking_type, granularity,
        collected_at, "SUCCESS", None,
        mapping_table_json=mapping_table_json,
        ui_period=ui_period, source_file=file_path.name,
        file_hash=parsed["file_hash"], file_size_bytes=file_size,
        n_records=parsed["n_records"], period_start=ps, period_end=pe,
        inserted_rows=inserted, skipped_duplicates=skipped,
        imported_at=imported_at,
    )


def _log_and_return(
    db_path, market, level_1, level_2, ranking_type, granularity,
    collected_at, status, error,
    *,
    mapping_table_json="{}",
    ui_period=None, source_file=None, file_hash=None,
    file_size_bytes=None, n_records=None,
    period_start=None, period_end=None,
    inserted_rows=None, skipped_duplicates=None,
    imported_at=None,
) -> dict:
    """写入 collection_log 并返回报告 dict。"""
    try:
        conn = connect(db_path)
        try:
            init_db(conn)
            repo = CollectionLogRepo(conn)
            rec = CollectionLogRecord(
                market=market,
                level_1_category=level_1,
                level_2_category=level_2,
                ranking_type=ranking_type,
                period_granularity=granularity,
                period_start=period_start,
                period_end=period_end,
                ui_period=ui_period,
                source_file=source_file,
                file_hash=file_hash,
                file_size_bytes=file_size_bytes,
                n_records=n_records,
                inserted_rows=inserted_rows,
                skipped_duplicates=skipped_duplicates,
                status=status,
                error=error,
                mapping_table=mapping_table_json,
                collected_at=collected_at,
                imported_at=imported_at,
            )
            log_id = repo.insert(rec)
        finally:
            conn.close()
    except Exception as e:
        # 即使日志写入失败，也返回结果（不影响采集本身）
        pass

    return {
        "status": status,
        "error": error,
        "market": market,
        "level_1_category": level_1,
        "level_2_category": level_2,
        "ranking_type": ranking_type,
        "period_granularity": granularity,
        "period_start": period_start,
        "period_end": period_end,
        "ui_period": ui_period,
        "source_file": source_file,
        "file_hash": file_hash,
        "file_size_bytes": file_size_bytes,
        "n_records": n_records,
        "inserted_rows": inserted_rows,
        "skipped_duplicates": skipped_duplicates,
        "mapping_table": mapping_table_json,
        "collected_at": collected_at,
        "imported_at": imported_at,
    }


def latest_valid(
    market: str, level_1: str, level_2: Optional[str], ranking_type: str,
    db_path: Optional[str | Path] = None,
) -> Optional[dict]:
    """查询某口径下最近一次成功采集的记录（供用户请求链路读取）。"""
    conn = connect(db_path)
    try:
        init_db(conn)
        repo = CollectionLogRepo(conn)
        row = repo.latest_success(market, level_1, level_2, ranking_type)
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


# =============================================================================
# Page TOP20 页面原生排序采集 - 批次管理模块
# 独立于 Excel 导出采集路径，复用 collection_log / latest_valid / 去重基础设施
# =============================================================================

# 六个排序字段（与 tiktok_page_top20.py 中 SORT_FIELDS 一致，这里只列标识名用于 batch 完整性校验）
TOP20_RANKING_FIELDS = [
    "search_volume",
    "product_clicks",
    "sku_sales_index",
    "on_sale_products",
    "ctr_index",
    "ctor_score",
]


def _gen_batch_id(market: str, level_1: str, level_2: Optional[str],
                  ranking_type: str, period_granularity: str,
                  period_start: str, period_end: str) -> str:
    """生成稳定的批次 ID（同一周期同一口径生成相同 batch_id，用于去重判断）。"""
    l2_part = level_2 or "L1"
    raw = f"{market}|{level_1}|{l2_part}|{ranking_type}|{period_granularity}|{period_start}|{period_end}"
    import hashlib
    return "top20_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _page_top20_log_record(
    market: str, level_1: str, level_2: Optional[str],
    ranking_type: str, period_granularity: str,
    period_start: str, period_end: str,
    batch_id: str, status: str, n_records: int = 0,
    error: Optional[str] = None,
) -> CollectionLogRecord:
    """构造 page_top20 用的 collection_log 记录。"""
    return CollectionLogRecord(
        market=market,
        level_1_category=level_1,
        level_2_category=level_2,
        ranking_type=ranking_type,
        period_granularity=period_granularity,
        period_start=period_start,
        period_end=period_end,

        status=status,
        n_records=n_records,
        error=error,
        source="page_top20",
        batch_id=batch_id,
        collected_at=now_str(),
        imported_at=now_str(),
    )


def _check_top20_duplicate(
    conn, market: str, level_1: str, level_2: Optional[str],
    ranking_type: str, period_granularity: str,
    period_start: str, period_end: str,
) -> bool:
    """检查同一口径同一周期是否已有 SUCCESS 的 page_top20 批次。"""
    if level_2:
        sql = (
            "SELECT 1 FROM collection_log "
            "WHERE market=? AND level_1_category=? AND level_2_category=? "
            "AND ranking_type=? AND source='page_top20' AND status='SUCCESS' "
            "AND period_start=? AND period_end=? LIMIT 1"
        )
        args = (market, level_1, level_2, ranking_type, period_start, period_end)
    else:
        sql = (
            "SELECT 1 FROM collection_log "
            "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
            "AND ranking_type=? AND source='page_top20' AND status='SUCCESS' "
            "AND period_start=? AND period_end=? LIMIT 1"
        )
        args = (market, level_1, ranking_type, period_start, period_end)
    return conn.execute(sql, args).fetchone() is not None


def collect_page_top20_batch(
    market: str,
    level_1: str,
    level_2: Optional[str],
    ranking_type: str,
    period_granularity: str,
    collect_fn,
    db_path: Optional[str] = None,
    skip_duplicate_check: bool = False,
) -> dict:
    """执行一次 Page TOP20 完整批次采集并入库。

    batch 完整性原则：
    - 六个 ranking_field 全部成功 + 全部通过校验 → batch SUCCESS
    - 任意一个字段失败 → 整个 batch FAILED，已入库的该 batch 数据删除
    - 旧 latest_valid 永远不受新 batch 失败影响

    参数:
        market, level_1, level_2, ranking_type, period_granularity: 采集口径
        collect_fn: 可调用对象，接受 (market, level_1, level_2, ranking_type, period_granularity)
                   返回 collect_top20_batch 的 result dict（含 success/records/error 等）
        db_path: 数据库路径，None 用默认
        skip_duplicate_check: 跳过重复检查（测试用）

    返回:
        dict: {status, batch_id, n_records, error, period_start, period_end}
    """
    from app.database import connect, init_db, PageTop20Repo, CollectionLogRepo

    conn = connect(db_path)
    try:
        init_db(conn)
        log_repo = CollectionLogRepo(conn)
        data_repo = PageTop20Repo(conn)

        # 1. 执行采集
        try:
            result = collect_fn(market, level_1, level_2, ranking_type, period_granularity)
        except Exception as e:
            print(f"[ERROR] 采集执行异常: {e}")
            failed_log = _page_top20_log_record(
                market, level_1, level_2, ranking_type, period_granularity,
                "", "", "", "COLLECTION_FAILED", error=str(e)
            )
            log_repo.insert(failed_log)
            return {"status": "COLLECTION_FAILED", "batch_id": "", "n_records": 0,
                    "error": str(e), "period_start": "", "period_end": ""}

        period_start = result.get("period_start", "")
        period_end = result.get("period_end", "")
        records = result.get("records", [])

        # 2. 生成稳定 batch_id
        batch_id = _gen_batch_id(market, level_1, level_2, ranking_type,
                                period_granularity, period_start, period_end)

        # 3. 重复检查（同口径同周期已有 SUCCESS 批次 → DUPLICATE）
        if not skip_duplicate_check:
            if period_start and period_end and _check_top20_duplicate(
                conn, market, level_1, level_2, ranking_type,
                period_granularity, period_start, period_end,
            ):
                dup_log = _page_top20_log_record(
                    market, level_1, level_2, ranking_type, period_granularity,
                    period_start, period_end, batch_id, "DUPLICATE",
                    n_records=len(records),
                )
                log_repo.insert(dup_log)
                return {"status": "DUPLICATE", "batch_id": batch_id,
                        "n_records": len(records), "error": None,
                        "period_start": period_start, "period_end": period_end}

        # 4. 判断 batch 整体成功/失败
        if result["success"]:
            # 双重校验：6 个 ranking_field 都应有数据，且总数 >= 6
            fields_present = set()
            for rec in records:
                fields_present.add(rec.ranking_field)
            all_fields_ok = all(f in fields_present for f in TOP20_RANKING_FIELDS)

            if not all_fields_ok:
                missing = [f for f in TOP20_RANKING_FIELDS if f not in fields_present]
                result["success"] = False
                result["error"] = f"缺少 ranking_field: {missing}"

        if result["success"]:
            # 5. 成功：批量入库
            for rec in records:
                rec.batch_id = batch_id
            inserted, skipped = data_repo.insert_many(records)

            success_log = _page_top20_log_record(
                market, level_1, level_2, ranking_type, period_granularity,
                period_start, period_end, batch_id, "SUCCESS",
                n_records=inserted,
            )
            log_repo.insert(success_log)

            return {
                "status": "SUCCESS",
                "batch_id": batch_id,
                "n_records": inserted,
                "error": None,
                "period_start": period_start,
                "period_end": period_end,
                "skipped_duplicates": skipped,
            }
        else:
            # 6. 失败：先尝试清理可能已写入的部分数据（保险措施），再记录 FAILED 日志
            #    旧 latest_valid 不受任何影响（因为不做任何切换操作）
            data_repo.delete_batch(batch_id)

            failed_log = _page_top20_log_record(
                market, level_1, level_2, ranking_type, period_granularity,
                period_start, period_end, batch_id, "VALIDATION_FAILED",
                n_records=len(records),
                error=result.get("error") or "采集校验失败",
            )
            log_repo.insert(failed_log)

            return {
                "status": "VALIDATION_FAILED",
                "batch_id": batch_id,
                "n_records": 0,
                "error": result.get("error"),
                "period_start": period_start,
                "period_end": period_end,
            }

    finally:
        conn.close()


def get_latest_valid_top20(
    market: str,
    level_1: str,
    level_2: Optional[str],
    ranking_type: str,
    period_granularity: str,
    db_path: Optional[str] = None,
) -> dict:
    """获取该口径下 latest_valid 的 Page TOP20 批次数据。

    只有完整 batch SUCCESS 的才会被返回，部分失败的 batch 绝不会成为 latest_valid。
    返回: {status, batch_id, period_start, period_end, n_records, rows}
    """
    from app.database import connect, init_db, PageTop20Repo

    conn = connect(db_path)
    try:
        init_db(conn)
        repo = PageTop20Repo(conn)

        batch_log = repo.latest_valid_batch(market, level_1, level_2, ranking_type, period_granularity)
        if not batch_log:
            return {"status": "NO_DATA", "batch_id": "", "period_start": "",
                    "period_end": "", "n_records": 0, "rows": []}

        rows = repo.get_batch_rows(batch_log["batch_id"])
        return {
            "status": "SUCCESS",
            "batch_id": batch_log["batch_id"],
            "period_start": batch_log["period_start"],
            "period_end": batch_log["period_end"],
            "n_records": len(rows),
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()
