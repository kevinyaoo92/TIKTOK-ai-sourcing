# Frontharbor 开发进度 vs 产品经理执行大纲 —— 对照汇报

生成时间：2026-10-05
汇报对象：外部专家
汇报目的：对照大纲，说明已完成、超纲、遗留三部分，供专家研判

---

## 0. 项目背景

- 产品：Frontharbor — TikTok 泰国站选品 SaaS
- 核心价值链：TikTok 市场需求 → AI 识别商品机会 → 生成 1688 搜索词 → 用户找货 → 妙手 ERP 采集
- 技术定位（产品框架文件）：Web SaaS + Chrome 浏览器扩展 + 云端 API 三部分协同
- 服务器：腾讯云 CVM 118.89.85.175，Ubuntu 22.04，2核2G/5Mbps
- 代码仓库：github.com/kevinyaoo92/TIKTOK-ai-sourcing
- 最新提交：09f5913

---

## 1. 大纲要求 vs 实际执行 —— 三分类总表

### 1.1 大纲要求且已完成

| 大纲项 | 阶段 | 状态 | 证据 |
|---|---|---|---|
| 确定云端架构 | Phase 1 | 完成 | CVM + systemd frontharbor.service，后端 0.0.0.0:8123 |
| 数据库 | Phase 1 | 完成 | data/tiktok_market.db 已上传服务器 |
| TikTok 数据 | Phase 1 | 完成 | keyword_metric 28014 行，category 182 行 |
| AI 选品 | Phase 1 | 完成 | Python AI 链路保留，opportunity_analysis 2493 条 |
| 商品机会 | Phase 1 | 完成 | 覆盖 161 个 L2，等级分布 S42/A170/B669/C1612 |
| 1688 搜索词 | Phase 1 | 完成 | 前端已输出 search_terms_1688 |
| 迁移现有核心业务逻辑 | Phase 1 | 完成 | server.py 保留原 Python 逻辑，本地与云端结果一致 |
| 前端核心流程 | Phase 1 | 完成 | 类目→机会→详情→1688搜索词→找货 |
| 免费额度 | Phase 6（提前） | 完成 | find 10 / collect 5，独立计数 |
| Nginx 反代 | Phase 1 | 完成 | 80 → 8123 |
| 服务器端额度控制 | Phase 6（提前） | 完成 | user_usage.check_and_decrement 在服务端执行 |
| 付费权限由服务器判断 | Phase 6（提前） | 部分完成 | 额度判断在服务器；支付未做 |

### 1.2 大纲未要求，但实际做了（超纲）

| 超纲项 | 大纲对应 | 实际做法 | 风险等级 |
|---|---|---|---|
| 真实 1688 找货 | Phase 1 只要求"占位 + 记录点击 + 申请体验" | 用 Playwright + CDP 9222 驱动本地 Chrome 真实访问 1688，返回真实货源 | 高 |
| 妙手采集 | Phase 5 才做 | 本地 miaoshou_collector.py 已跑通闭环，前端按钮可调用 | 中 |
| 跳过 Chrome Extension POC | Phase 3 要求先做扩展 POC | 完全未做扩展，直接用服务器侧 Playwright | 高 |
| 用户系统用匿名 UUID | Phase 1 要求"登录" | 匿名 UUID + localStorage + Cookie 双存，无登录注册 | 中 |
| 额度绑 UUID | Phase 6 明确要求"绑定 user_id，不能绑定设备" | 当前绑 UUID（设备级） | 高 |
| 找货成功后自动刷新额度 | 大纲未涉及 | 前端已实现 | 低 |

### 1.3 大纲要求但未做（遗留）

| 未做项 | 所属阶段 | 影响 |
|---|---|---|
| 登录/注册 | Phase 1 | 无法绑定 user_id，额度绑设备 |
| 找货"占位"设计 | Phase 1 | 已用真实找货替代，未留"申请体验"入口 |
| 点击埋点 | Phase 2 | 无法统计用户行为 |
| 机会点击统计 | Phase 2 | 无法知道哪些机会被关注 |
| 找货点击率 | Phase 2 | 无法验证需求强度 |
| 留联系方式 | Phase 2 | 无法收集意向用户 |
| 付费意愿验证 | Phase 2 | 无付费入口，无转化数据 |
| Chrome Extension POC | Phase 3 | 执行层未按大纲方向走 |
| 扩展读 1688 三字段（标题/链接/价格） | Phase 3 | POC 未做 |
| 扩展连续成功 3 次验证 | Phase 3 | POC PASS 标准未验证 |
| 订阅 | Phase 6 | 未做 |
| 支付 | Phase 6 | 未做 |
| 插件权限校验 | Phase 6 | 未做（无插件） |
| 数据统计后台 | Phase 6 | 未做 |
| Chrome Web Store 发布 | Phase 6 | 未做 |
| 域名解析 | Phase 1 | 等 ICP 备案 |
| HTTPS | Phase 1 | 等 ICP 备案 |

---

## 2. 已完成部分详述

### 2.1 云端部署

- 服务器：腾讯云 CVM，实例 ID ins-2zuo8hib，IP 118.89.85.175，上海五区，2核2G/5Mbps
- 服务：frontharbor.service（systemd，自动重启）
- 后端监听：0.0.0.0:8123
- Nginx：80 端口反代到 8123，已启用 HTTP Basic Auth（用户名 front）
- 外网访问：http://118.89.85.175（带密码保护）

### 2.2 数据层

- 主库：data/tiktok_market.db，active_period = 2026-09
- 表与行数：
  - keyword_metric: 28014 行
  - opportunity_analysis: 2493 条
  - category: 182 行
  - collection_batch: 182 条
  - ai_analysis_log: 2512 行
  - app_config: 1 行
  - user_usage: 已建表
- 覆盖：161 个 L2 有数据
- 等级分布：S 42 / A 170 / B 669 / C 1612
- AI 语义失败：0

### 2.3 前端核心流程

- 类目选择 → 机会列表 → 机会详情 → 1688 搜索词 → 找货弹窗
- 数据周期动态读取后端 active_period，2026-09 已切换
- 顶部额度条：显示"找货剩余 X/10 · 采集剩余 Y/5"

### 2.4 用户系统 + 免费额度

- 匿名 UUID：localStorage 优先 + Cookie 兜底，365 天
- 表结构：user_usage(uuid, action, count, created_at, updated_at)，主键 (uuid, action)
- 额度定义：QUOTA = {"find": 10, "collect": 5}
- 扣减：POST /api/find_suppliers 和 POST /api/miaoshou_collect 调 check_and_decrement
- 查询：GET /api/usage?uuid=...
- 单元测试：find 第 11 次拒绝，collect 第 6 次拒绝
- 前端自动刷新：找货弹窗关闭后额度 10→9，已实测
- 额度耗尽提示："免费找货次数已用完（10/10）。升级付费后可继续使用"

### 2.5 工程流程

- Git 仓库已配置，最新提交 09f5913
- 跨会话快照：_state/SESSION_STATE.md + make_session_report.py
- 状态文件：CURRENT_TASK.md、OPEN_ISSUES.md、PRODUCT_ROADMAP.md、PHASE1_SPEC.md、WORK_ERRORS.md
- AI 职责清单：已写入快照顶部，规定任务结束必须主动提醒收尾

---

## 3. 超纲部分详述

### 3.1 真实 1688 找货（最严重超纲）

大纲要求：Phase 1 的"找1688货源"只做入口，可以记录点击、显示"功能开发中/申请体验"，目的是验证用户是否真的需要这个功能。

实际做法：
- 后端 app/analysis/supplier_finder.py 使用 Playwright + CDP 9222 驱动本地 Chrome
- 使用 .chrome_1688 浏览器 profile 中的用户登录态
- 真实访问 1688，搜索关键词，读取商品标题、价格、SKU、库存、店铺信息
- 应用 SKU 规则（8 项上限、库存阈值、标题匹配）筛选商品
- 前端弹窗返回真实货源（已实测返回 3 个货源）

风险：
1. 并发问题：多用户同时找货，服务器需启动多个 Chrome 实例，2核2G 无法支撑
2. 平台风控：服务器出口 IP 集中访问 1688，容易被封
3. 账号边界：用户登录态在服务器浏览器里，与产品框架"云端不保存登录态"原则冲突
4. 合规风险：正式收费前未确认 1688 数据使用合规
5. 验证缺失：大纲要求的"点击率验证"未做，无法判断需求强度

### 3.2 妙手采集提前做（Phase 5 提前）

大纲要求：Phase 5 才做妙手，且要等 1688 找货稳定后再做，"不要 1688 和妙手同时开发"。

实际做法：
- app/analysis/miaoshou_collector.py 已实现 open_login_page / check_login / collect_to_miaoshou
- 后端路由 /api/miaoshou_collect、/api/miaoshou_check_login、/api/miaoshou_open_login
- 前端按钮：未登录时禁用，登录后可用，采集成功按钮变绿
- 本地已跑通闭环（3/3 成功）

风险：
1. 顺序提前，与大纲"1688 稳定后再做妙手"冲突
2. 同样是 Playwright 驱动，将来要迁移到扩展
3. 增加当前阶段复杂度，分散验证精力

### 3.3 跳过 Chrome Extension POC（Phase 3 完全跳过）

大纲要求：Phase 3 做 Chrome Extension POC，只验证一条链路：网站 → 扩展 → 用户自己的 1688 → 搜索固定关键词 → 读取一个商品 → 返回网站。POC PASS 标准：能进入 1688 搜索结果页、找到至少 1 个真实商品、成功读取标题/链接/价格三字段、数据成功发到后端、连续成功 3 次。

实际做法：完全未做扩展，直接用服务器侧 Playwright 替代。

风险：
1. 未验证扩展通道是否可行（浏览器扩展与 CDP 在权限、注入方式上有差异）
2. 执行层未来必须重写
3. 用户侧部署形态未定（扩展 vs 本地 Agent）

### 3.4 用户系统用匿名 UUID（偏离大纲）

大纲要求：Phase 1 前端核心流程第一步是"登录"；Phase 6 明确"免费额度必须绑定 user_id，不能绑定设备"。

实际做法：匿名 UUID + localStorage + Cookie，无登录注册。

风险：
1. 额度绑设备，清缓存可绕过（当前接受）
2. 未来上线账号体系时，匿名数据需迁移方案
3. 无法做用户级别的行为统计

### 3.5 额度绑 UUID 而非 user_id（Phase 6 明确冲突）

大纲要求：免费额度必须绑定 user_id，不能绑定设备。

实际做法：user_usage 表以 uuid 为键。

风险：Phase 6 商业化前必须迁移，涉及数据合并。

---

## 4. 未做/遗留部分详述

### 4.1 Phase 2「验证产品价值」几乎空白（最关键缺失）

大纲明确 Phase 2 要观察：
- 用户是否使用 AI 选品
- 哪些机会被点击
- 「找1688货源」点击率
- 用户是否愿意留下联系方式/申请体验
- 用户是否愿意付费

实际状态：以上五项均无埋点、无统计、无入口。

后果：无法判断产品价值，无法决定是否继续 Phase 3。

### 4.2 Phase 3 Chrome Extension POC 未做

- 未做扩展
- 未验证"扩展读 1688 三字段"
- 未验证"连续成功 3 次"
- POC PASS 标准未达成

### 4.3 Phase 6 商业化未做

- 无订阅
- 无支付
- 无插件权限校验
- 无数据统计后台
- 无 Chrome Web Store 发布

### 4.4 备案相关（阻塞）

- 域名解析：等 ICP 备案
- HTTPS：等 ICP 备案
- ICP 备案：订单号 30179108639935234，腾讯云初审 + 陕西管局审核中（7-10 工作日）
- 待办：接 010 电话、幕布拍照、管局审核

### 4.5 项目卫生

- C 盘空壳 tiktok-audit 和云盘副本待处理
- opportunity.html.bak_* 20+ 个备份文件待清理
- Git 临时脚本清理

---

## 5. 技术架构现状（供专家判断）

### 5.1 当前架构

Frontharbor Cloud (CVM 118.89.85.175)
- frontend/server.py (8123)
  - /api/analyze
  - /api/find_suppliers
  - /api/usage
  - /api/miaoshou_collect
- app/user_usage.py
- app/analysis/supplier_finder.py ← Playwright + CDP 9222
- app/analysis/miaoshou_collector.py ← Playwright
- data/tiktok_market.db

服务器侧 Playwright → 服务器上的 Chrome (.chrome_1688) → 1688 / 妙手 ERP

### 5.2 大纲期望的架构

Frontharbor Cloud
- 业务逻辑 / 数据 / AI / 用户 / 权限 / 订阅 / 任务 / 结果

HTTPS / API 下发
Frontharbor Extension
- 用户自己的 Chrome
- 用户自己的 1688 / 妙手登录态
- 本地执行

### 5.3 差距

| 维度 | 现状 | 大纲要求 | 差距 |
|---|---|---|---|
| 执行层位置 | 服务器 | 用户浏览器 | 全部重写 |
| 登录态 | 服务器浏览器 profile | 用户本地浏览器 | 全部重写 |
| 用户识别 | 匿名 UUID | user_id | 需迁移 |
| 任务分发 | 服务器直接执行 | API → 扩展 → 回传 | 需重写 |
| 额度控制 | 服务器侧（已符合） | 服务器侧 | 已对齐 |
| 核心业务逻辑 | Python | 云端保留 | 已对齐 |

---

## 6. 需要专家研判的问题

1. 是否推翻现有 Playwright 找货？
   - 选项 A：保留为内部验证工具，用户侧改回占位 + 申请体验
   - 选项 B：彻底删除，从零做扩展
   - 选项 C：保留并双轨（服务器执行 + 扩展执行）

2. 匿名 UUID 到 user_id 的迁移方案？
   - 何时引入账号体系
   - 匿名数据如何挂账
   - 是否允许"清缓存绕过额度"

3. Phase 2 验证指标的具体设计？
   - 需要埋哪些点
   - 观察多长时间
   - 判定阈值是多少

4. 现有业务层代码能否直接迁移到扩展？
   - SKU 规则、评分体系、AI 剔除逻辑，是否能原样搬到扩展
   - 还是需要重写

5. 合规问题的优先级？
   - ICP 备案是硬前提
   - 1688 数据合规何时确认
   - TikTok 数据转卖第三方是否允许

6. Phase 3 扩展 POC 的技术选型？
   - Manifest V3
   - content script 注入方式
   - 与后端 API 的鉴权方式

---

## 7. 一句话总结

Phase 1 技术交付超纲（真实找货 + 妙手 + 额度），但 Phase 2 商业验证空白（无埋点、无转化路径），执行层方向与大纲（Chrome 扩展）不一致，Phase 3 POC 未做。核心业务逻辑（SKU 规则、评分体系、AI 剔除）已冻结，将来迁移扩展时可复用。

---

## 附录：关键文件路径

- 后端：frontend/server.py
- 额度逻辑：app/user_usage.py
- 找货：app/analysis/supplier_finder.py
- 妙手：app/analysis/miaoshou_collector.py
- 前端：frontend/static/opportunity.html
- 主库：data/tiktok_market.db
- 状态文件：_state/CURRENT_TASK.md、_state/OPEN_ISSUES.md、_state/PHASE1_SPEC.md、_state/PRODUCT_ROADMAP.md
- 快照：_state/SESSION_STATE.md
- 冻结版本：_state/supplier_finder.STABLE_*.py 等