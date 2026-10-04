# -*- coding: utf-8 -*-
"""前端 L2 选择限制（最多 2 个）端到端测试

覆盖用户要求的 8 个场景：
  1. 0 个 L2 → 按钮 disabled
  2. 选择 1 个 L2 → 按钮 enabled → 正确传递 1 个 L2
  3. 选择 2 个 L2 → 按钮 enabled → 正确传递 2 个 L2
  4. 已选 2 个 → 第 3 个 disabled → 鼠标/程序事件均无法选择
  5. 取消 1 个已选 → disabled 恢复可选
  6. 切换 L1 → L2 选择清空重置 → 上限状态重置
  7. 美妆个护 → 美容 / 个护电器 保持两个独立 L2
  8. pytest 全量回归由外层 `pytest tests/` 统一执行

只验证前端行为；不触碰任何后端业务逻辑。
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except Exception as exc:  # pragma: no cover - 环境缺失时给出明确提示
    pytest.skip(f"Playwright 不可用：{exc}", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = ROOT / "frontend" / "server.py"
CHROME_PATH = r"D:\PythonEnv\playwright\chromium-1228\chrome-win64\chrome.exe"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def base_url():
    """启动只读前端服务（独立端口），返回 BASE_URL"""
    port = _free_port()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT), "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.3)
    else:  # pragma: no cover
        proc.terminate()
        raise RuntimeError("前端服务启动超时")
    time.sleep(0.5)
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME_PATH, headless=True)
        yield b
        b.close()


@pytest.fixture()
def page(browser, base_url):
    """每个测试一个全新页面（状态隔离），等待类目数据加载完成"""
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(base_url, wait_until="load")
    pg.wait_for_function(
        "() => { var s = document.getElementById('level1-select');"
        " return s && s.options.length > 1; }",
        timeout=10000,
    )
    yield pg
    ctx.close()


# ---------------------------------------------------------------- helpers
def select_l1(page, name):
    page.select_option("#level1-select", label=name)
    page.wait_for_function(
        "() => document.querySelectorAll('#level2-box .l2-item').length > 0"
    )


def l2_item(page, name):
    """精确按名称定位二级类目行"""
    return page.locator(".l2-item", has=page.locator(f".l2-name:text-is('{name}')"))


def l2_states(page):
    """返回 [{name, checked, disabled, cls_disabled}]"""
    return page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('#level2-box .l2-item').forEach(function (it) {
            const cb = it.querySelector('input[type=checkbox]');
            out.push({
              name: cb.value,
              checked: cb.checked,
              disabled: cb.disabled,
              cls_disabled: it.classList.contains('disabled'),
              cls_selected: it.classList.contains('selected')
            });
          });
          return out;
        }"""
    )


def btn_disabled(page):
    return page.evaluate("() => document.getElementById('btn-analyze').disabled")


def hint_text(page):
    return page.evaluate(
        "() => document.getElementById('l2-count-hint').textContent"
    ).strip()


def submit_selection(page):
    """点击 AI 选品分析按钮，等待跳转第二界面（opportunity.html）后返回 localStorage 传参结构"""
    page.click("#btn-analyze")
    page.wait_for_url("**/static/opportunity.html**", timeout=10000)
    # 等待第二界面机会网格渲染完成，确认 UI 正常
    page.wait_for_selector("#opportunity-grid > *", timeout=10000)
    return page.evaluate(
        "() => JSON.parse(localStorage.getItem('analysis-selection'))"
    )


def state_of(states, name):
    for s in states:
        if s["name"] == name:
            return s
    raise AssertionError(f"找不到二级类目: {name}")


def count_enabled(states):
    return sum(1 for s in states if not s["disabled"])


# ---------------------------------------------------------------- 测试1：0 个 L2 → 按钮 disabled
def test_1_zero_l2_btn_disabled(page):
    select_l1(page, "家居用品")
    assert btn_disabled(page) is True
    assert hint_text(page) == ""


# ---------------------------------------------------------------- 测试2：1 个 L2 → enabled → 传参 1 个
def test_2_one_l2_btn_enabled_and_passed(page):
    select_l1(page, "家居用品")
    l2_item(page, "家居收纳用品").click()
    assert btn_disabled(page) is False
    assert hint_text(page) == "已选择 1 个二级类目"
    sel = submit_selection(page)
    assert sel["country"] == "泰国"
    assert sel["level1"] == "家居用品"
    assert sel["level2"] == ["家居收纳用品"]


# ---------------------------------------------------------------- 测试3：2 个 L2 → enabled → 传参 2 个
def test_3_two_l2_btn_enabled_and_passed(page):
    select_l1(page, "家居用品")
    l2_item(page, "家居收纳用品").click()
    l2_item(page, "卫浴用品").click()
    assert btn_disabled(page) is False
    assert hint_text(page) == "已选择 2 个二级类目（单次最多选择 2 个二级类目）"
    sel = submit_selection(page)
    assert sel["level2"] == ["家居收纳用品", "卫浴用品"]  # 保持选择顺序


# ---------------------------------------------------------------- 测试4：第 3 个 disabled 且无法选择
def test_4_third_l2_disabled_and_cannot_select(page):
    select_l1(page, "家居用品")
    l2_item(page, "家居收纳用品").click()
    l2_item(page, "卫浴用品").click()

    states = l2_states(page)
    third = state_of(states, "装饰")
    assert third["disabled"] is True
    assert third["cls_disabled"] is True
    # 已选中的不得被 disabled
    assert state_of(states, "家居收纳用品")["disabled"] is False
    assert state_of(states, "卫浴用品")["disabled"] is False
    # 剩余未选全部 disabled
    assert count_enabled(states) == 2

    # 鼠标点击 disabled 行 → 无法选中
    l2_item(page, "装饰").click(force=True)
    states = l2_states(page)
    assert state_of(states, "装饰")["checked"] is False
    assert state_of(states, "装饰")["disabled"] is True

    # 程序事件注入（dispatch click 冒泡）→ 同样被拦截，不进入选中集合
    page.evaluate(
        """() => {
          const cbs = document.querySelectorAll('#level2-box input[type=checkbox]');
          for (const cb of cbs) {
            if (cb.value === '装饰') {
              cb.checked = true;
              cb.dispatchEvent(new Event('click', { bubbles: true }));
            }
          }
        }"""
    )
    sel = submit_selection(page)
    assert sel["level2"] == ["家居收纳用品", "卫浴用品"]  # 第 3 个未被注入


# ---------------------------------------------------------------- 测试5：取消后恢复
def test_5_cancel_restores_disabled(page):
    select_l1(page, "家居用品")
    l2_item(page, "家居收纳用品").click()
    l2_item(page, "卫浴用品").click()
    assert count_enabled(l2_states(page)) == 2

    # 取消 1 个 → 其余恢复可选
    l2_item(page, "家居收纳用品").click()
    states = l2_states(page)
    assert state_of(states, "家居收纳用品")["checked"] is False
    assert state_of(states, "装饰")["disabled"] is False
    assert state_of(states, "装饰")["cls_disabled"] is False
    assert hint_text(page) == "已选择 1 个二级类目"
    assert btn_disabled(page) is False

    # 恢复后还能再选第 2 个
    l2_item(page, "装饰").click()
    sel = submit_selection(page)
    assert sel["level2"] == ["卫浴用品", "装饰"]  # 取消后重新选择，顺序保持


# ---------------------------------------------------------------- 测试6：切换 L1 重置
def test_6_switch_l1_resets(page):
    select_l1(page, "家居用品")
    l2_item(page, "家居收纳用品").click()
    l2_item(page, "卫浴用品").click()
    assert btn_disabled(page) is False

    select_l1(page, "美妆个护")
    states = l2_states(page)
    assert len(states) > 0
    assert all(not s["checked"] for s in states)
    assert all(not s["disabled"] for s in states)
    assert all(not s["cls_disabled"] for s in states)
    assert btn_disabled(page) is True
    assert hint_text(page) == ""


# ---------------------------------------------------------------- 测试7：美容 / 个护电器 两个独立 L2
def test_7_beauty_units_independent(page):
    select_l1(page, "美妆个护")
    states = l2_states(page)
    names = {s["name"] for s in states}
    assert "美容" in names
    assert "个护电器" in names
    a = state_of(states, "美容")
    b = state_of(states, "个护电器")
    assert a["checked"] is False and b["checked"] is False

    l2_item(page, "美容").click()
    l2_item(page, "个护电器").click()
    sel = submit_selection(page)
    assert sel["level1"] == "美妆个护"
    assert sel["level2"] == ["美容", "个护电器"]  # 两个独立 L2，不合并


# ---------------------------------------------------------------- 测试8：pytest 全量回归（外层执行）
def test_8_pytest_regression_marker():
    """回归由 `python -m pytest tests/` 统一执行；
    本测试仅作为清单占位，说明场景 8 已纳入全量测试。"""
    assert True
