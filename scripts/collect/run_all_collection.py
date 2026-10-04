# -*- coding: utf-8 -*-
"""全量采集调度器：遍历 data/_tasks/final_collection_tasks.json 中全部 L2 类目。

流程（--overwrite-all 全量覆盖）：
1. 清空 ai_analysis_log / opportunity_analysis / keyword_metric / collection_batch
   （**保留 category 不动**）。
2. 删除 data/TH/ 下所有旧 *.txt，再重新生成（目录结构保持不变）。
3. 读取 final_collection_tasks.json，遍历 execute=true 的 215 个 L2：
   - 调用 tiktok_page_fullrow.py 采集「热门+飙升」两榜并合并去重 → TXT
   - 调用 normalize_fullrow.py 入库（collection_batch 每 L2 一条 + keyword_metric 带 batch_id）
4. 进度记录 data/_tasks/run_all_progress.json（断点续跑：已完成任务跳过）。
5. 状态记录 data/_batches/<batch_id>/status.json。

**表结构前置检查**：本脚本启动时会检查 collection_batch / keyword_metric 是否具备新字段
（run_ts/country/level1/level2/period/mode/range/source/keyword_count / batch_id）。
缺列时**不会自动改表**：打印 ALTER SQL 并退出，等待用户确认后手动执行或传 --ensure-schema。

用法:
  python scripts/collect/run_all_collection.py --mode fullrow --page-size 30 --source hot+rising --overwrite-all
  python scripts/collect/run_all_collection.py --mode fullrow --page-size 30 --source hot+rising --overwrite-all --ensure-schema
  python scripts/collect/run_all_collection.py --mode fullrow --page-size 30 --source hot+rising   # 断点续跑（不覆盖）
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.collect.normalize_fullrow import (
    DEFAULT_DB, check_schema, alter_sql, ensure_schema,
)

TASKS_FILE = PROJECT_ROOT / "data" / "_tasks" / "final_collection_tasks.json"
PROGRESS_FILE = PROJECT_ROOT / "data" / "_tasks" / "run_all_progress.json"
BATCHES_DIR = PROJECT_ROOT / "data" / "_batches"
DATA_TH = PROJECT_ROOT / "data" / "TH"
DB_PATH = PROJECT_ROOT / "data" / "tiktok_market.db"

COLLECT_SCRIPT = PROJECT_ROOT / "scripts" / "collect" / "tiktok_page_fullrow.py"
NORMALIZE_SCRIPT = PROJECT_ROOT / "scripts" / "collect" / "normalize_fullrow.py"

# 单榜单 TXT 文件名中的 source 标签（与 tiktok_page_fullrow.py 约定一致）
SOURCE_FILE_LABEL = {
    "hot": "热门搜索关键词",
    "rising": "飙升关键词",
}
# TXT 列顺序（与 tiktok_page_fullrow.py FULL_ROW_FIELDS 一致）
TXT_COLUMNS = ["排名", "关键词", "搜索量", "商品点击数", "SKU销售指数", "在售商品", "CTR指数", "CTOR评分"]

# 待清空表（全量覆盖时；category 保留）
CLEAR_TABLES = ["ai_analysis_log", "opportunity_analysis", "keyword_metric", "collection_batch"]

# 采集/入库子进程必须用装有 playwright 的 Python 解释器。
# Coze bash 的 `python` 会命中 desktop-runtime wrapper（无 playwright），
# 因此优先探测显式路径；找不到时回退 sys.executable。
CANDIDATE_PY = [
    r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe",
    r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\pythonw.exe",
]


def _resolve_python() -> str:
    for p in CANDIDATE_PY:
        if Path(p).exists():
            return p
    return sys.executable


PYTHON_BIN = _resolve_python()


def load_tasks() -> list[dict]:
    data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    tasks = data.get("tasks", [])
    meta = data.get("meta", {})
    skipped = meta.get("skipped_count", 0)
    return tasks, meta, skipped


def clear_database() -> None:
    """全量覆盖：清空四表（保留 category）。"""
    with sqlite3.connect(str(DB_PATH)) as conn:
        for tbl in CLEAR_TABLES:
            try:
                conn.execute(f"DELETE FROM {tbl}")
            except sqlite3.Error as e:
                print(f"[WARN] 清空 {tbl} 失败: {e}")
        conn.commit()
    print("[OK] 已清空:", ", ".join(CLEAR_TABLES), "（category 保留）")


def delete_old_txt() -> int:
    """删除 data/TH/ 下所有旧 *.txt，返回删除数量。"""
    if not DATA_TH.exists():
        return 0
    removed = 0
    for p in DATA_TH.rglob("*.txt"):
        try:
            p.unlink()
            removed += 1
        except OSError as e:
            print(f"[WARN] 删除 {p} 失败: {e}")
    print(f"[OK] 已删除旧 TXT: {removed} 个")
    return removed


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"batch_id": None, "started_at": None, "tasks": {}, "summary": {}}


def save_progress(progress: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def save_status(batch_id: str, status: dict) -> None:
    d = BATCHES_DIR / batch_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def run_collect(level1: str, level2: str, page_size: int, source_types: str,
                period: str | None, page_level1: str | None = None,
                page_level2: str | None = None) -> tuple[int, str, str]:
    cmd = [PYTHON_BIN, "-X", "utf8", str(COLLECT_SCRIPT),
           "--level1", level1, "--level2", level2,
           "--page-size", str(page_size), "--source-types", source_types]
    if page_level1 and page_level1 != level1:
        cmd += ["--page-level1", page_level1]
    if page_level2 and page_level2 != level2:
        cmd += ["--page-level2", page_level2]
    if period:
        cmd += ["--period", period]
    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r.returncode, r.stdout[-1500:], r.stderr[-1500:]


def run_normalize(txt: Path, period: str | None, batch_id: str | None) -> tuple[int, str, str]:
    cmd = [PYTHON_BIN, "-X", "utf8", str(NORMALIZE_SCRIPT), "--txt", str(txt)]
    if period:
        cmd += ["--period", period]
    if batch_id:
        cmd += ["--batch-id", batch_id]
    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, r.stdout[-1500:], r.stderr[-1500:]


def task_key(t: dict) -> str:
    return f"{t.get('official_level1')}|{t.get('official_level2')}"


def txt_path_for_source(level1: str, level2: str, source: str) -> Path:
    """单榜单采集后的 TXT 路径（与 tiktok_page_fullrow.py 文件名约定一致）。"""
    label = SOURCE_FILE_LABEL[source]
    return DATA_TH / level1 / level2 / "monthly" / f"TH_{level1}_{level2}_月度_{label}__fullrow.txt"


def merged_txt_path(level1: str, level2: str, period: str) -> Path:
    """hot+rising 合并后的 TXT 路径。"""
    return DATA_TH / level1 / level2 / "monthly" / f"TH_{level1}_{level2}_月度_热门+飙升_{period}_fullrow.txt"


def parse_source(arg: str) -> list[str]:
    """解析 --source 参数，返回标准 source 列表（去重保序）。

    支持：hot / rising / hot+rising / hot,rising 四种写法。
    """
    normalized = arg.replace("+", ",").lower()
    parts = [s.strip() for s in normalized.split(",") if s.strip()]
    valid = [s for s in parts if s in ("hot", "rising")]
    seen, result = set(), []
    for s in valid:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _parse_fullrow_txt(path: Path) -> tuple[str, list[dict]]:
    """解析 FULLROW TXT，返回 (date_line, rows)。rows: [{列名: 值}]。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    date_line = ""
    header_idx, headers = None, []
    for i, ln in enumerate(lines):
        if ln.startswith("排名|"):
            header_idx = i
            headers = [c.strip() for c in ln.split("|")]
            break
        if ln.startswith("日期："):
            date_line = ln
    if header_idx is None:
        raise ValueError(f"未找到表头行（排名|...）: {path}")
    rows = []
    for ln in lines[header_idx + 1:]:
        ln = ln.strip()
        if not ln:
            continue
        parts = [c.strip() for c in ln.split("|")]
        if len(parts) < len(headers):
            continue
        rows.append({h: parts[i] for i, h in enumerate(headers)})
    return date_line, rows


def merge_hot_rising_txt(hot_txt: Path, rising_txt: Path,
                         level1: str, level2: str,
                         period: str | None) -> tuple[Path, str]:
    """合并 hot + rising TXT：按 keyword 去重（热门优先、飙升补充新词），排名重排 1..N。

    返回 (合并后 TXT 路径, 实际使用的 period)。
    合并后文件名：TH_<L1>_<L2>_月度_热门+飙升_<period>_fullrow.txt
    """
    date_line, hot_rows = _parse_fullrow_txt(hot_txt)
    _, rising_rows = _parse_fullrow_txt(rising_txt)

    # period：优先参数，其次从日期行提取 YYYY-MM，最后取当前月
    eff_period = period
    if not eff_period:
        m = re.search(r"(\d{4}-\d{2})", date_line)
        eff_period = m.group(1) if m else datetime.now().strftime("%Y-%m")

    # 合并去重：热门优先，飙升补充新词（同一关键词只保留首次出现行）
    merged: dict[str, dict] = {}
    order: list[str] = []
    for r in hot_rows + rising_rows:
        kw = (r.get("关键词") or "").strip()
        if not kw or kw in merged:
            continue
        merged[kw] = dict(r)
        order.append(kw)

    # 写入合并 TXT（排名重排 1..N）
    lines = [
        f"TikTok 泰国｜{level1}｜{level2}｜热门+飙升",
        date_line or f"日期：{eff_period}",
        "=" * 50,
        "|".join(TXT_COLUMNS),
    ]
    for idx, kw in enumerate(order, start=1):
        row = merged[kw]
        vals = [str(row.get(c, "") or "") for c in TXT_COLUMNS]
        vals[1] = vals[1].replace("|", " ")  # 关键词中避免 |
        vals[0] = str(idx)                  # 排名重排
        lines.append("|".join(vals))

    out_path = merged_txt_path(level1, level2, eff_period)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # 清理中间文件
    for p in (hot_txt, rising_txt):
        try:
            p.unlink()
        except OSError:
            pass

    return out_path, eff_period


def collect_and_merge(level1: str, level2: str,
                      page_level1: str, page_level2: str,
                      page_size: int, period: str | None) -> tuple[Path, str]:
    """hot+rising 模式：分别采集 hot / rising，合并去重后返回 (合并 TXT 路径, period)。"""
    # 第一次：hot
    rc, out, err = run_collect(level1, level2, page_size, "hot", period,
                               page_level1=page_level1, page_level2=page_level2)
    if rc != 0:
        raise RuntimeError(f"采集(hot)失败 rc={rc}\n{out[-800:]}\n{err[-800:]}")
    print(f"  [COLLECT hot OK] {out.strip().splitlines()[-1] if out.strip() else 'done'}")
    hot_txt = txt_path_for_source(level1, level2, "hot")
    if not hot_txt.exists():
        raise RuntimeError("采集(hot)成功但未找到 TXT 文件")

    # 第二次：rising
    rc, out, err = run_collect(level1, level2, page_size, "rising", period,
                               page_level1=page_level1, page_level2=page_level2)
    if rc != 0:
        raise RuntimeError(f"采集(rising)失败 rc={rc}\n{out[-800:]}\n{err[-800:]}")
    print(f"  [COLLECT rising OK] {out.strip().splitlines()[-1] if out.strip() else 'done'}")
    rising_txt = txt_path_for_source(level1, level2, "rising")
    if not rising_txt.exists():
        raise RuntimeError("采集(rising)成功但未找到 TXT 文件")

    merged, eff_period = merge_hot_rising_txt(hot_txt, rising_txt, level1, level2, period)
    print(f"  [MERGE OK] {merged.name}（合并去重后排名重排）")
    return merged, eff_period


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="fullrow")
    ap.add_argument("--page-size", type=int, default=30)
    ap.add_argument("--source", default="hot+rising")
    ap.add_argument("--overwrite-all", action="store_true",
                    help="全量覆盖：清空四表 + 删旧 TXT + 重采集")
    ap.add_argument("--ensure-schema", action="store_true",
                    help="表结构缺列时自动执行 ALTER（需用户确认后使用）")
    ap.add_argument("--period", default=None, help="周期如 2026-08（默认从 TXT 文件名提取）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理前 N 个可执行任务（0=不限制）")
    args = ap.parse_args(argv)

    if args.mode != "fullrow":
        print(f"[ERROR] 仅支持 --mode fullrow（当前: {args.mode}）")
        return 2
    sources = parse_source(args.source)
    if not sources:
        print(f"[ERROR] --source 无效: {args.source}（支持: hot / rising / hot+rising / hot,rising）")
        return 2
    is_merge = set(sources) == {"hot", "rising"}
    source_label = "+".join(sources)  # hot / rising / hot+rising

    # ---- 表结构前置检查 ----
    mb, mk = check_schema(DB_PATH)
    if mb or mk:
        print("[SCHEMA] 表结构不满足 fullrow 需求（不自动改表）")
        print(f"  collection_batch 缺列: {mb or '(无)'}")
        print(f"  keyword_metric 缺列: {mk or '(无)'}")
        print("\n请执行以下 ALTER SQL 后重跑：\n" + alter_sql())
        if args.ensure_schema:
            print("[SCHEMA] --ensure-schema 已指定，执行扩展列...")
            ensure_schema(DB_PATH)
            print("[SCHEMA] 扩展列完成")
        else:
            print("[SCHEMA] 如需自动执行，加 --ensure-schema（仅 ADD COLUMN，不删除/不改类型）")
            return 3

    tasks, meta, skipped = load_tasks()
    executables = [t for t in tasks if t.get("execute", True)]
    print(f"[TASKS] 任务清单: 共 {len(tasks)}，可执行 {len(executables)}，跳过 {len(tasks) - len(executables)}（meta.skipped={skipped}）")

    # ---- --limit：只处理前 N 个可执行任务 ----
    if args.limit > 0:
        executables = executables[:args.limit]
        print(f"[LIMIT] --limit={args.limit}，本批仅处理前 {len(executables)} 个任务")

    # ---- 全量覆盖：清空 + 删旧 TXT ----
    if args.overwrite_all:
        print("\n[OVERWRITE] 全量覆盖模式")
        clear_database()
        delete_old_txt()
        # 覆盖模式视为新批次，重置进度
        if PROGRESS_FILE.exists():
            try:
                PROGRESS_FILE.unlink()
                print("[OVERWRITE] 已重置断点进度")
            except OSError:
                pass

    progress = load_progress()
    if progress.get("batch_id") is None:
        progress["batch_id"] = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        progress["started_at"] = datetime.now().isoformat(timespec="seconds")
        progress["tasks"] = {}
        progress["summary"] = {"total": len(executables), "done": 0, "failed": 0, "skipped_continue": 0}
    batch_id = progress["batch_id"]
    summary = progress["summary"]
    summary.setdefault("total", len(executables))
    summary.setdefault("done", 0)
    summary.setdefault("failed", 0)
    summary.setdefault("skipped_continue", 0)

    print(f"[BATCH] {batch_id} | 断点: 已完成 {summary.get('done', 0)} / {len(executables)}")

    failed_list = []
    started_all = time.time()
    for idx, t in enumerate(executables, start=1):
        l1 = t.get("official_level1") or t.get("page_level1")
        l2 = t.get("official_level2") or t.get("page_level2")
        pl1 = t.get("page_level1") or l1
        pl2 = t.get("page_level2") or l2
        if not l1 or not l2:
            continue
        key = task_key(t)
        # 断点续跑：已完成跳过
        if progress["tasks"].get(key, {}).get("status") == "done":
            summary["skipped_continue"] = summary.get("skipped_continue", 0) + 1
            continue
        if progress["tasks"].get(key, {}).get("status") == "failed":
            # 失败任务重跑（允许重试）
            pass

        print(f"\n[{idx}/{len(executables)}] {l1} / {l2}")
        error_msg = ""
        try:
            if is_merge:
                # hot+rising：分两次采集，合并去重（热门优先、飙升补充新词）
                txt, eff_period = collect_and_merge(l1, l2, pl1, pl2,
                                                    args.page_size, args.period)
            else:
                # 单榜：一次采集
                src = sources[0]
                rc, out, err = run_collect(l1, l2, args.page_size, src, args.period,
                                           page_level1=pl1, page_level2=pl2)
                if rc != 0:
                    raise RuntimeError(f"采集失败 rc={rc}\n{out[-800:]}\n{err[-800:]}")
                print(f"  [COLLECT OK] {out.strip().splitlines()[-1] if out.strip() else 'done'}")
                txt = txt_path_for_source(l1, l2, src)
                if not txt.exists():
                    raise RuntimeError("采集成功但未找到 TXT 文件")
                eff_period = args.period
            rc2, out2, err2 = run_normalize(txt, eff_period, batch_id)
            if rc2 != 0:
                raise RuntimeError(f"入库失败 rc={rc2}\n{out2[-800:]}\n{err2[-800:]}")
            print(f"  [NORMALIZE OK] {out2.strip().splitlines()[-1] if out2.strip() else 'done'}")
            status = "done"
        except Exception as e:
            status = "failed"
            error_msg = str(e)[:500]
            failed_list.append({"task": key, "l1": l1, "l2": l2, "error": error_msg})
            print(f"  [ERROR] {error_msg}")

        progress["tasks"][key] = {
            "status": status,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "error": error_msg,
        }
        if status == "done":
            summary["done"] = summary.get("done", 0) + 1
        else:
            summary["failed"] = summary.get("failed", 0) + 1
        save_progress(progress)

        # 每 10 个任务同步一次状态文件
        if idx % 10 == 0 or idx == len(executables):
            save_status(batch_id, {
                "batch_id": batch_id,
                "mode": args.mode,
                "source": source_label,
                "page_size": args.page_size,
                "overwrite_all": args.overwrite_all,
                "started_at": progress["started_at"],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_sec": round(time.time() - started_all, 1),
                "progress": progress["tasks"],
                "summary": summary,
                "failed": failed_list,
            })

    save_status(batch_id, {
        "batch_id": batch_id,
        "mode": args.mode,
        "source": source_label,
        "page_size": args.page_size,
        "overwrite_all": args.overwrite_all,
        "started_at": progress["started_at"],
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - started_all, 1),
        "progress": progress["tasks"],
        "summary": summary,
        "failed": failed_list,
    })
    save_progress(progress)

    print("\n" + "=" * 70)
    print(f"[DONE] batch_id={batch_id}")
    print(f"  总计: {len(executables)} | 成功: {summary.get('done', 0)} | 失败: {summary.get('failed', 0)} | 续跑跳过: {summary.get('skipped_continue', 0)}")

    # ========== 失败预警 ==========
    _failed_count = summary.get("failed", 0)
    _tasks = progress.get("tasks", {}) if isinstance(progress, dict) else {}

    _collect_fails = []
    _normalize_fails = []
    _other_fails = []
    for _tn, _info in _tasks.items():
        if isinstance(_info, dict) and _info.get("status") == "failed":
            _err = (_info.get("error") or "")
            if _err.startswith("入库失败"):
                _normalize_fails.append((_tn, _err[:200]))
            elif _err.startswith("采集"):
                _collect_fails.append((_tn, _err[:200]))
            else:
                _other_fails.append((_tn, _err[:200]))

    if _failed_count > 0:
        _alert_lines = [
            f"批次: {batch_id}",
            f"失败总数: {_failed_count}",
            f"  采集失败: {len(_collect_fails)}",
            f"  入库失败: {len(_normalize_fails)}",
            f"  其他失败: {len(_other_fails)}",
            "-" * 50,
        ]
        for _tn, _err in _collect_fails:
            _alert_lines.append(f"  [采集] {_tn}: {_err}")
        for _tn, _err in _normalize_fails:
            _alert_lines.append(f"  [入库] {_tn}: {_err}")
        for _tn, _err in _other_fails:
            _alert_lines.append(f"  [其他] {_tn}: {_err}")
        Path("FAILED_THIS_RUN.txt").write_text("\n".join(_alert_lines), encoding="utf-8")
        print()
        print("=" * 60)
        print(f"  [ALERT] 有 {_failed_count} 个任务失败")
        print(f"    - 采集失败: {len(_collect_fails)}")
        print(f"    - 入库失败: {len(_normalize_fails)}")
        print(f"    - 其他:     {len(_other_fails)}")
        print(f"  [ALERT] 详见: FAILED_THIS_RUN.txt")
        print("=" * 60)
    else:
        print()
        print("=" * 60)
        print("  [OK] 无失败任务")
        print("=" * 60)

    return 0 if not failed_list else 1


if __name__ == "__main__":
    sys.exit(main())
