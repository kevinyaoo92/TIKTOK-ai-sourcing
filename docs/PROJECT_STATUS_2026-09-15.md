# 泰国 TikTok Shop AI 选品数据系统 — 项目阶段记录

- 项目名称：泰国 TikTok Shop AI 选品数据系统
- 保存日期：2026-09-15
- 状态：开发中（阶段一 ~ 阶段五已完成，进入正式采集阶段）

> 保存要求：以后所有开发和执行必须基于以下状态继续，不重新设计架构，不重复测试已经通过的模块。

---

## 一、类目体系建设（已完成）

1. 官方文件（唯一类目来源，禁止人工维护第二份类目文件）：
   `C:\Users\Administrator\Desktop\泰国TK官方类目.txt`
2. 解析结果：
   - L1 一级类目：30 个
   - L2 二级类目：219 个
3. 已建立：
   - `app/category_loader.py`（官方 TXT 解析，`load_catalog()`）
   - `scripts/collect/build_collection_tasks.py`
   - `scripts/collect/build_final_collection_tasks.py`（读官方 TXT + 页面映射，生成 219 个任务）
   - 功能：官方 TXT 作为唯一类目来源、自动生成采集任务

## 二、TikTok 页面映射体系（已完成）

- 文件：`data/category_mapping/tiktok_th_category_page_mapping.json`
- 结果：
  - L1 验证：30/30 成功
  - L2 验证：MATCH 197 / ALIAS 18 / FAILED 4
- 正式执行规则：
  - 官方名称用于数据分类和保存
  - 页面名称用于 TikTok 页面点击
- 已处理：页面别名映射、页面名称差异
- 美妆个护特殊情况：官方一级类目「美妆个护」下二级类目「美容」「个护电器」两者保留为两个独立任务（不拆分、不去重）
- 报告：`data/category_mapping/tiktok_th_category_page_mapping_report.md`

## 三、Windows 独立采集系统（已完成）

- 文件：`scripts/collect/run_all_collection.py`
- 功能：
  - 脱离 Coze 运行、Windows 本机执行
  - 使用 Chrome 持久化登录态
  - 自动采集 TikTok 后台数据
  - 单任务失败不影响其他任务
  - 支持断点恢复（已成功任务不重复采集）
- 任务清单：`data/_tasks/final_collection_tasks.json`（219 个任务 = 215 执行 + 4 SKIPPED）
- 采集核心：`scripts/collect/tiktok_page_top20.py`（禁止修改核心逻辑）

## 四、数据保存体系（已完成）

- 原始数据：`data\_batches\<批次>\<一级类目>\<二级类目>\monthly\<TXT采集文件>`
- 标准化数据：`data\processed\TH\<一级类目>\<二级类目>\monthly\<*.json>`
- 数据库：`data\tiktok_market.db`（独立于 opportunity.db）
  - `category`：类目映射信息
  - `keyword_metric`：六个指标数据（搜索量/商品点击数/SKU销售指数/在售商品/CTR指数/CTOR评分）
  - `collection_batch`：批次记录

## 五、采集测试结果（已完成）

- 测试范围：一级类目「家居用品」，7 个二级类目全部成功：
  - 家居收纳用品 / 卫浴用品 / 装饰 / 家庭护理用品 / 洗衣工具 / 节庆和派对用品 / 家居日用
- 采集周期：2026-08-01 ～ 2026-08-31
- 结果：7/7 SUCCESS
- 数据：每个指标 TOP20；卫浴用品实际 18 条，未补造（忠实保留页面实际数据）

## 六、数据库标准化验证（已完成）

- 执行：`python scripts/collect/normalize_top20_data.py --txt-root data\_batches\20260915_214114 --db data\tiktok_market.db`
- 结果：文件 7 / 成功 7 / 失败 0 / 入库 828 行
- 说明：理论 840 行，由于卫浴用品实际 18 条，减少 12 行，符合预期
- 查询接口：`scripts/query/query_market_data.py`（top20 / keyword / history）

## 七、当前项目阶段

**当前已完成：**
- ✅ 官方类目体系（30 L1 / 219 L2）
- ✅ 页面映射体系（197 MATCH / 18 ALIAS / 4 FAILED）
- ✅ Windows 自动采集程序（断点恢复）
- ✅ 数据保存体系（批次 TXT / 标准 JSON / SQLite）
- ✅ TXT → 数据库标准化
- ✅ 查询接口
- ✅ 家居用品一级类目真实采集验证（7/7 SUCCESS）

**当前未完成：**
1. 215 个有效 L2 全量采集
2. 全量数据进入数据库
3. AI 选品分析模型
4. 1688 商品匹配
5. 产品评分系统
6. 前端展示系统

**下一阶段目标：**
1. 使用 Windows 执行正式采集：215 个有效 L2 任务（`python scripts/collect/run_all_collection.py`）
2. 采集完成后：TXT → `normalize_top20_data.py` → `tiktok_market.db`
3. 形成完整泰国 TikTok 市场关键词数据库
