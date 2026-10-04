# -*- coding: utf-8 -*-
"""TikTok 关键词数据采集器。

两条路径，均为确定性 Python，不调用 AI：
1) import   ：解析 data/raw/tiktok/thailand/ 下已导出的 Excel/CSV → SQLite（第一阶段主路径）
2) browser  ：调用已验证的 Playwright 导出脚本（project/automation/tiktok/tiktok_keyword_export.py），
              在真实卖家后台点「导出数据→下载」，文件落到原始目录后再导入。
              ——登录态复用 .run/profile；若需登录/验证码，脚本会提示，由用户手动完成一次。

浏览器自动化职责边界（AI 绝不参与）：定位固定按钮→点击→等待下载→保存文件。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from app import config
from app.database import KeywordRepo, connect, init_db
from app.tiktok.parser import parse_export_file

_EXPORT_GLOBS = ("*.xlsx", "*.xlsm", "*.csv")


def find_export_files(raw_dir: Optional[Path] = None) -> list[Path]:
    """原始导出目录下候选文件（按文件名排序，稳定）。"""
    d = Path(raw_dir) if raw_dir else config.DATA_RAW_TIKTOK_THAILAND
    if not d.exists():
        return []
    files: list[Path] = []
    for g in _EXPORT_GLOBS:
        files.extend(d.glob(g))
    return sorted(set(files), key=lambda p: p.name)


def import_export_file(
    file_path: str | Path,
    db_path: Optional[str | Path] = None,
    *,
    market: Optional[str] = None,
    category: Optional[str] = None,
    keyword_type: Optional[str] = None,
    level_1_category: Optional[str] = None,
    level_2_category: Optional[str] = None,
) -> dict:
    """导入单个导出文件 → keyword_data（统一导入入口）。

    四类数据源均走此入口：一级/二级 × 周/月。
      - 一级数据: level_1_category=一级类目(如 时尚配件), level_2_category=None, category=一级类目名
      - 二级数据: level_1_category=所属一级, level_2_category=二级类目(如 平价饰品),
                  category=二级类目名（与一级自然键隔离）
    返回报告 dict；周期无法识别(period_type=unknown)时抛 ValueError（不写入，
    避免无法保证历史可比性的脏数据）。
    """
    parsed = parse_export_file(
        file_path,
        market=market or config.DEFAULT_MARKET,
        category=category,
        keyword_type=keyword_type,
        level_1_category=level_1_category,
        level_2_category=level_2_category,
    )
    if parsed["period_type"] == "unknown":
        raise ValueError(
            f"无法从导出元信息[日期范围]识别周期(月/周): {Path(file_path).name}，拒绝导入"
        )

    conn = connect(db_path)
    try:
        init_db(conn)
        repo = KeywordRepo(conn)
        inserted, skipped = repo.insert_many(parsed["records"])
    finally:
        conn.close()

    return {
        "file": Path(file_path).name,
        "period_start": parsed["period_start"],
        "period_end": parsed["period_end"],
        "period_type": parsed["period_type"],
        "category": parsed["category"],
        "level_1_category": parsed["level_1_category"],
        "level_2_category": parsed["level_2_category"],
        "keyword_type": parsed["keyword_type"],
        "file_hash": parsed["file_hash"],
        "n_records": parsed["n_records"],
        "inserted": inserted,
        "skipped_duplicates": skipped,
    }


def import_all(
    raw_dir: Optional[Path] = None,
    db_path: Optional[str | Path] = None,
    *,
    market: Optional[str] = None,
    category: Optional[str] = None,
    keyword_type: Optional[str] = None,
    level_1_category: Optional[str] = None,
    level_2_category: Optional[str] = None,
) -> list[dict]:
    """导入目录下全部导出文件；单文件失败不影响其它文件，错误记入报告。"""
    reports = []
    for f in find_export_files(raw_dir):
        try:
            reports.append(import_export_file(
                f, db_path, market=market, category=category, keyword_type=keyword_type,
                level_1_category=level_1_category, level_2_category=level_2_category))
        except Exception as e:  # noqa: BLE001 —— 采集报告需要兜住单文件错误
            reports.append({"file": f.name, "error": f"{type(e).__name__}: {e}"})
    return reports


class LoginRequiredError(RuntimeError):
    """浏览器采集要求用户手动登录一次。"""


def run_browser_export(
    script: Optional[Path] = None,
    result_json: Optional[Path] = None,
    timeout_s: int = 1800,
    *,
    level_1: Optional[str] = None,
    level_2: Optional[str] = None,
    keyword_type: Optional[str] = None,
    granularity: Optional[str] = None,
    probe_max_months: int = 3,
    no_save: bool = False,
) -> dict:
    """运行已验证的 Playwright 导出脚本（确定性，无 AI），返回采集结果 dict。

    返回的 dict 至少包含：
      - final_file / final_filename / final_size_bytes：最终落盘信息
      - latest_period: {period_start, period_end, ui_month_cn, file_hash, n_records, stage_file}
      - mapping_table: UI 月份 → Excel 实际周期的探测映射表（含 valid/error）
      - available_ui_months: UI 当前可用月份列表（按从新到旧）
      - probed_count: 实际探测次数

    - 脚本退出码 3 = 需要手动登录（用户登录 .run/profile 后重跑即可）
    - 脚本把结果写入 result_json（.run/export_result.json）
    - level_1/level_2/keyword_type 传入脚本，决定采集哪个类目/榜单类型。
    - probe_max_months: 从最新 UI 月份向前探测的最大次数（实际数据周期判定）
    - no_save: 只探测不落盘（调试用）
    """
    script = script or config.TIKTOK_EXPORT_SCRIPT
    result_json = result_json or config.TIKTOK_EXPORT_RESULT_JSON
    if not script.exists():
        raise FileNotFoundError(f"导出自动化脚本不存在: {script}")

    cmd = [sys.executable, str(script)]
    if level_1:
        cmd += ["--level1", level_1]
    if level_2:
        cmd += ["--level2", level_2]
    if keyword_type:
        cmd += ["--keyword-type", keyword_type]
    cmd += ["--probe-max-months", str(probe_max_months)]
    if granularity:
        cmd += ["--granularity", granularity]
    if no_save:
        cmd += ["--no-save"]

    # Clear stale result JSON before running (avoid masking failures with old data)
    try:
        result_json.unlink(missing_ok=True)
    except Exception:
        pass

    proc = subprocess.run(cmd, timeout=timeout_s, cwd=str(config.PROJECT_ROOT))
    if proc.returncode == 3:
        raise LoginRequiredError(
            "需要手动登录卖家后台一次（脚本已打开浏览器）。登录后重跑本命令即可，"
            "账号密码请勿提供给任何人/写入代码。"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"导出脚本异常退出(returncode={proc.returncode})，"
            f"请检查 .run/export_result.json 或脚本 stdout 获取详细错误"
        )

    if not result_json.exists():
        raise RuntimeError(f"导出脚本未产出结果文件 {result_json}（返回码 {proc.returncode}）")
    result = json.loads(result_json.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"导出失败: {result.get('error', result)}")
    return result
