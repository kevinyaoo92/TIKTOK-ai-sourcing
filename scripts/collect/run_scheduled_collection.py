# -*- coding: utf-8 -*-
"""TikTok Seller Center 生产定时采集入口（脱离 Coze 独立运行）。

设计目标：
- 完全复用已验证的 tiktok_page_top20.py 采集逻辑（main()），不重写页面采集。
- 不依赖 Coze Agent / Coze SDK / 任何环境变量或外部服务，可直接由 Windows
  Task Scheduler 定时启动。
- 每次运行独立批次目录（data/_batches/<run_ts>/），只有批次内全部字段采集成功
  且 TXT 结构校验通过后，才提升为标准数据（data/TH/家居用品/<L2>/monthly/）。
- 失败保护：新一轮失败不删除、不覆盖上一轮已验证数据；标准位置旧文件在提升前
  归档到 monthly/_history/。
- 互斥锁：同一时刻只允许一个采集实例，避免定时任务重叠。

用法：
    python scripts/collect/run_scheduled_collection.py
    python scripts/collect/run_scheduled_collection.py --level1 家居用品
    python scripts/collect/run_scheduled_collection.py --level1 家居用品 --level2 卫浴用品 --tab 热门搜索关键词
    python scripts/collect/run_scheduled_collection.py --period month --no-promote

退出码：0=全部成功；1=存在失败（供任务计划程序记录）。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "collect"))

# 采集程序（已验证，不改动其逻辑，只调用 main()）
import tiktok_page_top20  # noqa: E402

# 官方类目加载器（TXT 是唯一类目来源；不硬编码 L1/L2 清单）
from app.category_loader import load_catalog  # noqa: E402

# ---------------- 默认配置 ----------------
MARKET = "TH"
LEVEL_1 = "家居用品"
DEFAULT_TABS = ["热门搜索关键词"]
PERIOD = "month"

# 官方类目名 → 页面显示名 适配表（仅官方名与页面显示名不一致时使用；
# 不修改官方类目名，产物/校验/路径一律使用官方名，页面点击/校验用显示名）
LEVEL2_PAGE_NAME_ALIAS = {
    "家居收纳": "家居收纳用品",
    "浴室用品": "卫浴用品",
    "节庆及派对用品": "节日和派对用品",
}

DATA_ROOT = PROJECT_ROOT / "data"
BATCH_ROOT = DATA_ROOT / "_batches"
LOG_ROOT = PROJECT_ROOT / ".runtime" / "logs"
LOCK_FILE = BATCH_ROOT / ".lock"

EXPECTED_FIELDS = ["搜索量", "商品点击数", "SKU销售指数", "在售商品", "CTR指数", "CTOR评分"]


def setup_logging(run_ts: str) -> logging.Logger:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("scheduled_collection")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_ROOT / f"scheduled_{run_ts}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def parse_display_num(v: str) -> float:
    s = v.replace(",", "").strip()
    mult = 1.0
    if s.upper().endswith("K"):
        mult, s = 1000.0, s[:-1]
    elif s.upper().endswith("M"):
        mult, s = 1000000.0, s[:-1]
    try:
        return float(s) * mult
    except Exception:
        return float("-inf")


def verify_txt(path: Path, level2: str, tab: str) -> tuple[bool, list[str]]:
    """校验采集产物 TXT 的结构与数据质量。"""
    errors: list[str] = []
    if not path.exists():
        return False, [f"TXT 不存在: {path}"]
    if path.stat().st_size == 0:
        return False, [f"TXT 为空: {path}"]
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"TXT 读取失败: {e}"]

    lines = txt.splitlines()
    if not lines:
        return False, ["TXT 无内容"]
    header = lines[0]
    if level2 not in header:
        errors.append(f"标题缺少类目「{level2}」: {header}")

    sections = re.split(r"\n【(.+?) TOP(\d+)｜高→低】", txt)
    found = {}
    for i in range(1, len(sections), 3):
        field = sections[i].strip()
        body = sections[i + 2]
        rows = []
        for l in [x for x in body.strip().splitlines() if x.strip()]:
            m = re.match(r"^(\d+)\. (.+?) — (.+)$", l.strip())
            if m:
                rows.append((int(m.group(1)), m.group(2), m.group(3)))
        found[field] = rows

    for field in EXPECTED_FIELDS:
        rows = found.get(field)
        if not rows:
            errors.append(f"缺少字段章节: {field}")
            continue
        if [r[0] for r in rows] != list(range(1, len(rows) + 1)):
            errors.append(f"{field} 排名不连续")
        if any(not r[1].strip() for r in rows):
            errors.append(f"{field} 存在空关键词")
        nums = [parse_display_num(r[2]) for r in rows]
        if any(n == float("-inf") for n in nums):
            errors.append(f"{field} 存在无法解析的数值")
        elif any(nums[i] < nums[i + 1] for i in range(len(nums) - 1)):
            errors.append(f"{field} 非降序")

    # 校验章节数量与期望一致（页面不足20条时按实际条数，允许 <20）
    if len(found) < len(EXPECTED_FIELDS):
        errors.append(f"章节数不足: {len(found)}/{len(EXPECTED_FIELDS)}")
    return (len(errors) == 0), errors


def extract_txt_stats(path: Path) -> dict:
    """提取 TXT 的六维条数与实际数据周期（用于批次报告）。

    周期取自文件名中最后的两个 yyyy-MM-dd 段。
    返回: {"field_counts": {field: n}, "period_start": str, "period_end": str}
    """
    counts: dict[str, int] = {}
    period_start = period_end = ""
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return {"field_counts": counts, "period_start": period_start, "period_end": period_end}
    sections = re.split(r"\n【(.+?) TOP(\d+)｜高→低】", txt)
    for i in range(1, len(sections), 3):
        field = sections[i].strip()
        body = sections[i + 2]
        rows = [l for l in body.strip().splitlines() if l.strip()]
        counts[field] = len(rows)
    m = re.search(r"_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.txt$", path.name)
    if m:
        period_start, period_end = m.group(1), m.group(2)
    return {"field_counts": counts, "period_start": period_start, "period_end": period_end}


def level2_page_name(level2: str) -> str:
    """官方类目名 → 页面显示名（无映射时直接用官方名）。"""
    return LEVEL2_PAGE_NAME_ALIAS.get(level2, level2)


def promote_batch(batch_l2_dir: Path, level1: str, level2: str,
                  run_ts: str, logger: logging.Logger) -> None:
    """把批次目录内验证通过的 TXT 提升为标准数据，旧文件先归档。"""
    std_base = DATA_ROOT / MARKET / level1 / level2 / "monthly"
    std_base.mkdir(parents=True, exist_ok=True)
    history_dir = std_base / "_history"
    for txt in sorted(batch_l2_dir.glob("monthly/*.txt")):
        target = std_base / txt.name
        if target.exists():
            # 旧数据归档，不删除
            history_dir.mkdir(parents=True, exist_ok=True)
            archived = history_dir / f"{run_ts}_{target.name}"
            target.replace(archived)
            logger.info("[promote] 旧数据归档: %s -> %s", target.name, archived.name)
        txt.replace(target)
        logger.info("[promote] 新数据提升: %s", target)


def acquire_lock(logger: logging.Logger) -> bool:
    try:
        BATCH_ROOT.mkdir(parents=True, exist_ok=True)
        fd = LOCK_FILE.open("x")
        fd.write(str(datetime.now().isoformat()))
        fd.close()
        return True
    except FileExistsError:
        logger.warning("检测到已有采集实例在运行（%s 存在），本次跳过", LOCK_FILE)
        return False


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def run_collection(logger: logging.Logger, level1: str, level2: str, tab: str, period: str,
                   batch_l2_dir: Path, no_promote: bool, run_ts: str) -> dict:
    entry = {"level1": level1, "level2": level2, "tab": tab, "period": period,
             "status": "FAIL", "error": None, "page_level2": level2_page_name(level2)}
    batch_l2_dir.mkdir(parents=True, exist_ok=True)
    try:
        page_name = level2_page_name(level2)
        logger.info(">>> 开始采集: %s / %s (页面名: %s) / %s / %s",
                    level1, level2, page_name, tab, period)
        argv = [
            "--period", period,
            "--tab", tab,
            "--level1", level1,
            "--level2", level2,
            "--level2-page-name", page_name,
            "--output-dir", str(batch_l2_dir),
        ]
        rc = tiktok_page_top20.main(argv)
        if rc != 0:
            entry["error"] = "采集主流程返回失败码"
            logger.error("<<< 采集主流程失败: %s / %s (rc=%s)", level2, tab, rc)
            return entry

        # 采集成功，定位产物并校验
        files = sorted(batch_l2_dir.glob("monthly/*.txt"))
        if not files:
            entry["error"] = "采集成功但未找到 TXT 产物"
            logger.error("<<< 未找到 TXT 产物: %s", batch_l2_dir)
            return entry

        all_ok = True
        field_counts: dict[str, int] = {}
        period_start = period_end = ""
        for f in files:
            ok, errors = verify_txt(f, level2, tab)
            if not ok:
                all_ok = False
                for e in errors:
                    logger.error("[校验失败] %s: %s", f.name, e)
            stats = extract_txt_stats(f)
            field_counts = stats["field_counts"] or field_counts
            if stats["period_start"]:
                period_start, period_end = stats["period_start"], stats["period_end"]
        entry["field_counts"] = field_counts
        entry["period_start"] = period_start
        entry["period_end"] = period_end
        if not all_ok:
            entry["error"] = "TXT 结构校验未通过，未提升"
            logger.error("<<< 校验未通过，标准数据保持不变")
            return entry

        if no_promote:
            logger.info("<<< --no-promote 模式：保留在批次目录 %s", batch_l2_dir)
        else:
            promote_batch(batch_l2_dir, level1, level2, run_ts, logger)
            logger.info("<<< 已提升为标准数据: %s", files[0].name)
        entry["status"] = "SUCCESS"
        entry["files"] = [f.name for f in files]
        return entry
    except Exception as e:  # noqa: BLE001 - 定时任务需要兜底记录
        entry["error"] = f"异常: {type(e).__name__}: {e}"
        logger.exception("<<< 采集异常: %s / %s", level2, tab)
        return entry


def entry_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TikTok 生产定时采集入口")
    ap.add_argument("--level1", default=LEVEL_1,
                    help="一级类目（官方名），默认: " + LEVEL_1)
    ap.add_argument("--level2", action="append", default=None,
                    help="指定二级类目（官方名，可多次）；缺省时从官方类目清单读取该 L1 下全部 L2")
    ap.add_argument("--tab", action="append", default=None,
                    help="指定榜单（可多次），默认: " + ",".join(DEFAULT_TABS))
    ap.add_argument("--period", choices=["month", "week"], default=PERIOD)
    ap.add_argument("--no-promote", action="store_true",
                    help="只采集到批次目录，不提升标准数据（用于演练）")
    args = ap.parse_args(argv)

    run_ts = entry_ts()
    logger = setup_logging(run_ts)
    logger.info("=" * 60)
    logger.info("TikTok 生产定时采集启动 run_ts=%s", run_ts)

    if not acquire_lock(logger):
        return 1
    try:
        level1 = args.level1
        if args.level2:
            level2s = args.level2
        else:
            # 从官方类目清单读取该 L1 下的全部 L2（TXT 是唯一类目来源）
            try:
                catalog = load_catalog()
                l2s: list[str] = []
                for e in catalog.entries:
                    if e.level1 == level1:
                        l2s = list(e.level2)
                        break
                if not l2s:
                    logger.error("官方类目清单中未找到 L1「%s」", level1)
                    return 1
                level2s = l2s
                logger.info("从官方类目清单读取 L1「%s」下 %d 个 L2: %s",
                            level1, len(level2s), ", ".join(level2s))
            except Exception as e:  # noqa: BLE001
                logger.error("读取官方类目清单失败: %s", e)
                return 1
        tabs = args.tab or DEFAULT_TABS
        period = args.period
        run_root = BATCH_ROOT / run_ts
        run_root.mkdir(parents=True, exist_ok=True)

        results = []
        for level2 in level2s:
            for tab in tabs:
                batch_l2_dir = run_root / level1 / level2
                results.append(run_collection(
                    logger, level1, level2, tab, period, batch_l2_dir, args.no_promote, run_ts))

        ok_count = sum(1 for r in results if r["status"] == "SUCCESS")
        status = {
            "run_ts": run_ts,
            "period": period,
            "market": MARKET,
            "level1": level1,
            "level2s": level2s,
            "started_at": run_ts,
            "finished_at": entry_ts(),
            "results": results,
            "success": ok_count,
            "total": len(results),
            "all_success": ok_count == len(results),
        }
        (run_root / "status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("运行汇总: %s/%s 成功", ok_count, len(results))
        logger.info("状态文件: %s", run_root / "status.json")
        return 0 if ok_count == len(results) else 1
    finally:
        release_lock()
        logger.info("结束")


if __name__ == "__main__":
    sys.exit(main())
