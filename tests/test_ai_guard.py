# -*- coding: utf-8 -*-
"""AI 模块守卫测试：AI 默认关闭，任何语义调用都必须被明确拒绝。"""
from __future__ import annotations

import pytest

from app.ai import deepseek
from app.ai.deepseek import ALLOWED_TASKS, DeepSeekDisabledError


def test_disabled_by_default(monkeypatch):
    """AI_ENABLED=false 或缺少 key 时，调用必须抛 DeepSeekDisabledError（不允许悄悄调用）。"""
    monkeypatch.setattr(deepseek.config, "AI_ENABLED", False)
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", "")
    with pytest.raises(DeepSeekDisabledError):
        deepseek.chat_completion([{"role": "user", "content": "hi"}])

    monkeypatch.setattr(deepseek.config, "AI_ENABLED", True)   # 开了但没 key
    with pytest.raises(DeepSeekDisabledError):
        deepseek.analyze_keywords_batch(["พวงกุญแจ"])


def test_task_whitelist():
    """AI 只能执行本阶段白名单内任务（阶段=批量语义解析 + 机会分析；防范围蔓延）。"""
    assert ALLOWED_TASKS == ("analyze_keywords_batch", "analyze_opportunity")
    with pytest.raises(ValueError):
        deepseek._check_task("write_ads_copy")   # 不在白名单 → 拒绝
    # 白名单内任务可以通过
    deepseek._check_task("analyze_opportunity")
    deepseek._check_task("analyze_keywords_batch")


def test_batch_size_guard():
    """单批超过 30 词必须被拒绝（产品硬上限 30）。"""
    words = [f"w{i}" for i in range(31)]
    with pytest.raises(ValueError):
        deepseek.analyze_keywords_batch(words)
