# 分析方法完整 SOP（100% 从代码确认）

最后更新: 20261002_082142

## 一、入口

服务入口: app.analysis.analyze_service.analyze(country, level1, level2, period, top_n=20, with_ai=True, force_refresh=False)

分析管线: app.analysis.analyze_pipeline.run_analysis(...)

调用者:
- 前端: POST /api/analyze（用户手动点一个 L2 触发）
- 临时脚本: 项目根目录 20+ 个 .py
- **无正式批量脚本**（下次要新建 run_all_analysis.py）

## 二、完整数据流（从代码逐行确认）

### 步骤 1: 缓存检查（analyze_service L356-362）
- 若 force_refresh=False，先读 opportunity_analysis 表
- 命中缓存直接返回，不再算
- 批量跑必须传 force_refresh=True

### 步骤 2: 读原始数据
- 从 keyword_metric 表读该 country+level1+level2+period 的所有行

### 步骤 3: Stage 0 清洗（pipeline L105-128）
删除规则（三条，任一命中即删）:
1. 关键字段任一为空: 搜索量 / SKU销售指数 / CTOR评分 / 在售商品
2. SKU销售指数=0 且 CTOR评分=0
3. keyword 重复（同一 L2 内）

### 步骤 4: Stage 1 非商品过滤（pipeline L140-151）
- 用 screening.NonProductFilter 分类
- 规则库: NONPRODUCT_PATTERN_RULES（促销词、价格词、泰语促销词等）
- 加上 AI 反向写入的额外黑名单（extra_blacklist 参数）
- 命中即剔除

### 步骤 5: Stage 2 样本量判定（pipeline L372-398）★ 关键
- cohort = Stage 1 保留的行
- **cohort < 3 → 直接返回 empty，不产出任何机会**
- **不使用 L1 fallback**（代码注释明确说"fallback 已禁用"）

**结论: 有效关键词 < 3 条 → 判 empty**

### 步骤 6: Stage 2 百分位（pipeline L155-175）
- 在该 L2 内计算 5 个指标的百分位:
  - demand_pct = 搜索量百分位
  - intent_pct = CTOR评分百分位
  - sales_pct = SKU销售指数百分位
  - competition_pct = 在售商品百分位
  - click_pct = CTR指数百分位（不进评分，只展示）
- 算法: 标准中位秩 (below + 0.5*ties)/n

### 步骤 7: Stage 3 机会分（pipeline L181-206）
公式:
- Demand = demand_pct
- Conversion = 0.70 * intent_pct + 0.30 * sales_pct
- Competition = 1 - competition_pct
- OpportunityScore = 100 * (0.40 * Demand + 0.35 * Conversion + 0.25 * Competition)
- 四指标任一为 None → 该词机会分=None，不参与排序

### 步骤 8: Stage 4 排序 + Top N
- 按机会分降序
- 取 Top N（默认 20）

### 步骤 9: Stage 5 等级（pipeline L212-234）
- 正常样本（cohort >= 30）: S>=85 / A>=75 / B>=65 / C<65
- 小样本（cohort < 30）: S>=75 / A>=65 / B>=55 / C<55（阈值下调 10）

### 步骤 10: AI 语义分析（analyze_service L394-425）
对 Top N 每条调 DeepSeek，产出:
- normalized_product_name_cn → 中文名
- market_judgment / competition_judgment
- suitable_for_tiktok_th
- search_terms_1688（找货用）
- risks / confidence
- product_intent（若判"非商品" → 该机会被剔除，且关键词写入反向规则库）

AI 调用失败: 用占位文本，机会保留（不阻塞）

### 步骤 11: 写库
- 写 opportunity_analysis（每条机会一行）
- 写 ai_analysis_log（AI 调用日志）
- 写前先 _clear_l2_cache（清该 L2 旧缓存，避免残留）

## 三、空 L2 的处理逻辑（已确认）

**判据: Stage 1 后 cohort < 3**

- 返回 status=empty，reason=insufficient_candidates
- 不写 opportunity_analysis
- 报错信息: "该类目 <period> 仅有 N 个有效关键词，样本过少，暂不建议分析。"
- 前端显示"该类目样本量过少"

当前 2026-08 空着的 19 个 L2（keyword_metric 只有 6-18 行原始数据）:
二手手表、二手时尚配件、二手鞋靴、人文社科、儿童家具、儿童穆斯林服装、
婴儿安全防护用品、安防劳保用品、室外家具、家庭智能系统、服装布料、
水上运动设备、汽车灯、沙滩车/房车/游艇设备、烘焙用具、玉石、珍珠、
运动收藏品、非天然水晶

## 四、下次全量跑的正确方式（9 月数据）

第 1 步: 采集
  python scripts\collect\run_all_collection.py --mode fullrow --page-size 30 --source hot+rising --overwrite-all --period 2026-09

第 2 步: 批量分析（脚本待写）
  需要新建 scripts/analyze/run_all_analysis.py，要求:
  - 读 data/_tasks/final_collection_tasks.json 的 219 个任务
  - 逐个调 analyze_service.analyze(country="泰国", l1, l2, period="2026-09", top_n=20, with_ai=True, force_refresh=True)
  - 失败重试最多 2 次
  - 断点续跑（记录已完成 L2）
  - 跑完输出: 成功 N 个 / 空 M 个 / 失败 K 个
  - 空的（cohort<3）按同样逻辑判 empty，不硬撑

第 3 步: 切换显示周期
  UPDATE app_config SET value='2026-09' WHERE key='active_period'

## 五、当前已修复的坑

- analyze_pipeline.py L380 硬编码 "2026-08" 已改为 {period}（本次修复）

## 六、下次跑之前必须做的事

1. 备份 8 月库
2. 小范围试跑（--limit 3 或 3 个 L2）验证 period 正确
3. 确认 app_config.active_period 已是 2026-09
4. 正式全量跑
5. 跑完检查 opportunity_analysis 表的 period 分布是否为 2026-09
