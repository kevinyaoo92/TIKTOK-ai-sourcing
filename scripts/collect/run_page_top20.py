"""Page TOP20 采集 CLI 入口（第一阶段测试用）。

用途：在 Windows 本机执行真实 TikTok 页面采集，走完整 batch 管理流程。
第一阶段测试目标：TH → 家居用品 → 家居收纳 → 月度 → 热门搜索关键词

用法:
    python scripts/collect/run_page_top20.py --market TH --level1 "家居用品" --level2 "家居收纳" --ranking-type "热门搜索关键词" --period month

注意：
- 浏览器启动逻辑完全复用 tiktok_page_top20.py 已验证的方式（channel="chrome" + 持久化 Profile）
- 需要 Chrome Profile 已登录 TikTok Seller Center
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "collect"))


def run_page_top20(
    market: str,
    level1: str,
    level2: str | None,
    ranking_type: str,
    period: str = "month",
    profile_path: str | None = None,
    db_path: str | None = None,
) -> dict:
    """执行一次 Page TOP20 完整采集批次。

    返回 collect_page_top20_batch 的结果 dict。
    浏览器启动/导航流程与 tiktok_page_top20.py main() 保持一致。
    """
    from playwright.sync_api import sync_playwright
    from tiktok_page_top20 import (
        PROFILE,
        BASE_URL,
        KEYWORD_URL,
        check_login,
        collect_top20_batch,
        dismiss_chat_popup,
        safe_goto,
    )
    from app.tiktok.collection_manager import collect_page_top20_batch

    prof_dir = profile_path or PROFILE
    print(f"Chrome Profile: {prof_dir}")
    print(f"目标: {market} / {level1} / {level2 or '一级'} / {ranking_type} / {period}")

    def _collect_fn(mkt, l1, l2, rt, pg):
        """包装浏览器操作，供 collection_manager 调用。"""
        with sync_playwright() as p:
            # 启动浏览器：与 tiktok_page_top20.py main() 完全一致
            # （channel="chrome" 复用系统 Chrome，user_data_dir 复用持久化 Profile）
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=prof_dir,
                channel="chrome",
                headless=False,
                viewport={"width": 1680, "height": 950},
                locale="zh-CN",
                args=["--disable-blink-features=AutomationControlled",
                      "--no-first-run", "--no-default-browser-check"],
                ignore_default_args=["--enable-automation"]
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            try:
                # 打开首页 + 登录检查（与 main() 一致）
                safe_goto(page, BASE_URL, 6)
                time.sleep(4)
                if not ctx.pages:
                    raise RuntimeError("浏览器无可用页面")
                page = ctx.pages[0]
                if not check_login(page):
                    raise RuntimeError("未检测到登录状态，请先在 Chrome 中手动登录 TikTok Seller Center")
                print("[OK] 登录状态正常")

                # 进入关键词榜单页面
                safe_goto(page, KEYWORD_URL, 8)
                time.sleep(8)
                dismiss_chat_popup(page)

                # 等待表格区域出现
                try:
                    page.wait_for_selector(".theme-arco-table, [role='tab']", timeout=30000)
                    print("[OK] 页面元素已加载")
                except Exception:
                    print("[WARN] 等待页面元素超时，继续尝试...")

                # 执行采集（collect_top20_batch 内部完成：类目选择/榜单切换/周期选择/六字段排序/TOP20提取）
                result = collect_top20_batch(
                    page=page,
                    market=mkt,
                    level_1=l1,
                    level_2=l2,
                    ranking_type=rt,
                    period_granularity=pg,
                    profile_path=prof_dir,
                )
                return result

            finally:
                ctx.close()

    result = collect_page_top20_batch(
        market=market,
        level_1=level1,
        level_2=level2,
        ranking_type=ranking_type,
        period_granularity=period,
        collect_fn=_collect_fn,
        db_path=db_path,
    )

    return result


def main():
    parser = argparse.ArgumentParser(description="TikTok Seller Center Page TOP20 采集器")
    parser.add_argument("--market", default="TH", help="市场代码，默认 TH")
    parser.add_argument("--level1", required=True, help="一级类目名称（页面原文）")
    parser.add_argument("--level2", default=None, help="二级类目名称（页面原文），不填则只选一级")
    parser.add_argument("--ranking-type", default="热门搜索关键词",
                        choices=["热门搜索关键词", "飙升关键词"],
                        help="榜单类型，默认 热门搜索关键词")
    parser.add_argument("--period", default="month", choices=["month", "week"],
                        help="周期粒度，默认 month")
    parser.add_argument("--profile", default=None, help="Chrome Profile 路径（默认用配置中的路径）")
    parser.add_argument("--db", default=None, help="数据库路径（默认用项目配置）")
    args = parser.parse_args()

    print("=" * 60)
    print("TikTok Page TOP20 采集（Batch 管理模式）")
    print("=" * 60)

    result = run_page_top20(
        market=args.market,
        level1=args.level1,
        level2=args.level2,
        ranking_type=args.ranking_type,
        period=args.period,
        profile_path=args.profile,
        db_path=args.db,
    )

    print("\n" + "=" * 60)
    print(f"采集结果: {result['status']}")
    print(f"Batch ID: {result.get('batch_id', 'N/A')}")
    print(f"周期: {result.get('period_start', 'N/A')} ~ {result.get('period_end', 'N/A')}")
    print(f"记录数: {result.get('n_records', 0)}")
    if result.get("error"):
        print(f"错误: {result['error']}")
    print("=" * 60)

    return 0 if result["status"] in ("SUCCESS", "DUPLICATE") else 1


if __name__ == "__main__":
    sys.exit(main())
