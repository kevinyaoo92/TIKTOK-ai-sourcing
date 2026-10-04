# -*- coding: utf-8 -*-
"""run_all_collection 执行器单元测试（mock 采集核心，不启动真实浏览器）。

覆盖：
1. 只执行 execute=true 的任务（SKIPPED 不调用采集核心）
2. 单任务失败不中断后续任务
3. 断点恢复：已 SUCCESS 任务不重复采集
4. --level2-page-name 正确传入页面显示名（ALIAS）
5. 进度文件 / 批次记录正确保存
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "collect"))

import run_all_collection as rac  # noqa: E402


def _make_tasks_file(tmp_path: Path, tasks: list[dict]) -> Path:
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")
    return p


def _sample_tasks() -> list[dict]:
    """3 个执行任务（2 MATCH + 1 ALIAS）+ 1 个 SKIPPED。"""
    return [
        {
            "market": "TH", "official_level1": "家居用品", "official_level2": "家居收纳用品",
            "page_level1": "家居用品", "page_level2": "家居收纳用品",
            "match_status": "MATCH", "execute": True, "status": "PENDING",
            "period": "month", "tab": "热门搜索关键词", "reason": "", "verified_at": "2026-09-15T10:00:00",
        },
        {
            "market": "TH", "official_level1": "厨具", "official_level2": "餐具",
            "page_level1": "厨具", "page_level2": "餐具与器皿",
            "match_status": "ALIAS", "execute": True, "status": "PENDING",
            "period": "month", "tab": "热门搜索关键词", "reason": "", "verified_at": "2026-09-15T10:00:00",
        },
        {
            "market": "TH", "official_level1": "鞋靴", "official_level2": "女鞋",
            "page_level1": "鞋靴", "page_level2": "女鞋",
            "match_status": "MATCH", "execute": True, "status": "PENDING",
            "period": "month", "tab": "热门搜索关键词", "reason": "", "verified_at": "2026-09-15T10:00:00",
        },
        {
            "market": "TH", "official_level1": "收藏品", "official_level2": "收藏钱币",
            "page_level1": "收藏品", "page_level2": "",
            "match_status": "FAILED", "execute": False, "status": "SKIPPED",
            "period": "month", "tab": "热门搜索关键词",
            "reason": "二级列中未找到与官方名精确或子串匹配的项", "verified_at": "2026-09-15T10:00:00",
        },
    ]


def _install_fake_collector(monkeypatch, calls: list, result_map=None):
    """mock 采集核心：记录调用 argv，可控制返回码；创建假 TXT 产物。"""
    result_map = result_map or {}

    def fake_main(argv):
        calls.append(list(argv))
        key = None
        for i, a in enumerate(argv):
            if a == "--level2":
                key = argv[i + 1]
                break
        batch_dir = Path([argv[i + 1] for i, a in enumerate(argv) if a == "--output-dir"][0])
        monthly = batch_dir / "monthly"
        monthly.mkdir(parents=True, exist_ok=True)
        (monthly / "fake.txt").write_text("", encoding="utf-8")
        return result_map.get(key, 0)

    monkeypatch.setattr(rac, "_collector", type("FakeCollector", (), {"main": staticmethod(fake_main)})())
    # 校验与统计均 mock（聚焦执行器逻辑）
    monkeypatch.setattr(rac, "verify_txt", lambda *a, **k: (True, []))
    monkeypatch.setattr(rac, "extract_txt_stats", lambda *a, **k: {"field_counts": {"搜索量": 1}})


def _run(monkeypatch, tasks_file: Path, progress: Path, batch_root: Path,
         extra: list | None = None) -> int:
    argv = ["--tasks", str(tasks_file), "--progress", str(progress),
            "--batches-root", str(batch_root)] + (extra or [])
    return rac.main(argv)


# =============================================================================
# 测试 1: 只执行 execute=true
# =============================================================================
def test_only_execute_true_runs(tmp_path, monkeypatch):
    calls: list = []
    _install_fake_collector(monkeypatch, calls)
    tasks_file = _make_tasks_file(tmp_path, _sample_tasks())
    progress = tmp_path / "progress.json"
    batch_root = tmp_path / "batches"

    rc = _run(monkeypatch, tasks_file, progress, batch_root)
    assert rc == 0

    # 只调用 3 个 execute=true 任务，SKIPPED 的收藏钱币不调用
    called_l2 = []
    for argv in calls:
        for i, a in enumerate(argv):
            if a == "--level2":
                called_l2.append(argv[i + 1])
    assert sorted(called_l2) == ["女鞋", "家居收纳用品", "餐具"], f"实际调用: {called_l2}"
    assert "收藏钱币" not in called_l2

    # 进度文件只包含执行的 3 个任务
    prog = json.loads(progress.read_text(encoding="utf-8"))
    assert len(prog["results"]) == 3
    assert all(v["status"] == "SUCCESS" for v in prog["results"].values())
    # SUCCESS 任务不得残留 error（防止日志/校验异常被误吞为成功）
    assert all(v.get("error") is None for v in prog["results"].values())
    print("  ✓ 测试1: 只执行 execute=true，SKIPPED 不调用")


# =============================================================================
# 测试 2: 单任务失败不中断
# =============================================================================
def test_failed_does_not_stop(tmp_path, monkeypatch):
    calls: list = []
    _install_fake_collector(monkeypatch, calls,
                            result_map={"餐具": 1})  # 餐具失败，其余成功
    tasks_file = _make_tasks_file(tmp_path, _sample_tasks())
    progress = tmp_path / "progress.json"
    batch_root = tmp_path / "batches"

    rc = _run(monkeypatch, tasks_file, progress, batch_root)
    assert rc == 1  # 有失败
    assert len(calls) == 3  # 失败不中断，3 个都执行了

    prog = json.loads(progress.read_text(encoding="utf-8"))
    statuses = {k.split("||")[1]: v["status"] for k, v in prog["results"].items()}
    assert statuses["餐具"] == "FAIL", statuses
    assert statuses["家居收纳用品"] == "SUCCESS"
    assert statuses["女鞋"] == "SUCCESS"

    # 批次记录
    batch_status = list((batch_root).glob("*/status.json"))[0]
    bs = json.loads(batch_status.read_text(encoding="utf-8"))
    assert bs["task_total"] == 3 and bs["success"] == 2 and bs["failed"] == 1
    print("  ✓ 测试2: 单任务失败不中断，状态正确保存")


# =============================================================================
# 测试 3: 断点恢复跳过 SUCCESS
# =============================================================================
def test_resume_skips_success(tmp_path, monkeypatch):
    calls: list = []
    _install_fake_collector(monkeypatch, calls)
    tasks_file = _make_tasks_file(tmp_path, _sample_tasks())
    progress = tmp_path / "progress.json"
    batch_root = tmp_path / "batches"

    rc1 = _run(monkeypatch, tasks_file, progress, batch_root)
    assert rc1 == 0 and len(calls) == 3

    # 第二次运行：全部成功任务应跳过，采集核心不再被调用
    rc2 = _run(monkeypatch, tasks_file, progress, batch_root)
    assert rc2 == 0
    assert len(calls) == 3, f"断点恢复后不应重复采集，实际调用 {len(calls)} 次"

    prog = json.loads(progress.read_text(encoding="utf-8"))
    assert len(prog["results"]) == 3
    print("  ✓ 测试3: 断点恢复跳过已成功任务")


# =============================================================================
# 测试 4: ALIAS 任务正确传页面显示名
# =============================================================================
def test_page_level2_passed(tmp_path, monkeypatch):
    calls: list = []
    _install_fake_collector(monkeypatch, calls)
    tasks_file = _make_tasks_file(tmp_path, _sample_tasks())
    progress = tmp_path / "progress.json"
    batch_root = tmp_path / "batches"

    _run(monkeypatch, tasks_file, progress, batch_root)

    # 找到餐具任务的 argv，断言 --level2-page-name 为 餐具与器皿
    argv_zhujv = [a for a in calls if "--level2" in a and a[a.index("--level2") + 1] == "餐具"][0]
    assert "--level2-page-name" in argv_zhujv
    assert argv_zhujv[argv_zhujv.index("--level2-page-name") + 1] == "餐具与器皿"
    print("  ✓ 测试4: --level2-page-name 传入页面显示名（ALIAS）")


# =============================================================================
# 测试 5: --reset-progress 会重跑
# =============================================================================
def test_reset_progress_reruns(tmp_path, monkeypatch):
    calls: list = []
    _install_fake_collector(monkeypatch, calls)
    tasks_file = _make_tasks_file(tmp_path, _sample_tasks())
    progress = tmp_path / "progress.json"
    batch_root = tmp_path / "batches"

    _run(monkeypatch, tasks_file, progress, batch_root)
    assert len(calls) == 3

    # 重置进度后再次运行 → 重新执行 3 个
    _run(monkeypatch, tasks_file, progress, batch_root, extra=["--reset-progress"])
    assert len(calls) == 6, f"--reset-progress 应重新执行，实际调用 {len(calls)} 次"
    print("  ✓ 测试5: --reset-progress 强制重跑")
