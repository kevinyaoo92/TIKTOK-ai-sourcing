# 架构冻结声明 2026-10-05

## 背景

服务器侧 Playwright 找货 / 妙手采集已跑通技术闭环。
但战略决策：停止证明技术，开始证明用户需求。
服务器 Playwright 保留为内部验证资产，不再作为正式架构继续投资。

## 冻结清单

| 模块 | 路径 | 状态 |
|---|---|---|
| 1688 货源查找器 | app/analysis/supplier_finder.py | 保留，冻结，不继续扩展 |
| 妙手采集器 | app/analysis/miaoshou_collector.py | 保留，冻结，不继续扩展 |
| 服务器 Chrome（.chrome_1688 + CDP 9222） | 服务器本地 | 保留测试用途 |

## 定位

以上全部定位为 Internal Validation / Reference Implementation：
- 内部技术验证
- 开发测试工具
- 参照实现，不作为最终生产架构

## 明确不做

- 不继续扩展这两个模块的功能
- 不做多用户并发
- 不做服务器 Chrome 多实例
- 不为这两个模块添加新的生产依赖

## 后续正式执行层方向

Chrome Extension + 用户自己的 Chrome。

- 网站 → Extension → 用户自己的 1688 → 读取结果 → 返回网站
- 云端不保存用户 1688 / 妙手登录态
- 服务器负责业务逻辑、数据、AI、用户、权限、订阅、任务、结果
- 扩展负责浏览器环境、页面操作、用户登录态、本地执行

详见产品框架：Web SaaS + Chrome 扩展 + 云端 API 三部分协同。

## 参考

- 战略决策：PRODUCT_ROADMAP.md（顶部）
- 进度 vs 大纲对照：REPORT_vs_PM_OUTLINE.md
- 当前任务：CURRENT_TASK.md