# Thailand TikTok Opportunity

泰国 TikTok **市场机会发现**系统（第一阶段：关键词榜单 → SQLite → 可解释市场机会评分）。
终极形态为可产品化 Web SaaS 的「TikTok 市场机会发现 + 1688 供应链匹配」，当前只做第一阶段。

## 业务主线（产品铁律，任何功能不得偏离）

```
TikTok市场数据 → 市场机会发现 → AI理解商品需求 → 1688供应链匹配
→ 商品筛选与排序 → 用户选择 → 用户进入自己的TikTok销售流程
```

**禁止**做成：通用 AI 聊天机器人 / AI 写标题 / AI 生图 / 通用 TikTok 运营助手 / 泛电商 ERP / 单纯 1688 选品工具。

## 角色与 AI 边界

| 角色 | 职责 |
|---|---|
| 程序 (Python) | 浏览器导航/点击、数据获取、Excel/CSV 读取、清洗、入库、排序、数学与趋势计算、去重、价格/库存/SKU 筛选、数据库 |
| DeepSeek/AI | **只做语义**：泰语关键词理解、类目判断、聚类、需求理解、1688 中文搜索词生成、TikTok↔1688 语义匹配（第一阶段**默认关闭**） |
| 用户 | 查看机会与数据依据、做最终商业选择、进入自己的 TikTok 销售流程 |
| 产品老板 | 产品规则、数据源、参数、业务逻辑、后台管理 |

铁律：**能用 Python 解决的问题不调用 AI**；AI 绝不承担浏览器操作/表格解析/数学计算；
不把密钥写进代码（走 `.env`，见 `.env.example`）。

## 目录结构

```
app/
  config.py                全局配置：路径/默认口径/评分权重/可调规则（环境变量可覆盖）
  database.py              SQLite：keyword_data(原始历史只增不覆盖) + opportunity_data(分析结果)
  models/                  数据模型 dataclass + 列顺序常量（单一事实来源）
  tiktok/
    parser.py              导出文件解析（真实导出结构验证过：元信息行+表头+泰语关键词）
    collector.py           采集：import(解析入库) / browser(确定性Playwright导出)
  analysis/opportunity.py  市场机会评分（纯 Python，可解释，中文逐因素理由）
  analysis/screening.py    V1 初期筛选层（清洗→非商品过滤→通道→评分→TopN）
  ai/deepseek.py           DeepSeek 语义接口（AI Guard：默认禁用 + 任务白名单）
  ai/semantics.py          语义流水线（批量 20~50 词、断点续跑、重试、单条隔离）
scripts/
  collect_tiktok.py        关键词榜单 → SQLite
  analyze_opportunities.py 计算并输出机会列表
  run_screening_v1.py      V1 初期关键词筛选（候选池输出）
  analyze_semantics.py     AI 语义理解：关键词 → 商品需求理解 → 1688 中文搜索词
data/
  raw/tiktok/thailand/     原始导出原样保存（只读不改，文件名含导出时刻）
  processed/               处理产物（如机会 CSV，git 忽略）
database/opportunity.db    SQLite 机会库
tests/                     pytest（解析/评分/入库/AI守卫）
project/automation/tiktok/tiktok_keyword_export.py   已验证的 Playwright 导出自动化（无 AI）
poc/                       POC 探索遗留（不参与主链路）
```

## 数据来源与口径（第一阶段）

- 固定入口：`https://seller.tiktokshopglobalselling.com/`（泰国跨境后台；
  `seller.tiktok.com` 会路由美区，**不对**）。
- 页面路径：数据分析 → 商品卡 → 关键词榜单。
- 默认口径：**热门搜索关键词 / 月 / 时尚配件 / 泰国(TH)**。
  （卖家后台关键词榜单的月数据有滞后：当月只能取到前两个月，如 2026-09 → 2026-07。）
- 采集方式：页面「导出数据 → 下载」优先；浏览器操作全部为确定性 Playwright（定位按钮→点击→等待下载→保存），
  不用 AI 视觉/读 DOM 决策。登录/验证码由用户手动完成一次（Chrome 登录档案在 `.run/profile`），
  **不要把账号密码提供给任何人/写入代码**。

## 快速开始

```powershell
# 1) 依赖
pip install -r requirements.txt

# 2) 导入已导出的原始文件（默认扫描 data/raw/tiktok/thailand/）
python scripts/collect_tiktok.py
#    指定单个文件:  python scripts/collect_tiktok.py --file <路径.xlsx>
#    浏览器导出:    python scripts/collect_tiktok.py --mode browser   （需登录一次）

# 3) 计算市场机会并入库、展示 Top20
python scripts/analyze_opportunities.py
#    看某词为什么入选（完整中文理由）
python scripts/analyze_opportunities.py --explain พวงกุญแจ
#    导出 CSV / 清理重算
python scripts/analyze_opportunities.py --export-csv data/processed/opp_2026-07.csv --replace-run

# 4) 测试
python -m pytest tests/ -q
```

重复导入同一导出文件会被自然键去重自动跳过；**历史快照只增不覆盖**，为以后周→周、月→月趋势对比留数据。

## V1 初期关键词筛选层

数据流：`keyword_data → 数据清洗 → 非商品意图过滤 → 高需求主通道 → 高购买意图保护通道 → Opportunity Score → Top N candidate`

```powershell
python scripts/run_screening_v1.py                  # 默认: TH/时尚配件/热门/月/最新周期, Top80
python scripts/run_screening_v1.py --top-n 80 --replace-run
python scripts/run_screening_v1.py --no-store       # 预览不入库
```

- 全部阈值/权重/TopN 配置化（`app/config.py` 的 `V1_*`），可用环境变量覆盖。
- 计分：`demand_pct`=搜索量百分位（商品点击数仅展示，**不与搜索量重复计分**，ρ=0.975）；
  `supply_pct`=在售商品百分位；`opportunity_gap=demand_pct−supply_pct`（不用"在售<X"硬判断）；
  `purchase_intent=0.70×CTOR分位+0.30×SKU分位`；`score=0.40×demand+0.30×intent+0.30×gap`。
- 通道：搜索量分位<P25 → 低需求淘汰，但 CTOR 分位≥P75 可经**保护通道**放行；CTR 不进主评分；趋势不进 V1。
- 非商品过滤在评分前完成（先剔除再算分位参照系，避免促销词污染）：
  精确黑名单（config + 可选外部文件每行一词）+ 9 类模式规则（价格/优惠券/免费样品/COD/跨区比索/分期/新客/平台/数字开头）。
- 候选数量默认 **80 = V1 假设值**（未经历史回测，注释与输出均已标注）。
- 结果入库 `screening_v1`（每词记录 passed/channel/reject_reason/demand_pct/supply_pct/gap/intent/score/top_rank），每次运行一个 run_id 批次。

## AI 语义理解（第二阶段）

业务链：`screening_v1 TopN 候选 → DeepSeek 语义理解 → 商品分类/意图判定 → 1688中文搜索词`
（**只做这一环**；1688 实际搜索/ERP/前端/用户系统等后续阶段再开发。）

```
screening_v1（通过筛选且 top_rank ≤ AI_CANDIDATE_LIMIT，默认 80 = V1 假设）
  → 断点过滤（已 success 不重复调用；failed 达上限跳过）
  → 分批(每批≤30，默认 30)调 DeepSeek analyze_keywords_batch
  → 严格校验（intent_status↔is_product 一致 / PRODUCT 须 2~5 个 1688 词 / confidence 0~1）
  → ai_semantic_results（intent_status: PRODUCT|NON_PRODUCT|AMBIGUOUS + 全量可追溯）
```

```powershell
# 前置：AI_ENABLED=true + DEEPSEEK_API_KEY 配置在 .env
python scripts/analyze_semantics.py --dry-run     # 统计候选/待处理，不调用 AI
python scripts/analyze_semantics.py --status      # 已处理统计
python scripts/analyze_semantics.py               # 批量语义分析（断点续跑）
python scripts/analyze_semantics.py --ai-candidate-limit 80 --batch-size 30
python scripts/analyze_semantics.py --force-retry # 重试曾失败(达上限)的词
```

- **AI 只做语义**：泰语理解、商品需求/意图判断（PRODUCT/NON_PRODUCT/AMBIGUOUS）、分类、
  生成 2~5 个面向 1688 供应链的中文搜索词（覆盖材质/款式/用途/人群；不写营销文案/完整标题/编造品牌）。
  百分位/排名/机会分等数字**全部仍由 Python（screening_v1）负责**。
- **意图三元**：无法确定时为 AMBIGUOUS 而非强制判断；低 confidence 条目照常保留入库。
- **批量与节省**：每批最多 30（硬上限，默认 30）；成功词默认永不再调用；失败断点重试
  （attempts 上限默认 3，`--force-retry` 重置）；单条失败不影响同批；batch JSON 解析失败
  整批记 failed + error，**不写入伪造成功结果**。
- **可追溯**：ai_semantic_results 保存 原词+来源上下文、意图/分类/1688 词/置信度、模型、
  created_at/updated_at、status(success/failed)、attempts、error；(market, original_keyword) 唯一，
  success 行不被失败覆盖。

## 数据库

- `keyword_data`：每行 = 一个(周期×市场×类目×榜单类型×关键词)。保存：周期起止/粒度、关键词、搜索量、
  商品点击数、SKU销售指数、在售商品、平均价格(฿=THB，导出原值)、CTR指数、CTOR评分、名次、来源文件。
- `opportunity_data`：每行 = 一次机会计算结果（含各因素分、综合分、标签、中文理由 JSON、源数据快照），
  每次运行追加一批历史；`--replace-run` 可清理同基准周期的旧批次。
- `ai_semantic_results`（第二阶段）：每行 = 一个 (market, 关键词) 的 AI 语义缓存，含中文含义/商品大类/子类/
  1688 中文搜索词(JSON)/is_product/confidence/model/status/attempts/error/created_at/updated_at；
  `success` 行不被失败覆盖、默认不再调用 AI；`failed` 行带错误信息供断点重试。
- `screening_v1`：每行 = 一个关键词在 V1 筛选一次运行(run_id)中的决策记录（passed/channel/
  淘汰原因/demand_pct/supply_pct/opportunity_gap/ctor_pct/sku_pct/purchase_intent/
  opportunity_score/top_rank），供候选池与后续 1688/AI 阶段读取。

```
sqlite3 database/opportunity.db
.tables
SELECT keyword, opportunity_score, label FROM opportunity_data
 WHERE category='时尚配件' ORDER BY opportunity_score DESC LIMIT 20;
```

## 市场机会评分（第一版：可解释，不承诺"爆款"）

综合分 = 各因素分(0~100) 加权，权重集中在 `app/config.py::SCORE_WEIGHTS`，产品老板可调。
某因素数据不足时剔除该因素、对剩余权重归一（并在理由中说明）。每条结果都给出逐因素中文理由 + 综合结论，
回答"为什么该词被认为是市场机会"。

| 因素 | 含义 | 计算（确定性 Python） |
|---|---|---|
| 需求 demand | 搜索需求强度 | 搜索量在同期同类目词中的分位（0~100） |
| 增长 growth | 最近 momentum | 与上一周期(月比月/周比周)环比：`50 + 增长率×100`，截断 0~100；新上榜给中性偏高默认 65 |
| 点击承接 click | 搜索后点进商品的意愿 | 商品点击数/搜索量 的分位 |
| 销售信号 sales | 成交信号 | SKU销售指数 分位 |
| 竞争 competition | 竞争激烈度 | 在售商品数越少分越高（分位反转） |
| 价格 price | 目标价格带 | 平均价落入 `PRICE_BAND_THB` 区间得满分，越远越低；默认未配置→不计分 |

等级标签：≥80 高机会 / ≥60 中机会 / <60 低机会（`config.LABEL_BANDS` 可调）。

> 第一阶段只有月快照：需求/点击/销售/竞争/价格照常计分，增长因素显示"暂无环比/新上榜"。
> 等积累 ≥2 期同粒度快照后，环比自动生效（历史数据已按周期保留）。

### 周与月数据综合（规划，规则可调）

产品最终会自动综合两口径：月判断长期需求/稳定性，周判断近期 momentum。示例规则（已在
`config.WEEK_MONTH_LABELS` 预留为可调整数据结构）：

- 月高 + 周涨 → 稳定热门
- 月一般 + 周明显上涨 → 新兴机会
- 周突然高 + 月很弱 → 短期异常/观察中

## 当前边界（第一、二阶段明确不做）

1688 实际搜索 / 妙手ERP / 用户登录 / 支付 / 会员 / 完整前端 / 复杂后台 / AI 聊天 / 多国家 / 多平台 /
Redis/Celery/Docker/K8s/微服务——均等后续阶段指令再开发。
已完成：TikTok 关键词获取与入库 → 市场机会评分 → AI 语义理解（商品需求/分类/1688 搜索词）。
代码为未来扩展预留：Web API 对应 app/ 模块化边界、AI 任务白名单、周月标签规则表、多市场字段。
**AI 语义结果目前只入库供查询，尚未与机会评分/用户界面联动（等后续阶段指令）。**

## 已知口径说明（诚实记录）

- 导出中的"搜索量"为平台提供的数值（可能含脱敏/指数化），本系统**原样保存、不做换算推断**；
  环比比较基于同一指标自身的前后期数值。
- 平均价格单位为导出标注的 ฿（泰铢），原值保存；原始文件永不改动、不删字段、不补缺。
