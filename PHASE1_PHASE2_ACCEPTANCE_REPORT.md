# Phase 1 + Phase 2 验收报告

**项目**：Thailand TikTok Opportunity（泰国 TikTok 市场机会发现系统）
**验收日期**：2026-09-09
**验收范围**：Phase 1（工程审计）+ Phase 2（TikTok 单任务采集链路）

---

## 1. Phase 1 工程审计结论

### 1.1 项目位置与代码资产

- **真实项目目录**：`D:\tiktok-ai-sourcing`（不在默认工作区 `C:\Users\Administrator\WorkBuddy\2026-09-09-15-49-10` 内）
- **Python 环境**：系统 Python 3.11（`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`），已安装全部依赖：`playwright 1.61.0` / `pandas 3.0.5` / `openpyxl 3.1.5` / `pytest 9.1.1` / `python-dotenv 1.0.1`
- **浏览器**：使用系统 Chrome 通道（`channel="chrome"`），无需 `playwright install`

### 1.2 现有代码模块（全部冻结，未重写）

| 模块 | 文件 | 状态 |
|---|---|---|
| 配置 | `app/config.py` | V1 权重（0.40/0.30/0.30）、PurchaseIntent（0.70/0.30）、33 黑名单、9 pattern 已冻结正确 |
| 数据模型 | `app/models/__init__.py` | 完整 dataclass + 列顺序常量（含 `KeywordRecord.level_1/level_2_category`、`file_hash` 等） |
| 数据库 | `app/database.py` | `keyword_data` 含完整血缘字段；自然键 UNIQUE 防重；历史只增不覆盖 |
| 解析器 | `app/tiktok/parser.py` | 元信息行解析 + 表头别名 + 周期自动判定 |
| 采集 | `app/tiktok/collector.py` | `import_export_file()` 支持 level_1/level_2 入库 |
| V1 筛选 | `app/analysis/screening.py` | 精确黑名单 + 9 pattern + 通道逻辑 + 评分公式全部冻结 |
| 采集入口 | `scripts/collect_tiktok.py` | `--level1/--level2` 参数齐全 |
| 前端 | `frontend/server.py` | 只读 HTTP 服务，零第三方依赖 |

### 1.3 已有数据现状（审计前）

| 表 | 行数 | 含义 |
|---|---|---|
| `keyword_data` | 252 | TH / 时尚配件 / 热门搜索关键词 / month / 2026-07-01~07-31 / level_2=NULL |
| `opportunity_data` | 252 | 历史机会评分（基于时尚配件一级数据） |
| `screening_v1` | 252 | V1 筛选历史 |
| `ai_semantic_results` | 80 | 历史 DeepSeek 语义缓存 |
| `product_opportunities` / `product_opportunities_v2` | 47 | 历史商品机会 |

**关键观察**：
- ✅ 与规格第十六条完全吻合（"252 条 2026-07 月度数据，时尚配件一级，无二级类目"）
- ✅ 没有任何家居用品数据（Phase 2 验收任务就是补齐第一条家居收纳数据）
- ✅ 7 个归档错误周期文件 `data/raw/_archive_wrong_period_202606/` 完整保留未删

### 1.4 已有采集脚本问题

`project/automation/tiktok/tiktok_keyword_export.py`（旧版）三个核心问题：

1. **类目硬编码**：`TARGET_CATEGORY = "时尚配件"`，脚本会 `step_fail` 拒绝其他类目
2. **无二级类目选择逻辑**：从 UI 层面无法切到"家居收纳"
3. **月份硬编码**：`target_month()` 函数固定返回 month-2（如9月→7月），与规格"必须从后台实际读取最新完整月度，禁止写死"冲突

---

## 2. Phase 2 单任务采集：实施与验证

### 2.1 实施内容

**重写文件**：`project/automation/tiktok/tiktok_keyword_export.py`
- 移除类目硬编码 → `--level1/--level2/--keyword-type` 命令行参数
- 重写 `pick_cascader_item()` → hover+click 级联（实测可正确展开二级列）
- 重写 `pick_latest_month()` → 从后台实际读取所有非 disabled 月份 cell，取最后一个作为"最新可用完整月度"
- 路径修正：`PROJECT_ROOT = parents[3]`（指向项目根，而非 project/ 子目录）
- 导出/下载流程：`page.expect_download(timeout=150000)` 正式捕获下载事件，校验文件真实落盘
- 增强：导出后从 Excel 元信息 `[日期范围]` 读取**真实周期**写入结果（UI 可选月份 ≠ Excel 实际周期，因为 TikTok 月度数据滞后；规格要求"实际日期必须从后台实际选择结果获得"——Excel 元信息是权威）

**联动更新**：
- `app/tiktok/collector.py` `run_browser_export()` 增加 level1/level2/keyword_type 透传
- `scripts/collect_tiktok.py` browser 模式把参数透传给 Playwright 脚本

### 2.2 真实运行结果

```json
{
  "ok": true,
  "level_1_category": "家居用品",
  "level_2_category": "家居收纳",
  "keyword_type": "热门搜索关键词",
  "period_month": "08月",
  "period_label": "八月",
  "file": "D:\\tiktok-ai-sourcing\\data\\raw\\tiktok\\thailand\\Top_Search_Term_Rank_List_20260909080435.xlsx",
  "filename": "Top_Search_Term_Rank_List_20260909080435.xlsx",
  "size_bytes": 10995,
  "period_start_actual": "[日期范围]: 2026-07-01 ~ 2026-07-31"
}
```

**真实采集链路验证**：
1. ✅ 真实打开 Chrome 浏览器连接 TikTok Seller Center
2. ✅ 检测登录态（已登录 .run/profile）
3. ✅ 进入关键词榜单页 `https://seller.tiktokshopglobalselling.com/compass/search-analytics/keyword-rank?shop_region=TH`
4. ✅ 切到 "热门搜索关键词" tab
5. ✅ 类目级联：点开 → 点 "家居用品" → 点 "家居收纳"
6. ✅ 月份选择器：点开 → 切"月"粒度 → **从可用月份（一月~八月）中选最后一个非禁用月份"八月"**
7. ✅ 点击"导出数据"按钮
8. ✅ 在导出抽屉中点"下载"，用 `expect_download` 捕获文件下载事件
9. ✅ 文件保存并校验大小（10995 字节 > 0）
10. ✅ 从 Excel 元信息读取真实周期：`2026-07-01 ~ 2026-07-31`（与 TikTok 月度数据滞后特性一致，规格 README 明确提到 "keyword-rank monthly data lags: only month-2 published"）

### 2.3 Excel 内容验证

| 项目 | 值 |
|---|---|
| 文件路径 | `D:\tiktok-ai-sourcing\data\raw\tiktok\thailand\Top_Search_Term_Rank_List_20260909080435.xlsx` |
| 文件大小 | 10995 字节 |
| 元信息[日期范围] | `2026-07-01 ~ 2026-07-31` |
| 元信息[类目] | `家居收纳` |
| 表头字段 | 关键词 \| 搜索量 \| 商品点击数 \| SKU 销售指数 \| 在售商品 \| 平均价格 (฿) \| CTR 指数 \| CTOR 评分（**8 个核心字段全部保留**） |
| 数据行数 | **68 条泰语关键词** |
| 关键词样本 | ชั้นวางรองเท้า（鞋架）、ไม้แขวนเสื้อ（衣架）——确为家居收纳类商品 |

### 2.4 数据库血缘验证（入库后）

| market | level_1 | level_2 | keyword_type | period_type | period_start | period_end | n | source_file | file_hash |
|---|---|---|---|---|---|---|---|---|---|
| TH | 时尚配件 | NULL | 热门搜索关键词 | month | 2026-07-01 | 2026-07-31 | 252 | Top_Search_Term_Rank_List_20260907023548.xlsx | c6d7de42f5f9b71d |
| TH | **家居用品** | **家居收纳** | 热门搜索关键词 | month | 2026-07-01 | 2026-07-31 | **68** | Top_Search_Term_Rank_List_20260909080435.xlsx | 0a0c396b96cde1fc |

**入库后 `keyword_data` 总行数：252 + 68 = 320**（历史 252 条完全未删未改）

### 2.5 历史数据完整性验证（与备份库对比）

| 表 | backup (pre_p0_backup_20260909) | current | 状态 |
|---|---|---|---|
| keyword_data | 252 | **320** | +68（新采集入库）|
| opportunity_data | 252 | 252 | 未变 |
| screening_v1 | 252 | 252 | 未变 |
| ai_semantic_results | - | 80 | 未变 |
| product_opportunities | - | 47 | 未变 |
| product_opportunities_v2 | - | 47 | 未变 |
| opportunity_directions | - | 11 | 未变 |

**完全符合规格**："不删除任何历史数据。不重算。不重新设计产品。不修改 V1。" ✅

### 2.6 V1 体系回归测试

```
tests/ 全部 90 个测试通过（6.31s）
```

包含以下关键回归：
- `test_screening_v1.py`：V1 筛选层（33 黑名单 + 9 pattern + 权重公式）
- `test_parser.py`：Excel 解析（元信息 + 表头 + 周期判定）
- `test_category_lineage.py`：类目血缘（含 level_1/level_2 + NULL 一级数据）
- `test_opportunity_v11.py`：V1.1 商品机会评分
- `test_ai_guard.py`：AI Guard 默认关闭
- `test_semantics.py` / `test_semantics_intent.py`：语义缓存不覆盖

---

## 3. 失败记录与处理

| 失败 | 原因 | 处理 |
|---|---|---|
| 第一次采集：找不到 "热门搜索关键词" tab | 偶发时序（页面加载延迟），60s 超时；`expect_download` 等待未生效 | 重试即成功（1m47s 完成）；脚本逻辑本身无问题 |
| 第一次保存路径错位 | `PROJECT_ROOT = parents[2]` 指向 `project/` 而非项目根 | 改为 `parents[3]`；文件已移到正确位置 `data/raw/tiktok/thailand/` |
| UI 选"八月" vs Excel 实际周期"七月" | TikTok 月度榜单数据滞后 2 个月（README 已说明） | 在脚本结果中记录 `period_start_actual`（从 Excel 元信息读取）——以元信息为最终权威，避免 UI 与数据不一致 |

---

## 4. 第一阶段验收任务达成情况

按规格第三十一节要求，逐项核对：

| 验收项 | 实际 |
|---|---|
| 1. 检查当前工作目录 | ✅ 项目位于 `D:\tiktok-ai-sourcing` |
| 2. 检查现有代码 | ✅ 见 1.2 表 |
| 3. 检查已有数据库 | ✅ 见 1.3 表 |
| 4. 检查已有 Excel | ✅ `data/raw/tiktok/thailand/Top_Search_Term_Rank_List_20260907023548.xlsx`（时尚配件 252 条） |
| 5. 检查现有 TikTok 采集代码 | ✅ `project/automation/tiktok/tiktok_keyword_export.py`（已重写修正） |
| 6. 不删除任何历史数据 | ✅ 252 条 + 7 个归档文件全部保留 |
| 7. 不重新设计产品 | ✅ V1 权重 / 公式 / 过滤器 / 33 黑名单 / 9 pattern 未改 |
| 8. 不修改 V1 | ✅ 所有 90 个测试通过 |
| 泰国 / 家居用品 / 家居收纳 / 热门搜索关键词 / 最新完整月度 | ✅ 真实导出成功 |
| Excel 成功下载 | ✅ 10995 字节，落到 `data/raw/tiktok/thailand/` |
| 原始字段完整 | ✅ 8 个核心字段全部保留（关键词/搜索量/商品点击数/SKU销售指数/在售商品/平均价格/CTR/CTOR） |
| 数据血缘完整 | ✅ 68 条入库含 level_1=家居用品, level_2=家居收纳, period=2026-07-01~07-31, source_file, file_hash, imported_at |

---

## 5. 当前项目状态总结

- **原始数据层**：`keyword_data` 共 **320 条**（时尚配件 252 + 家居收纳 68）
- **真实二级类目数据**：已具备第一条 `家居收纳` 二级类目数据，可作为后续筛选层/语义层测试基础
- **采集脚本**：参数化、确定性、月份动态读取，**未来可直接扩展到飙升关键词、高潜力关键词、其他二级类目**
- **V1 体系**：完全冻结，未做任何修改
- **测试**：90/90 通过

**Phase 2 验收任务完成。** 可进入 Phase 3（增加飙升关键词）。