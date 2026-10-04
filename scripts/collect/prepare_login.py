# -*- coding: utf-8 -*-
"""TikTok Seller Center 登录准备脚本。用户登录完成后按回车继续。"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = r"D:\tiktok-ai-sourcing\project\.run\profile"
BASE_URL = "https://seller.tiktokshopglobalselling.com/"


def _is_logged_in(page) -> bool:
    try:
        url = (page.url or "").lower()
        if "login" in url or "account/login" in url:
            return False
        return True
    except Exception:
        return False


def main() -> int:
    print("=" * 70)
    print("TikTok Seller Center 登录准备")
    print("=" * 70)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, channel="chrome", headless=False,
            viewport={"width": 1680, "height": 950}, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
            ignore_default_args=["--enable-automation"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print()
        print("[1] 打开 TikTok Seller Center")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        if _is_logged_in(page):
            print("[OK] 已登录，无需操作。3 秒后自动关闭")
            time.sleep(3)
            ctx.close()
            return 0

        print()
        print("=" * 70)
        print("请在打开的 Chrome 窗口里完成登录（含验证码）。")
        print("登录完成后，回到这个 PowerShell 窗口，按【回车键】继续。")
        print("=" * 70)
        try:
            input("登录完成后按回车：")
        except EOFError:
            print("[WARN] 无法读取输入，改为自动检测（最多 15 分钟）")
            for i in range(300):
                time.sleep(3)
                if _is_logged_in(page):
                    print("[OK] 自动检测到已登录")
                    break
                if (i+1) % 20 == 0:
                    print("[等待] " + str((i+1)*3) + " 秒...")

        page.wait_for_timeout(2000)
        if _is_logged_in(page):
            print("[OK] 登录态已确认。关闭浏览器")
            time.sleep(1)
            ctx.close()
            return 0
        else:
            print("[ERROR] 检测到仍未登录（可能跳到了登录页）。浏览器保持打开，请检查后重新运行本脚本。")
            input("检查完后按回车关闭浏览器：")
            ctx.close()
            return 3


if __name__ == "__main__":
    sys.exit(main())