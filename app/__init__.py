# -*- coding: utf-8 -*-
"""Thailand TikTok Opportunity 主包。

业务主线（任何功能不得偏离）：
TikTok市场数据 → 市场机会发现 → AI理解商品需求 → 1688供应链匹配
→ 商品筛选与排序 → 用户选择 → 用户进入自己的TikTok销售流程

第一阶段范围：TikTok 关键词数据获取/导入 → SQLite 保存历史
→ Python 可解释市场机会计算 → 输出机会列表。
AI(DeepSeek) 仅预留接口，默认不参与流水线。
"""
