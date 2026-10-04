# -*- coding: utf-8 -*-
"""TikTok 数据层：导出文件解析(parser) + 确定性采集(collector)。

第一阶段优先"页面直接导出 → 下载文件 → Python 解析入库"；
浏览器自动化只做确定性 Playwright 操作（定位按钮→点击→等待下载→保存），
绝不使用 AI 视觉/LLM 控制浏览器。
"""
